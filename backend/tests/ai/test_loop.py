"""Turn runner with the scripted fake provider (docs/ai-assistant.md A6, A8; plan M1)."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from instagraph.ai import loop
from instagraph.ai.providers.fake import FakeProvider
from instagraph.ai.store import ConversationStore
from instagraph.ai.tools import NO_SCOPE_MESSAGE
from instagraph.errors import ApiError
from instagraph.store.app_settings import AppSettings
from instagraph.store.dataset import Dataset

ON = AppSettings(ai_enabled=True, ai_confirmed_notice=True, timezone="Europe/Bratislava")
JANA_CHAT = "jananovakova_1"


async def collect(events: AsyncIterator[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e async for e in events]


def text_of(events: list[dict[str, Any]]) -> str:
    return "".join(e["text"] for e in events if e["type"] == "text")


class Env:
    def __init__(self, tmp_path: Path, ds: Dataset, fake: FakeProvider) -> None:
        self.store = ConversationStore(tmp_path)
        self.ds = ds
        self.fake = fake

    def lookup(self, pid: str) -> FakeProvider | None:
        return self.fake if pid == self.fake.id else None

    def conversation(self, *, hide_names: bool = True, scope: list[str] | None = None) -> str:
        conv = self.store.create(
            provider="fake",
            model="fake-model",
            effort="medium",
            hide_names=hide_names,
            scope=[JANA_CHAT] if scope is None else scope,
        )
        return conv.id

    async def start(self, conv_id: str, text: str, settings: AppSettings = ON) -> Any:
        return await loop.run_turn(
            self.store,
            conv_id,
            text,
            dataset=self.ds,
            settings=settings,
            provider_lookup=self.lookup,
        )

    def ask(self, conv_id: str, text: str, settings: AppSettings = ON) -> list[dict[str, Any]]:
        async def go() -> list[dict[str, Any]]:
            return await collect(await self.start(conv_id, text, settings))

        return asyncio.run(go())


@pytest.fixture
def env(tmp_path: Path, small_dataset: Dataset) -> Env:
    return Env(tmp_path, small_dataset, FakeProvider())


# ---- names hidden ----------------------------------------------------------------------


def test_names_hidden_end_to_end(env: Env) -> None:
    env.fake.script = [
        ("tool", "list_people", {}),
        ("text", "[P1] sent 6 messages, you sent 6. [[msg:1]]"),
        ("usage", {"input_tokens": 100, "output_tokens": 20}),
    ]
    cid = env.conversation(hide_names=True)
    events = env.ask(cid, "How much does Jana Nováková write compared to me?")

    sent = env.fake.requests[0]
    assert "Jana" not in sent.user_message and "Nováková" not in sent.user_message
    assert "[P1]" in sent.user_message
    assert "today is" in sent.user_message and "1 chat " in sent.user_message
    assert "placeholders" in sent.system_prompt
    tool_text = env.fake.tool_results[0]
    assert "Jana" not in tool_text and "Nováková" not in tool_text and "[P1]" in tool_text

    assert text_of(events) == "Jana Nováková sent 6 messages, you sent 6. [[msg:1]]"
    kinds = [e["type"] for e in events]
    assert kinds[0] == "tool_start" and "tool_end" in kinds and kinds[-1] == "done"
    assert events[-1]["status"] == "done"
    usage = [e for e in events if e["type"] == "usage"][-1]
    assert usage["input_tokens"] == 100

    conv = env.store.get(cid)
    turn = conv.turns[0]
    assert turn.answer.startswith("[P1]")  # stored as sent
    assert turn.user_text.startswith("How much does Jana")  # as typed, local only
    assert turn.tools[0].name == "list_people" and turn.tools[0].summary
    assert conv.title.startswith("How much does Jana")
    assert conv.names_locked
    shown = loop.present(conv, env.ds, ON)
    assert shown["turns"][0]["answer"].startswith("Jana Nováková")
    assert "provider_state" not in shown


def test_split_placeholders_are_joined_while_streaming(env: Env) -> None:
    env.fake.chunk = 1  # every character arrives on its own
    env.fake.script = [("text", "[P1] and [ME] and [P99]")]
    events = env.ask(env.conversation(), "hi")
    assert text_of(events) == "Jana Nováková and Me and [P99]"


def test_context_note_only_on_first_turn(env: Env) -> None:
    cid = env.conversation()
    env.ask(cid, "first")
    env.ask(cid, "second")
    assert "[Context" in env.fake.requests[0].user_message
    assert "[Context" not in env.fake.requests[1].user_message
    assert len(env.store.get(cid).turns) == 2


# ---- names visible ---------------------------------------------------------------------


def test_names_visible_sends_real_names(env: Env) -> None:
    env.fake.script = [("tool", "list_people", {}), ("text", "Jana writes a lot.")]
    cid = env.conversation(hide_names=False)
    events = env.ask(cid, "Does Jana write a lot?")
    sent = env.fake.requests[0]
    assert "Jana" in sent.user_message
    assert "placeholders" not in sent.system_prompt
    assert "Jana Nováková" in env.fake.tool_results[0]
    assert text_of(events) == "Jana writes a lot."
    assert env.store.get(cid).turns[0].answer == "Jana writes a lot."


# ---- scope, errors, limits -------------------------------------------------------------


def test_empty_scope_tools_refuse(env: Env) -> None:
    env.fake.script = [("tool", "list_chats", {}), ("text", "ok")]
    env.ask(env.conversation(scope=[]), "hi")
    assert NO_SCOPE_MESSAGE in env.fake.tool_results[0]
    assert "0 chats" in env.fake.requests[0].user_message


def test_feature_off_refuses(env: Env) -> None:
    cid = env.conversation()
    with pytest.raises(ApiError) as e:
        env.ask(cid, "hi", AppSettings())
    assert e.value.code == "ai_disabled"
    assert env.fake.requests == []


def test_preflight_errors(env: Env) -> None:
    cid = env.conversation()
    with pytest.raises(ApiError) as e:
        env.ask(cid, "   ")
    assert e.value.code == "empty_message"
    env.fake.available = False
    with pytest.raises(ApiError) as e:
        env.ask(cid, "hi")
    assert e.value.code == "provider_unavailable"
    conv = env.store.create(
        provider="missing", model="m", effort="medium", hide_names=True, scope=[]
    )
    with pytest.raises(ApiError) as e:
        env.ask(conv.id, "hi")
    assert e.value.code == "provider_unavailable"


def test_provider_error_event(env: Env) -> None:
    env.fake.script = [("text", "partial "), ("error", "rate_limit", "Too many requests")]
    cid = env.conversation()
    events = env.ask(cid, "hi")
    assert {"type": "error", "code": "rate_limit", "message": "Too many requests"} in events
    assert events[-1] == {"type": "done", "status": "error"}
    turn = env.store.get(cid).turns[0]
    assert turn.status == "error" and turn.answer == "partial "


class Exploding(FakeProvider):
    async def run_turn(self, request: Any) -> AsyncIterator[Any]:  # type: ignore[override]
        raise RuntimeError("boom")
        yield  # pragma: no cover


def test_provider_exception_ends_turn_cleanly(tmp_path: Path, small_dataset: Dataset) -> None:
    env = Env(tmp_path, small_dataset, Exploding())
    cid = env.conversation()
    events = env.ask(cid, "hi")
    assert events[0]["code"] == "provider_error" and "boom" in events[0]["message"]
    assert env.store.get(cid).turns[0].status == "error"
    assert not loop.is_running(cid)


def test_stop_cancels_quickly(env: Env) -> None:
    env.fake.script = [("text", "a"), ("wait", 10), ("text", "b")]
    cid = env.conversation()

    async def go() -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        async for ev in await env.start(cid, "hi"):
            events.append(ev)
            if ev["type"] == "text":
                assert loop.cancel(cid)
        return events

    t0 = time.monotonic()
    events = asyncio.run(go())
    assert time.monotonic() - t0 < 5
    assert events[-1] == {"type": "done", "status": "stopped"}
    assert env.store.get(cid).turns[0].status == "stopped"
    assert not loop.cancel(cid)


def test_one_turn_at_a_time(env: Env) -> None:
    env.fake.script = [("text", "a"), ("wait", 10)]
    cid = env.conversation()

    async def go() -> None:
        events = await env.start(cid, "first")
        first = await events.__anext__()
        assert first["type"] == "text" and loop.is_running(cid)
        with pytest.raises(ApiError) as e:
            await env.start(cid, "second")
        assert e.value.code == "turn_running"
        loop.cancel(cid)
        async for _ in events:
            pass

    asyncio.run(go())
    assert not loop.is_running(cid)


def test_client_disconnect_saves_turn(env: Env) -> None:
    env.fake.script = [("text", "abcdef"), ("wait", 10)]
    cid = env.conversation()

    async def go() -> None:
        events = await env.start(cid, "hi")
        await events.__anext__()
        await events.aclose()  # what Starlette does when the browser goes away

    asyncio.run(go())
    turn = env.store.get(cid).turns[0]
    assert turn.status == "stopped" and turn.finished_at is not None
    assert not loop.is_running(cid)


def test_unstarted_stream_does_not_block(env: Env) -> None:
    cid = env.conversation()

    async def go() -> None:
        await env.start(cid, "never iterated")

    asyncio.run(go())
    assert not loop.is_running(cid)
    env.ask(cid, "works")
