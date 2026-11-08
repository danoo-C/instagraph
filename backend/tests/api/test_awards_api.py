"""``GET /api/chats/{id}/awards`` and ``GET /api/overview/awards``."""

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from instagraph.testing import build_dataset, make_client


def _winners(body: dict[str, Any]) -> dict[str, list[int]]:
    return {a["key"]: [w["person_id"] for w in a["winners"]] for a in body["awards"]}


def test_chat_awards(client: TestClient) -> None:
    r = client.get("/api/chats/0/awards")
    assert r.status_code == 200
    body = r.json()
    assert body["scope"] == "chat" and body["chat_id"] == 0
    assert len(body["awards"]) == 18
    w = _winners(body)
    assert w["caller"] == [0]
    assert w["second_thoughts"] == [0]
    assert w["laugher"] == [1]
    assert w["reel_dealer"] == []  # Peter's reel is in the group chat
    assert w["novelist"] == []  # nobody reaches 20 text messages
    caller = next(a for a in body["awards"] if a["key"] == "caller")
    assert caller["value"] == 1
    assert caller["drilldown"] == {**caller["drilldown"], "thread_id": 0, "types": ["call"]}
    assert caller["eligibility"] == "at least one"


def test_overview_awards(client: TestClient) -> None:
    body = client.get("/api/overview/awards").json()
    assert body["scope"] == "global"
    w = _winners(body)
    assert w["reel_dealer"] == [2]
    assert w["heart_giver"] == [1]  # Jana: ❤ reaction + like = 2; Me: one ❤
    assert body["dataset_version"] >= 1


def test_overview_awards_people_filter(client: TestClient) -> None:
    w = _winners(client.get("/api/overview/awards?people=0").json())
    assert w["reel_dealer"] == []
    assert w["caller"] == [0]


def test_unknown_chat(client: TestClient) -> None:
    r = client.get("/api/chats/999/awards")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_no_data(empty_client: TestClient) -> None:
    assert empty_client.get("/api/overview/awards").status_code == 409


def test_rate_award_with_enough_activity(tmp_path: Path) -> None:
    msgs = []
    for i in range(25):
        msgs.append(
            {"thread_id": 0, "sender_id": 1, "ts": f"2024-01-{i + 1:02d}T10:00:00Z",
             "text": "one two three four five six"}
        )  # fmt: skip
        msgs.append(
            {"thread_id": 0, "sender_id": 0, "ts": f"2024-01-{i + 1:02d}T10:01:00Z", "text": "ok"}
        )
    ds = build_dataset(
        [{"display_name": "Me", "is_owner": True}, {"display_name": "Ana"}],
        [{"title": "Ana", "participants": [0, 1]}],
        msgs,
    )
    with make_client(tmp_path, ds) as c:
        body = c.get("/api/chats/0/awards").json()
    novelist = next(a for a in body["awards"] if a["key"] == "novelist")
    assert [p["person_id"] for p in novelist["winners"]] == [1]
    assert novelist["value"] == 6
    assert [e["person"]["person_id"] for e in novelist["ranking"]] == [1, 0]
    speed = next(a for a in body["awards"] if a["key"] == "speed_demon")
    assert [p["person_id"] for p in speed["winners"]] == [0]
    assert speed["value"] == 60_000
