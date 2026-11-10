"""Anthropic API provider (docs/ai-assistant.md A8.1-A8.3) with a fake SDK client.

Nothing here calls the real API: the provider's ``client_factory`` returns a fake whose
``beta.messages.stream(**params)`` replays scripted rounds and records every request.
"""

import asyncio
import json
import os
import stat
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

anthropic = pytest.importorskip("anthropic")

from instagraph.ai import keys, loop, providers  # noqa: E402
from instagraph.ai.providers.anthropic_api import AnthropicProvider, echo_content  # noqa: E402
from instagraph.ai.store import ConversationStore  # noqa: E402
from instagraph.store.app_settings import AppSettings  # noqa: E402
from instagraph.store.dataset import get_store  # noqa: E402

ON = AppSettings(ai_enabled=True, ai_confirmed_notice=True, ai_provider="anthropic")
KEY = "sk-ant-test-0123456789abcd"


def delta(text: str) -> Any:
    return SimpleNamespace(
        type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=text)
    )


def final(content: list[dict[str, Any]], stop: str, **usage: int) -> Any:
    return SimpleNamespace(
        content=content,
        stop_reason=stop,
        stop_details=SimpleNamespace(category="cyber") if stop == "refusal" else None,
        usage=SimpleNamespace(**({"input_tokens": 10, "output_tokens": 5} | usage)),
    )


class Round:
    def __init__(self, events: list[Any], message: Any, wait: float = 0) -> None:
        self.events, self.message, self.wait = events, message, wait

    async def __aenter__(self) -> "Round":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def _iter(self) -> AsyncIterator[Any]:
        for ev in self.events:
            yield ev
            if self.wait:
                await asyncio.sleep(self.wait)

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._iter()

    async def get_final_message(self) -> Any:
        return self.message


class FakeClient:
    def __init__(self, rounds: list[Round | Exception]) -> None:
        self.rounds = list(rounds)
        self.calls: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))
        self.models = SimpleNamespace(list=self._list)
        self.key_ok = True

    def _stream(self, **params: Any) -> Round:
        self.calls.append(json.loads(json.dumps(params)))
        r = self.rounds.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    async def _list(self, **_: Any) -> Any:
        if not self.key_ok:
            raise anthropic.AuthenticationError(
                "bad key",
                response=httpx.Response(401, request=httpx.Request("GET", "https://x")),
                body=None,
            )
        return []


TOOL_ROUND = Round(
    [],
    final(
        [
            {"type": "thinking", "thinking": "", "signature": "sig-abc"},
            {"type": "tool_use", "id": "tu_1", "name": "list_people", "input": {}},
        ],
        "tool_use",
    ),
)


def answer_round(text: str = "[P1] writes a lot.") -> Round:
    return Round(
        [delta(text[:5]), delta(text[5:])], final([{"type": "text", "text": text}], "end_turn")
    )


class Env:
    def __init__(self, client: FakeClient, model: str = "claude-opus-5-5") -> None:
        self.data_dir = get_store().data_dir
        keys.save_api_key(self.data_dir, KEY)
        self.client = client
        self.made: list[str] = []
        self.provider = AnthropicProvider(client_factory=self._make)
        self.store = ConversationStore(self.data_dir)
        self.cid = self.store.create(
            provider="anthropic",
            model=model,
            effort="low",
            hide_names=True,
            scope=["jananovakova_1"],
        ).id

    def _make(self, key: str) -> FakeClient:
        self.made.append(key)
        return self.client

    def ask(self, text: str, cancel_after_text: bool = False) -> list[dict[str, Any]]:
        async def go() -> list[dict[str, Any]]:
            out = []
            events = await loop.run_turn(
                self.store,
                self.cid,
                text,
                dataset=get_store().get(),
                settings=ON,
                provider_lookup=lambda _: self.provider,
            )
            async for ev in events:
                out.append(ev)
                if cancel_after_text and ev["type"] == "text":
                    loop.cancel(self.cid)
            return out

        return asyncio.run(go())

    def history(self) -> list[dict[str, Any]]:
        data: list[dict[str, Any]] = self.store.get(self.cid).provider_state.get("messages", [])
        return data


@pytest.fixture(autouse=True)
def no_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


# ---- the request ---------------------------------------------------------------------------


