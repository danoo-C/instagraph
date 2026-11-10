"""Provider-agnostic turn runner (docs/ai-assistant.md A8, plan "Architecture").

For one user message it:

1. checks the feature is on and the conversation's provider is ready;
2. builds the scoped tool box (allowlist) and, when names are hidden, the
   pseudonymizer: the user's message and every tool result are pseudonymized, tool
   arguments are translated back before running;
3. streams the provider's events, translating placeholders back to names in the text
   (split placeholders are held back until complete);
4. saves the turn (answer as sent, tool activity, usage, status) - also when the
   client disconnects or the user presses Stop.

Events yielded are plain dicts for the SSE endpoint: ``text``, ``tool_start``,
``tool_end``, ``usage``, ``error``, ``done``.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl

from instagraph.ai import prompts
from instagraph.ai.providers import get_provider
from instagraph.ai.providers.base import (
    ErrorEvent,
    Provider,
    TextDelta,
    ToolEnd,
    ToolStart,
    TurnRequest,
    UsageEvent,
)
from instagraph.ai.pseudonym import ChatEntry, PersonEntry, Pseudonymizer
from instagraph.ai.scope import ai_context, resolve_scope
from instagraph.ai.store import Conversation, ConversationStore, ToolActivity, Turn
from instagraph.ai.tools import ToolBox
from instagraph.api.deps import AnalysisContext
from instagraph.errors import ApiError
from instagraph.store.app_settings import AppSettings
from instagraph.store.dataset import Dataset

logger = logging.getLogger("instagraph")

TITLE_FROM_MESSAGE = 60

#: conversation id -> cancel event of its running turn (one turn at a time).
_running: dict[str, asyncio.Event] = {}

_pseudo_cache: dict[tuple[Any, ...], Pseudonymizer] = {}


class AiDisabledError(ApiError):
    def __init__(self) -> None:
        super().__init__("ai_disabled", "The AI assistant is turned off in Settings.", 409)


def ensure_enabled(settings: AppSettings) -> None:
    if not settings.ai_enabled:
        raise AiDisabledError()


# --------------------------------------------------------------------------------------
# Pseudonymizer for a dataset + settings
# --------------------------------------------------------------------------------------


def _username_from_folder(folder: str) -> str | None:
    """Instagram folder keys look like ``username_123456``; return ``username``."""
    base, _, tail = folder.rpartition("_")
    return base if base and tail.isdigit() else None


def build_pseudonymizer(ds: Dataset, settings: AppSettings) -> Pseudonymizer:
    """Placeholder mapping over the WHOLE dataset (stable across conversations and
    scopes; also hides people outside the scope when their names appear in text).

    Names are matched by their real forms; decoding shows the app's displayed names
    (the anonymize labels while anonymize mode is on)."""
    key = (
        ds.version,
        settings.settings_hash(),
        tuple(sorted((k, tuple(v)) for k, v in settings.ai_person_aliases.items())),
        tuple(sorted(settings.ai_alias_disabled)),
    )
    hit = _pseudo_cache.get(key)
    if hit is not None:
        return hit

    ctx = AnalysisContext(dataset=ds, filters=_plain_filters(), settings=settings)
    refs = ctx.person_refs()
    people_df = ds.people_df()
    usernames: dict[int, set[str]] = {}
    for row in ds.threads_df().filter(~pl.col("is_group")).iter_rows(named=True):
        user = _username_from_folder(str(row["folder"] or ""))
        others = [p for p in row["participants"] or [] if p != ctx.owner_id]
        if user and len(others) == 1:
            usernames.setdefault(int(others[0]), set()).add(user)

    disabled = set(settings.ai_alias_disabled)
    people: list[PersonEntry] = []
    for row in people_df.iter_rows(named=True):
        pid = int(row["person_id"])
        real = str(row["display_name"] or "")
        shown = refs[pid].name if pid in refs else real
        aliases = {a for a in (row["aliases"] or []) if a}
        aliases.update(settings.ai_person_aliases.get(real, []))
        if shown != real:
            aliases.add(real)  # anonymize mode: match the real name, show the label
        aliases.discard(shown)
        people.append(
            PersonEntry(
                person_id=pid,
                display_name=shown,
                aliases=tuple(sorted(aliases)),
                usernames=tuple(sorted(usernames.get(pid, set()))),
                is_owner=pid == ctx.owner_id,
                # Deleted accounts are shown as "Instagram User ...": nothing to find in text.
                match_in_text=real not in disabled and not row["is_deleted_account"],
            )
        )
    titles = ctx.thread_titles()
    chats = [ChatEntry(thread_id=tid, title=title) for tid, title in sorted(titles.items())]
    pseudo = Pseudonymizer(people, chats)
    if len(_pseudo_cache) > 4:
        _pseudo_cache.clear()
    _pseudo_cache[key] = pseudo
    return pseudo


def _plain_filters() -> Any:
    from instagraph.api.filters import FilterParams

    return FilterParams(include_requests=True)


# --------------------------------------------------------------------------------------
# Running a turn
# --------------------------------------------------------------------------------------


def is_running(conv_id: str) -> bool:
    return conv_id in _running


def cancel(conv_id: str) -> bool:
    """Ask the running turn of ``conv_id`` to stop. ``False`` if none is running."""
    event = _running.get(conv_id)
    if event is None:
        return False
    event.set()
    return True


def _tool_box(
    ds: Dataset, settings: AppSettings, conv: Conversation, pseudo: Pseudonymizer | None
) -> tuple[ToolBox, int]:
    thread_ids = resolve_scope(ds, conv.scope)
    ctx = ai_context(ds, settings, thread_ids) if thread_ids else None
    return ToolBox(ctx, encoder=pseudo), len(thread_ids)


async def run_turn(
    store: ConversationStore,
    conv_id: str,
    user_text: str,
    *,
    dataset: Dataset,
    settings: AppSettings,
    provider_lookup: Callable[[str], Provider | None] = get_provider,
) -> AsyncIterator[dict[str, Any]]:
    """Run one turn and stream its events (see module docstring).

    Raises :class:`ApiError` *before* the first event for problems the route should
    turn into an HTTP error (feature off, unknown conversation, provider not ready,
    turn already running, empty message).
    """
    ensure_enabled(settings)
    text = user_text.strip()
    if not text:
        raise ApiError("empty_message", "Type a message first.", 422)
    conv = store.get(conv_id)
    provider = provider_lookup(conv.provider)
    if provider is None:
        raise ApiError(
            "provider_unavailable", f"AI provider {conv.provider!r} isn't installed.", 409
        )
    status = provider.status(settings)
    if not status.available:
        raise ApiError("provider_unavailable", status.message or "AI provider isn't ready.", 409)
    if conv_id in _running:
        raise ApiError("turn_running", "An answer is still being written.", 409)

    pseudo = build_pseudonymizer(dataset, settings) if conv.hide_names else None
    tools, n_chats = _tool_box(dataset, settings, conv, pseudo)
    # The running marker is set when the stream starts (not here), so a stream that is
    # never iterated can't leave the conversation stuck as "running".
    return _stream(store, conv, provider, text, tools, n_chats, pseudo, settings)


async def _stream(
    store: ConversationStore,
    conv: Conversation,
    provider: Provider,
    text: str,
    tools: ToolBox,
    n_chats: int,
    pseudo: Pseudonymizer | None,
    settings: AppSettings,
) -> AsyncIterator[dict[str, Any]]:
    if conv.id in _running:  # another request started a turn in the meantime
        yield {
            "type": "error",
            "code": "turn_running",
            "message": "An answer is still being written.",
        }
        yield {"type": "done", "status": "error"}
        return
    cancel_event = asyncio.Event()
    first = not conv.turns
    turn = Turn(user_text=text)
    conv.turns.append(turn)
    if first and conv.title == "New conversation":
        conv.title = text[:TITLE_FROM_MESSAGE] + ("…" if len(text) > TITLE_FROM_MESSAGE else "")
    message = pseudo.encode_text(text) if pseudo else text
    if first:
        tz = settings.timezone or "UTC"
        today = datetime.now(ZoneInfo(tz)).date()
        note = prompts.context_note(today=today, tz=tz, chats_shared=n_chats)
        message = f"{note}\n\n{message}"
    elif conv.pending_note:
        message = f"{conv.pending_note}\n\n{message}"
    conv.pending_note = None
    request = TurnRequest(
        conversation=conv,
        user_message=message,
        system_prompt=prompts.system_prompt(hide_names=pseudo is not None),
        tools=tools,
        model=conv.model,
        effort=conv.effort,
        settings=settings,
        cancel=cancel_event,
    )
    decoder = pseudo.stream_decoder() if pseudo else None
    _running[conv.id] = cancel_event
    try:
        store.save(conv)
        agen = provider.run_turn(request)
        try:
            async for ev in agen:
                if isinstance(ev, TextDelta):
                    turn.answer += ev.text
                    out = decoder.feed(ev.text) if decoder else ev.text
                    if out:
                        yield {"type": "text", "text": out}
                elif isinstance(ev, ToolStart):
                    turn.tools.append(ToolActivity(id=ev.id, name=ev.name, args=ev.args))
                    yield {"type": "tool_start", "id": ev.id, "name": ev.name, "args": ev.args}
                elif isinstance(ev, ToolEnd):
                    for act in turn.tools:
                        if act.id == ev.id:
                            act.summary, act.is_error = ev.summary, ev.is_error
                            act.truncated = ev.truncated
                    yield {
                        "type": "tool_end",
                        "id": ev.id,
                        "name": ev.name,
                        "summary": ev.summary,
                        "is_error": ev.is_error,
                        "truncated": ev.truncated,
                    }
                elif isinstance(ev, UsageEvent):
                    turn.usage = turn.usage.add(ev.usage)
                    yield {"type": "usage", **turn.usage.model_dump()}
                elif isinstance(ev, ErrorEvent):
                    turn.status, turn.error = "error", ev.message
                    yield {"type": "error", "code": ev.code, "message": ev.message}
                    break
                if cancel_event.is_set():
                    turn.status = "stopped"
                    break
        except Exception as exc:  # provider bug or network failure: end the turn cleanly
            logger.exception("AI provider %s failed", provider.id)
            turn.status, turn.error = "error", str(exc)[:300] or type(exc).__name__
            yield {"type": "error", "code": "provider_error", "message": turn.error}
        finally:
            # Close the provider's stream right away (a `break` alone leaves it suspended),
            # so it can stop its work, e.g. terminate a subprocess, before we go on.
            aclose = getattr(agen, "aclose", None)
            if aclose is not None:
                with contextlib.suppress(Exception):
                    await aclose()
        if decoder:
            rest = decoder.flush()
            if rest:
                yield {"type": "text", "text": rest}
        if cancel_event.is_set() and turn.status == "running":
            turn.status = "stopped"
        if turn.status == "running":
            turn.status = "done"
        yield {"type": "done", "status": turn.status}
    finally:
        if turn.status == "running":  # client went away mid-answer
            turn.status = "stopped"
        turn.finished_at = datetime.now(UTC)
        conv.last_request = {
            "provider": provider.id,
            "model": conv.model,
            "system_prompt": request.system_prompt,
            "user_message": request.user_message,
            "tools": [t["name"] for t in tools.definitions()],
            "tool_calls": tools.log,
        }
        _running.pop(conv.id, None)
        store.save(conv)


# --------------------------------------------------------------------------------------
# Reading conversations back
# --------------------------------------------------------------------------------------


def scope_note(n_chats: int) -> str:
    chats = f"{n_chats} chat" + ("" if n_chats == 1 else "s")
    return f"[Context from the app: the user changed chat access. You can now read {chats}.]"


def screener(dataset: Dataset | None, settings: AppSettings) -> Callable[[str], str]:
    """In anonymize mode: a function replacing real names in any text with the anonymize
    labels ("Person A"...). Otherwise the identity."""
    if not settings.anonymize or dataset is None:
        return lambda text: text
    pseudo = build_pseudonymizer(dataset, settings)
    return lambda text: pseudo.decode_text(pseudo.encode_text(text))


def screen_value(screen: Callable[[str], str], value: Any) -> Any:
    if isinstance(value, str):
        return screen(value)
    if isinstance(value, dict):
        return {k: screen_value(screen, v) for k, v in value.items()}
    if isinstance(value, list):
        return [screen_value(screen, v) for v in value]
    return value


def scope_chats(
    dataset: Dataset | None, settings: AppSettings, conv: Conversation
) -> tuple[list[dict[str, Any]], int]:
    """``[{chat_id, title}]`` for the conversation's scope (titles anonymize-aware) and
    how many stored folders no longer exist in the dataset."""
    if dataset is None:
        return [], len(conv.scope)
    ids = resolve_scope(dataset, conv.scope)
    ctx = AnalysisContext(dataset=dataset, filters=_plain_filters(), settings=settings)
    chats = [{"chat_id": t, "title": ctx.thread_title(t)} for t in sorted(ids)]
    found = {str(dataset.thread_rows()[t]["folder"]) for t in ids}
    return chats, len(set(conv.scope) - found)


def present(conv: Conversation, dataset: Dataset | None, settings: AppSettings) -> dict[str, Any]:
    """Conversation for the UI: answers translated back to names (current mapping;
    placeholders that no longer resolve stay as they are), scope resolved to chats, and
    in anonymize mode every real name replaced by its label."""
    data = conv.model_dump(
        mode="json", exclude={"provider_state", "last_request", "pending_note", "scope"}
    )
    data["names_locked"] = conv.names_locked
    data["running"] = is_running(conv.id)
    data["scope_chats"], data["scope_missing"] = scope_chats(dataset, settings, conv)
    if conv.hide_names and dataset is not None:
        pseudo = build_pseudonymizer(dataset, settings)
        for t in data["turns"]:
            t["answer"] = pseudo.decode_text(t["answer"])
    screen = screener(dataset, settings)
    data["title"] = screen(data["title"])
    for t in data["turns"]:
        t["user_text"] = screen(t["user_text"])
        t["answer"] = screen(t["answer"])
        if t.get("error"):
            t["error"] = screen(t["error"])
        t["tools"] = screen_value(screen, t["tools"])
    return data
