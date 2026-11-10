"""Scripted provider for tests and UI development (never discovered automatically).

A script is a list of steps run in order for every turn:

* ``("text", "...")`` - stream this text (split into small chunks, so stream
  translation of split placeholders gets exercised);
* ``("tool", name, args)`` - call a tool through the real tool box;
* ``("usage", {...})`` - report token usage;
* ``("error", code, message)`` - fail the turn;
* ``("wait", seconds)`` - pause (for cancel tests).

With ``echo_tools=True`` the text step may contain ``{tool:N}``, replaced by the text
of the N-th tool result of this turn (handy for checking what the model would see).
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from instagraph.ai.providers.base import (
    ErrorEvent,
    ProviderEvent,
    ProviderStatus,
    TextDelta,
    ToolEnd,
    ToolStart,
    TurnRequest,
    UsageEvent,
)
from instagraph.ai.store import Usage
from instagraph.store.app_settings import AppSettings

Step = tuple[Any, ...]


@dataclass
class FakeProvider:
    script: Sequence[Step] = (("text", "Hello from the fake provider."),)
    id: str = "fake"
    label: str = "Fake (tests)"
    chunk: int = 3
    available: bool = True
    requests: list[TurnRequest] = field(default_factory=list)
    """Every request received (tests inspect what would have been sent)."""
    tool_results: list[str] = field(default_factory=list)

    def status(self, settings: AppSettings) -> ProviderStatus:
        return ProviderStatus(
            available=self.available,
            message="" if self.available else "Fake provider switched off.",
            models=("fake-model",),
        )

    async def run_turn(self, request: TurnRequest) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        results: list[str] = []
        history = request.conversation.provider_state.setdefault("messages", [])
        history.append({"role": "user", "content": request.user_message})
        answer: list[str] = []
        for i, step in enumerate(self.script):
            if request.cancel.is_set():
                return
            kind = step[0]
            if kind == "text":
                text = str(step[1])
                for n, r in enumerate(results):
                    text = text.replace(f"{{tool:{n}}}", r)
                answer.append(text)
                for j in range(0, len(text), self.chunk):
                    yield TextDelta(text[j : j + self.chunk])
                    await asyncio.sleep(0)
            elif kind == "tool":
                call_id = f"call_{i}"
                yield ToolStart(call_id, step[1], dict(step[2]))
                result = await request.tools.acall(step[1], step[2])
                results.append(result.as_text())
                self.tool_results.append(result.as_text())
                yield ToolEnd(call_id, step[1], result.summary, result.is_error, result.truncated)
            elif kind == "usage":
                yield UsageEvent(Usage(**step[1]))
            elif kind == "error":
                yield ErrorEvent(step[1], step[2])
                return
            elif kind == "wait":
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(request.cancel.wait(), timeout=float(step[1]))
        history.append({"role": "assistant", "content": "".join(answer)})


DEMO_SCRIPT: tuple[Step, ...] = (
    ("tool", "list_chats", {"limit": 3}),
    ("tool", "get_overview", {"section": "summary"}),
    (
        "text",
        "**This is the fake provider** (development only, `INSTAGRAPH_AI_FAKE=1`). "
        "Nothing was sent anywhere.\n\n"
        "It called two real tools on the chats you shared, so you can check the tool list "
        "above and the *What was sent* view.\n\n"
        "- With names hidden, the model only saw placeholders; [ME] here was one of them.\n"
        "- A message citation looks like this: [[msg:1]]\n"
        "- A chat citation looks like this: [[chat:0]]",
    ),
    ("usage", {"input_tokens": 1200, "output_tokens": 90}),
)


def demo_provider() -> "FakeProvider":
    return FakeProvider(script=DEMO_SCRIPT, label="Fake (development)", chunk=4)