def test_tool_loop_request_shape_and_history(client: TestClient) -> None:
    env = Env(FakeClient([TOOL_ROUND, answer_round()]))
    events = env.ask("How much does Jana Nováková write?")
    assert (
        "".join(e["text"] for e in events if e["type"] == "text") == "Jana Nováková writes a lot."
    )
    assert events[-1] == {"type": "done", "status": "done"}
    assert env.made == [KEY]

    first, second = env.client.calls
    assert first["model"] == "claude-opus-5-5"
    assert first["thinking"] == {"type": "adaptive"}
    assert first["output_config"] == {"effort": "low"}
    assert first["cache_control"] == {"type": "ephemeral"}
    assert first["betas"] == ["server-side-fallback-2026-07-01"] and first["fallbacks"] == "default"
    assert "tool_choice" not in first
    assert all(t["strict"] and t["eager_input_streaming"] for t in first["tools"])
    assert "placeholders" in first["system"]
    assert first["messages"][0]["role"] == "user"
    assert "Jana" not in json.dumps(first["messages"], ensure_ascii=False)

    sent = second["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert sent[1]["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig-abc"}
    result = sent[2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "tu_1"
    assert "[P1]" in result["content"] and "Jana" not in result["content"]

    history = env.history()
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
    # Append-only: the next turn sends the earlier history unchanged.
    env.client.rounds.append(answer_round("ok"))
    env.ask("Thanks")
    assert env.client.calls[2]["messages"][:4] == history


def test_haiku_has_no_fallback(client: TestClient) -> None:
    env = Env(FakeClient([answer_round("hi")]), model="claude-haiku-5-5")
    env.ask("hi")
    call = env.client.calls[0]
    assert call["model"] == "claude-haiku-5-5"
    assert "fallbacks" not in call and "betas" not in call


def test_unknown_model_falls_back_to_opus(client: TestClient) -> None:
    env = Env(FakeClient([answer_round("hi")]), model="something-else")
    env.ask("hi")
    assert env.client.calls[0]["model"] == "claude-opus-5-5"


# ---- unfinished turns roll back ------------------------------------------------------------


def test_refusal_rolls_back(client: TestClient) -> None:
    env = Env(FakeClient([Round([], final([], "refusal"))]))
    events = env.ask("hi")
    err = next(e for e in events if e["type"] == "error")
    assert err["code"] == "refusal" and "cyber" in err["message"]
    assert env.history() == []


def test_api_error_mid_turn_rolls_back(client: TestClient) -> None:
    conn = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api"))
    env = Env(FakeClient([TOOL_ROUND, conn]))
    events = env.ask("hi")
    err = next(e for e in events if e["type"] == "error")
    assert err["code"] == "network" and KEY not in json.dumps(events)
    assert env.history() == []
    assert env.store.get(env.cid).turns[0].status == "error"


def test_max_tokens(client: TestClient) -> None:
    env = Env(
        FakeClient([Round([delta("abc")], final([{"type": "text", "text": "abc"}], "max_tokens"))])
    )
    events = env.ask("hi")
    assert any(e.get("code") == "max_tokens" for e in events)
    assert env.history() == []


def test_stop_rolls_back(client: TestClient) -> None:
    slow = Round([delta("a"), delta("b"), delta("c")], final([], "end_turn"), wait=5)
    env = Env(FakeClient([slow]))
    events = env.ask("hi", cancel_after_text=True)
    assert events[-1] == {"type": "done", "status": "stopped"}
    assert env.history() == []


def test_echo_content_after_mid_output_fallback() -> None:
    blocks = [
        {"type": "thinking", "thinking": "", "signature": "s"},
        {"type": "text", "text": "partial "},
        {"type": "tool_use", "id": "x", "name": "list_chats", "input": {}},
        {"type": "fallback", "from": {"model": "a"}, "to": {"model": "b"}},
        {"type": "thinking", "thinking": "", "signature": "t"},
        {"type": "text", "text": "rest"},
    ]
    assert echo_content(blocks) == [
        {"type": "text", "text": "partial "},
        {"type": "thinking", "thinking": "", "signature": "t"},
        {"type": "text", "text": "rest"},
    ]
    assert echo_content(blocks[:3]) == blocks[:3]


# ---- status and keys -----------------------------------------------------------------------


def test_status_follows_the_key(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = get_store().data_dir
    p = AnthropicProvider(client_factory=lambda k: FakeClient([]))
    assert not p.status(ON).available
    keys.save_api_key(data_dir, KEY)
    st = p.status(ON)
    assert st.available and "…abcd" in st.message and KEY not in st.message
    assert st.models == ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")
    mode = stat.S_IMODE(os.stat(keys.key_path(data_dir)).st_mode)
    assert mode == 0o600
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env-wxyz")
    assert keys.get_api_key(data_dir) == "sk-ant-from-env-wxyz"
    assert keys.key_info(data_dir).source == "env"


def test_bad_keys_are_refused(tmp_path: Path) -> None:
    for bad in ("", "  ", "has space", "x" * 600):
        with pytest.raises(ValueError):
            keys.save_api_key(tmp_path, bad)


@pytest.fixture
def fake_anthropic() -> Iterator[FakeClient]:
    fake = FakeClient([])
    providers.reset()
    providers.register(AnthropicProvider(client_factory=lambda k: fake))
    yield fake
    providers.reset()


def test_key_endpoints_never_return_the_key(client: TestClient, fake_anthropic: FakeClient) -> None:
    assert client.get("/api/ai/status").json()["api_key"] == {
        "set": False,
        "source": None,
        "hint": "",
    }
    r = client.put("/api/ai/key", json={"key": KEY})
    assert r.status_code == 200 and r.json() == {"set": True, "source": "file", "hint": "…abcd"}
    assert client.put("/api/ai/key", json={"key": "has space"}).status_code == 422
    bodies = [
        client.get("/api/ai/status").text,
        client.get("/api/settings").text,
        client.get("/api/export/all.json").text
        if client.get("/api/export/all.json").status_code == 200
        else "",
    ]
    assert not [b for b in bodies if KEY in b]
    settings_file = get_store().data_dir / "settings.json"
    assert not settings_file.exists() or KEY not in settings_file.read_text()

    assert client.post("/api/ai/key/test").json() == {"ok": True, "message": "The key works."}
    fake_anthropic.key_ok = False
    res = client.post("/api/ai/key/test", json={"key": "sk-other-key-1234"}).json()
    assert res["ok"] is False and "rejected" in res["message"]

    assert client.delete("/api/ai/key").json()["set"] is False
    assert client.post("/api/ai/key/test").json()["ok"] is False


def test_status_does_not_create_a_client(client: TestClient) -> None:
    made: list[str] = []
    providers.reset()
    providers.register(AnthropicProvider(client_factory=lambda k: made.append(k) or FakeClient([])))
    try:
        keys.save_api_key(get_store().data_dir, KEY)
        client.get("/api/ai/status")
        assert made == []  # nothing is contacted until a message is sent or a key tested
    finally:
        providers.reset()
