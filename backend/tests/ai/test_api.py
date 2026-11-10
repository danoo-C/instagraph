"""AI endpoints (docs/ai-assistant.md A8.5) with the fake provider."""

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from instagraph.ai import providers
from instagraph.ai.providers.fake import FakeProvider

ON = {"ai_enabled": True, "ai_confirmed_notice": True, "ai_provider": "fake"}


@pytest.fixture
def fake() -> Iterator[FakeProvider]:
    provider = FakeProvider(
        script=[
            ("tool", "list_people", {}),
            ("text", "[P1] writes as much as you. [[msg:1]]"),
            ("usage", {"input_tokens": 10, "output_tokens": 5}),
        ]
    )
    providers.reset()
    providers.register(provider)
    yield provider
    providers.reset()


@pytest.fixture
def ai(client: TestClient, fake: FakeProvider) -> TestClient:
    assert client.put("/api/settings", json=ON).status_code == 200
    return client


def sse(resp: Any) -> list[dict[str, Any]]:
    events = []
    for line in resp.text.splitlines():
        if line.startswith("data:"):
            events.append(json.loads(line[5:].strip()))
    return events


def new(client: TestClient, **body: Any) -> dict[str, Any]:
    r = client.post("/api/ai/conversations", json={"chat_ids": [0]} | body)
    assert r.status_code == 201, r.text
    data: dict[str, Any] = r.json()
    return data


def test_everything_but_status_is_off_by_default(client: TestClient, fake: FakeProvider) -> None:
    status = client.get("/api/ai/status").json()
    assert status["enabled"] is False and status["hide_names_default"] is True
    assert "fake" in [p["id"] for p in status["providers"]]
    for method, path in [
        ("get", "/api/ai/conversations"),
        ("post", "/api/ai/conversations"),
        ("get", "/api/ai/conversations/" + "0" * 32),
        ("delete", "/api/ai/conversations"),
        ("post", f"/api/ai/conversations/{'0' * 32}/messages"),
    ]:
        kw = {"json": {"text": "hi"}} if path.endswith("messages") else {}
        if method == "post" and path.endswith("conversations"):
            kw = {"json": {}}
        r = getattr(client, method)(path, **kw)
        assert r.status_code == 409 and r.json()["error"]["code"] == "ai_disabled", path
    assert fake.requests == []


