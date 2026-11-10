"""Provider interface (docs/ai-assistant.md A8.1, decision AD6).

A provider turns one user message into a stream of :data:`ProviderEvent` values, using
the conversation's :class:`~instagraph.ai.tools.ToolBox` for data. It doesn't know
about scope or name hiding: the tool box already applies both, and the turn runner
(:mod:`instagraph.ai.loop`) translates names back in the text it emits.

Providers are discovered automatically: every module in this package that defines a
module-level ``provider`` object is registered (see ``__init__.py``).
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from instagraph.ai.store import Conversation, Usage
from instagraph.ai.tools import ToolBox
from instagraph.store.app_settings import AppSettings


@dataclass(frozen=True)
class ProviderStatus:
    available: bool
    """Ready to answer (package installed and key set, or Claude Code found and logged in)."""
    message: str = ""
    """Why it isn't available, or a short note shown under the provider name."""
    models: tuple[str, ...] = ()


@dataclass(frozen=True)
class TextDelta:
    text: str
    type: Literal["text"] = "text"


@dataclass(frozen=True)
class ToolStart:
    id: str
    name: str
    args: dict[str, Any]
    type: Literal["tool_start"] = "tool_start"


@dataclass(frozen=True)
class ToolEnd:
    id: str
    name: str
    summary: str
    is_error: bool = False
    truncated: bool = False
    type: Literal["tool_end"] = "tool_end"


@dataclass(frozen=True)
class UsageEvent:
    usage: Usage
    type: Literal["usage"] = "usage"


@dataclass(frozen=True)
class ErrorEvent:
    code: str
    message: str
    type: Literal["error"] = "error"


ProviderEvent = TextDelta | ToolStart | ToolEnd | UsageEvent | ErrorEvent


@dataclass
class TurnRequest:
    """Everything a provider needs for one answer."""

    conversation: Conversation
    """Mutable: the provider updates ``conversation.provider_state`` to continue later."""
    user_message: str
    """Already pseudonymized when names are hidden; includes the context note on the
    first turn."""
    system_prompt: str
    tools: ToolBox
    model: str
    effort: Literal["low", "medium", "high"]
    settings: AppSettings
    cancel: asyncio.Event = field(default_factory=asyncio.Event)


class Provider(Protocol):
    id: str
    label: str

    def status(self, settings: AppSettings) -> ProviderStatus: ...

    def run_turn(self, request: TurnRequest) -> AsyncIterator[ProviderEvent]:
        """Stream one answer. Must stop soon after ``request.cancel`` is set, and must
        not raise for expected failures (yield an :class:`ErrorEvent` instead)."""
        ...
