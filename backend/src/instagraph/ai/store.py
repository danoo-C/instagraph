"""Saved AI conversations (docs/ai-assistant.md A8.7).

One JSON file per conversation in ``<data_dir>/ai/conversations/<id>.json``, inside the
data directory (git-ignored, deleted by "clear all data"). Writes are atomic.

A conversation keeps two views of its history:

* ``turns`` - provider-neutral, for the UI: what the user typed, the answer as sent
  (with placeholders when names are hidden; translated back when read), tool activity
  and usage per turn;
* ``provider_state`` - whatever the provider needs to continue (the Anthropic message
  list as sent, or Claude Code's session id). Opaque to everything else.
"""

import json
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from instagraph.errors import NotFoundError

TITLE_MAX = 80


def _now() -> datetime:
    return datetime.now(UTC)


class ToolActivity(BaseModel):
    """One tool call as shown under an answer (pseudonymized form, as sent)."""

    id: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    is_error: bool = False
    truncated: bool = False


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def add(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens
            + other.cache_creation_input_tokens,
        )


class Turn(BaseModel):
    """One user message and the answer to it."""

    user_text: str
    """As typed (kept locally only; the provider got the pseudonymized form)."""
    answer: str = ""
    """As produced by the model: placeholders when names were hidden."""
    tools: list[ToolActivity] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    status: Literal["running", "done", "stopped", "error"] = "running"
    error: str | None = None
    created_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None


class Conversation(BaseModel):
    id: str
    title: str = "New conversation"
    mode: Literal["ask", "report"] = "ask"
    provider: str
    model: str
    effort: Literal["low", "medium", "high"] = "medium"
    hide_names: bool = True
    """Fixed once the first message is sent (docs/ai-assistant.md A6.6)."""
    scope: list[str] = Field(default_factory=list)
    """Thread folder keys the AI may read."""
    turns: list[Turn] = Field(default_factory=list)
    provider_state: dict[str, Any] = Field(default_factory=dict)
    last_request: dict[str, Any] | None = None
    """What the last turn sent (system prompt, message, tool calls and results), for the
    "What was sent" view. Pseudonymized when names are hidden."""
    pending_note: str | None = None
    """A note for the model prepended to the next user message (e.g. chat access changed)."""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @property
    def names_locked(self) -> bool:
        return bool(self.turns)


class ConversationSummary(BaseModel):
    id: str
    title: str
    mode: str
    provider: str
    hide_names: bool
    scope_size: int
    turns: int
    updated_at: datetime


class ConversationStore:
    """File-backed conversations. Thread-safe within one process."""

    def __init__(self, data_dir: Path) -> None:
        self.directory = data_dir / "ai" / "conversations"
        self._lock = threading.RLock()

    def _path(self, conv_id: str) -> Path:
        if not conv_id or not all(c in "0123456789abcdef" for c in conv_id):
            raise NotFoundError("Conversation not found.")
        return self.directory / f"{conv_id}.json"

    def create(
        self,
        *,
        provider: str,
        model: str,
        effort: Literal["low", "medium", "high"],
        hide_names: bool,
        scope: list[str],
        mode: Literal["ask", "report"] = "ask",
        title: str | None = None,
    ) -> Conversation:
        conv = Conversation(
            id=uuid.uuid4().hex,
            provider=provider,
            model=model,
            effort=effort,
            hide_names=hide_names,
            scope=sorted(set(scope)),
            mode=mode,
            title=(title or "New conversation")[:TITLE_MAX],
        )
        self.save(conv)
        return conv

    def get(self, conv_id: str) -> Conversation:
        path = self._path(conv_id)
        with self._lock:
            try:
                raw = path.read_text(encoding="utf-8")
            except FileNotFoundError as exc:
                raise NotFoundError("Conversation not found.") from exc
        return Conversation.model_validate_json(raw)

    def save(self, conv: Conversation) -> Conversation:
        conv.updated_at = _now()
        path = self._path(conv.id)
        data = conv.model_dump_json(indent=1)
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".tmp-{uuid.uuid4().hex}")
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, path)
        return conv

    def list(self) -> list[ConversationSummary]:
        """All conversations, most recently updated first (unreadable files skipped)."""
        out: list[ConversationSummary] = []
        with self._lock:
            paths = sorted(self.directory.glob("*.json")) if self.directory.is_dir() else []
            for path in paths:
                try:
                    conv = Conversation.model_validate(json.loads(path.read_text("utf-8")))
                except (OSError, ValueError):
                    continue
                out.append(
                    ConversationSummary(
                        id=conv.id,
                        title=conv.title,
                        mode=conv.mode,
                        provider=conv.provider,
                        hide_names=conv.hide_names,
                        scope_size=len(conv.scope),
                        turns=len(conv.turns),
                        updated_at=conv.updated_at,
                    )
                )
        out.sort(key=lambda s: s.updated_at, reverse=True)
        return out

    def delete(self, conv_id: str) -> None:
        path = self._path(conv_id)
        with self._lock:
            try:
                path.unlink()
            except FileNotFoundError as exc:
                raise NotFoundError("Conversation not found.") from exc

    def delete_all(self) -> int:
        with self._lock:
            if not self.directory.is_dir():
                return 0
            paths = list(self.directory.glob("*.json"))
            for path in paths:
                path.unlink(missing_ok=True)
            return len(paths)