def test_conversation_lifecycle_and_stream(ai: TestClient, fake: FakeProvider) -> None:
    conv = new(ai)
    assert conv["hide_names"] is True and not conv["names_locked"]
    assert conv["scope_chats"] == [{"chat_id": 0, "title": "Jana Nováková"}]
    assert conv["provider"] == "fake" and conv["model"] == "fake-model"

    r = ai.post(f"/api/ai/conversations/{conv['id']}/messages", json={"text": "Jana vs me?"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = sse(r)
    text = "".join(e["text"] for e in events if e["type"] == "text")
    assert text == "Jana Nováková writes as much as you. [[msg:1]]"
    assert events[-1] == {"type": "done", "status": "done"}

    got = ai.get(f"/api/ai/conversations/{conv['id']}").json()
    assert got["names_locked"] and got["title"] == "Jana vs me?"
    assert got["turns"][0]["answer"] == text
    assert got["turns"][0]["tools"][0]["name"] == "list_people"
    assert got["turns"][0]["usage"]["input_tokens"] == 10

    sent = ai.get(f"/api/ai/conversations/{conv['id']}/last-request").json()
    assert "Jana" not in json.dumps(sent, ensure_ascii=False)
    assert sent["tool_calls"][0]["name"] == "list_people"
    assert "[P1]" in json.dumps(sent["tool_calls"][0]["result"])
    assert "list_chats" in sent["tools"]

    listed = ai.get("/api/ai/conversations").json()["items"]
    assert [c["id"] for c in listed] == [conv["id"]] and listed[0]["turns"] == 1

    assert ai.delete(f"/api/ai/conversations/{conv['id']}").json() == {"deleted": 1}
    assert ai.get(f"/api/ai/conversations/{conv['id']}").status_code == 404


def test_defaults_come_from_settings(ai: TestClient) -> None:
    ai.put(
        "/api/settings",
        json={"ai_default_scope": ["partia_2"], "ai_hide_names": False, "ai_effort": "high"},
    )
    assert ai.get("/api/ai/status").json()["default_chat_ids"] == [1]
    conv = ai.post("/api/ai/conversations", json={}).json()
    assert [c["chat_id"] for c in conv["scope_chats"]] == [1]
    assert conv["hide_names"] is False and conv["effort"] == "high"


def test_names_switch_locks_after_first_message(ai: TestClient) -> None:
    conv = new(ai)
    r = ai.patch(f"/api/ai/conversations/{conv['id']}", json={"hide_names": False})
    assert r.status_code == 200 and r.json()["hide_names"] is False
    ai.post(f"/api/ai/conversations/{conv['id']}/messages", json={"text": "hi"})
    r = ai.patch(f"/api/ai/conversations/{conv['id']}", json={"hide_names": True})
    assert r.status_code == 409 and r.json()["error"]["code"] == "names_locked"


def test_scope_change_mid_conversation_tells_the_model(ai: TestClient, fake: FakeProvider) -> None:
    conv = new(ai)
    url = f"/api/ai/conversations/{conv['id']}"
    ai.post(f"{url}/messages", json={"text": "first"})
    r = ai.patch(url, json={"chat_ids": [0, 1], "title": "Renamed"})
    assert r.json()["title"] == "Renamed" and len(r.json()["scope_chats"]) == 2
    ai.post(f"{url}/messages", json={"text": "second"})
    assert "changed chat access" in fake.requests[1].user_message
    assert "2 chats" in fake.requests[1].user_message


def test_validation_errors(ai: TestClient) -> None:
    assert ai.post("/api/ai/conversations", json={"chat_ids": [99]}).status_code == 422
    assert ai.post("/api/ai/conversations", json={"provider": "nope"}).status_code == 422
    conv = new(ai)
    url = f"/api/ai/conversations/{conv['id']}/messages"
    assert ai.post(url, json={"text": ""}).status_code == 422
    assert ai.get(f"/api/ai/conversations/{conv['id']}/last-request").status_code == 404
    assert ai.get("/api/ai/conversations/../../etc").status_code == 404


def test_stop_without_running_answer(ai: TestClient) -> None:
    conv = new(ai)
    assert ai.post(f"/api/ai/conversations/{conv['id']}/stop").json() == {"deleted": 0}


def test_anonymize_mode_hides_names_everywhere(ai: TestClient) -> None:
    conv = new(ai, hide_names=False)
    ai.post(f"/api/ai/conversations/{conv['id']}/messages", json={"text": "Is Jana Nováková nice?"})
    ai.put("/api/settings", json={"anonymize": True})
    leaks = ("Jana", "Nováková")
    bodies = [
        ai.get("/api/ai/conversations").json(),
        ai.get(f"/api/ai/conversations/{conv['id']}").json(),
        ai.get(f"/api/ai/conversations/{conv['id']}/last-request").json(),
    ]
    text = json.dumps(bodies, ensure_ascii=False)
    assert not [s for s in leaks if s in text], text[:500]
    assert "Person" in text


def test_clear_all_data_removes_conversations(ai: TestClient) -> None:
    new(ai)
    ai.delete("/api/data")
    ai.put("/api/settings", json=ON)
    assert ai.get("/api/ai/conversations").json()["items"] == []


def test_long_messages_up_to_100k_characters(ai: TestClient) -> None:
    conv = new(ai)
    url = f"/api/ai/conversations/{conv['id']}/messages"
    assert ai.post(url, json={"text": "a" * 50_000}).status_code == 200
    assert ai.post(url, json={"text": "a" * 100_001}).status_code == 422
