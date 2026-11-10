"""Read-only tools the AI can call (docs/ai-assistant.md A5).

Every tool calls the same route function the app's API uses, on the *scoped* context
from :mod:`instagraph.ai.scope`, so numbers match the app's pages and nothing outside
the allowlist is reachable. Results then go through:

1. :func:`compact` - drop fields the model doesn't need (colors, drilldown queries,
   media URLs and file paths, dataset version);
2. :func:`fit` - cap the size (about 8k tokens), shrinking the longest lists first;
3. the pseudonymizer, when names are hidden (applied by :class:`ToolBox`).

Tools never raise into the turn: failures come back as ``is_error`` results with a
short message (unknown chat, chat not allowed, bad regex, call limit reached).
"""

import asyncio
import dataclasses
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Protocol, get_args

import regex
from pydantic import BaseModel, ValidationError

from instagraph.api.deps import AnalysisContext
from instagraph.api.routes import (
    chat_awards,
    chat_dynamics,
    chat_media,
    chat_reactions,
    chat_text,
    chats,
    compare,
    messages,
    overview,
    overview_awards,
    overview_dynamics,
    overview_emojis,
    overview_graph,
    people,
    wrapped,
)
from instagraph.errors import ApiError
from instagraph.schemas.chats import ChatSortKey
from instagraph.schemas.common import Bucket, MessageQuery
from instagraph.schemas.people import PeopleSort
from instagraph.schemas.words import TermMode
from instagraph.store.dataset import MessageType

logger = logging.getLogger("instagraph")

#: Rough cap per tool result: ~8,000 tokens at ~4 characters per token.
MAX_RESULT_CHARS = 32_000
MAX_TOOL_CALLS = 15
MAX_PAGE = 50
MAX_CONTEXT = 30
MAX_SAMPLE = 30

#: Keys removed from every result (case-sensitive, any depth).
DROP_KEYS = frozenset(
    {
        "dataset_version",
        "color_index",
        "drilldown",
        "drilldowns",
        "total_drilldown",
        "given_drilldown",
        "received_drilldown",
        "url",
        "thumb_url",
        "uri",
        "source",
        "path",
        "media",
        "sparkline",
        "sparkline_months",
    }
)

NO_SCOPE_MESSAGE = (
    "No chats are shared with the assistant in this conversation. "
    "Ask the user to allow some chats in the chat picker."
)


class Encoder(Protocol):
    """What the tool box needs from the pseudonymizer."""

    def encode_value(self, value: Any) -> Any: ...

    def decode_text(self, text: str) -> str: ...


def decode_args(encoder: Encoder, value: Any) -> Any:
    """Translate placeholders in tool arguments back to real names, so e.g. a text
    search for ``[P3]`` searches for the person's real name."""
    if isinstance(value, str):
        return encoder.decode_text(value)
    if isinstance(value, dict):
        return {k: decode_args(encoder, v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode_args(encoder, v) for v in value]
    return value


class ToolError(Exception):
    """A failure to report to the model as an ``is_error`` tool result."""


# --------------------------------------------------------------------------------------
# Result shaping
# --------------------------------------------------------------------------------------


def compact(value: Any) -> Any:
    """JSON-ready copy of ``value`` without :data:`DROP_KEYS` and null fields."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {
            k: compact(v)
            for k, v in value.items()
            if k not in DROP_KEYS and v is not None and v != [] and v != {}
        }
    if isinstance(value, list | tuple):
        return [compact(v) for v in value]
    return value


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _longest_list(value: Any, best: tuple[int, list[Any] | None]) -> tuple[int, list[Any] | None]:
    if isinstance(value, list):
        if len(value) > best[0]:
            best = (len(value), value)
        for v in value:
            best = _longest_list(v, best)
    elif isinstance(value, dict):
        for v in value.values():
            best = _longest_list(v, best)
    return best


def fit(value: Any, max_chars: int = MAX_RESULT_CHARS) -> tuple[Any, bool]:
    """Shrink ``value`` (a compacted JSON value) to at most ``max_chars``.

    Halves the longest list until it fits; as a last resort returns a cut JSON
    string. Returns ``(value, truncated)``.
    """
    if _size(value) <= max_chars:
        return value, False
    value = json.loads(json.dumps(value))  # private copy: lists are cut in place
    while _size(value) > max_chars:
        n, lst = _longest_list(value, (1, None))
        if lst is None or n <= 1:
            break
        del lst[max(1, n // 2) :]
    if _size(value) > max_chars:
        text = json.dumps(value, ensure_ascii=False)[: max_chars - 200]
        value = {"partial_json": text}
    if isinstance(value, dict):
        value["truncated"] = True
        value["hint"] = "Result was cut to fit. Narrow the date range, page, or use search."
    return value, True


def _summary(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("items", "messages", "people", "chats"):
            if isinstance(value.get(key), list):
                total = value.get("total")
                shown = len(value[key])
                return f"{shown} {key}" + (f" of {total}" if total is not None else "")
        return f"{len(value)} fields"
    if isinstance(value, list):
        return f"{len(value)} items"
    return "ok"


# --------------------------------------------------------------------------------------
# Argument helpers
# --------------------------------------------------------------------------------------


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    out = dict(schema)
    t = out.get("type")
    if isinstance(t, str):
        out["type"] = [t, "null"]
    if "enum" in out:
        out["enum"] = [*out["enum"], None]
    return out


def _schema(required: dict[str, Any], optional: dict[str, Any] | None = None) -> dict[str, Any]:
    """Strict JSON schema: every property listed in ``required``; optional ones are
    nullable (pass ``null`` to leave them out)."""
    props = dict(required)
    for name, s in (optional or {}).items():
        props[name] = _nullable(s)
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


DATE = {"type": "string", "description": "Local date YYYY-MM-DD (inclusive)"}
DATE_RANGE = {"start": DATE, "end": DATE}
CHAT_ID = {"type": "integer", "description": "chat_id from list_chats"}
PERSON_ID = {"type": "integer", "description": "person_id from list_people"}


def _enum(values: Any, description: str = "") -> dict[str, Any]:
    s: dict[str, Any] = {"type": "string", "enum": list(get_args(values))}
    if description:
        s["description"] = description
    return s


def _date(args: dict[str, Any], key: str) -> date | None:
    raw = args.get(key)
    if raw in (None, ""):
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError as exc:
        raise ToolError(f"{key} must be a date like 2024-05-31, got {raw!r}.") from exc


def _int(
    args: dict[str, Any],
    key: str,
    default: int | None = None,
    *,
    lo: int = 0,
    hi: int | None = None,
) -> int | None:
    raw = args.get(key)
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        raise ToolError(f"{key} must be an integer.")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ToolError(f"{key} must be an integer.") from exc
    value = max(lo, value)
    return min(hi, value) if hi is not None else value


def _req_int(args: dict[str, Any], key: str) -> int:
    value = _int(args, key)
    if value is None:
        raise ToolError(f"{key} is required.")
    return value


def _choice(args: dict[str, Any], key: str, values: Any, default: str) -> Any:
    raw = args.get(key)
    if raw is None:
        return default
    allowed = get_args(values)
    if raw not in allowed:
        raise ToolError(f"{key} must be one of {', '.join(map(str, allowed))}.")
    return raw


def _ranged(ctx: AnalysisContext, args: dict[str, Any]) -> AnalysisContext:
    """``ctx`` with the tool's optional ``start``/``end`` date range applied."""
    start, end = _date(args, "start"), _date(args, "end")
    if start is None and end is None:
        return ctx
    if start and end and start > end:
        raise ToolError("start must not be after end.")
    return dataclasses.replace(
        ctx, filters=ctx.filters.model_copy(update={"start": start, "end": end})
    )


# --------------------------------------------------------------------------------------
# Tool handlers
# --------------------------------------------------------------------------------------

OverviewSection = Literal[
    "summary",
    "timeline",
    "sent_received",
    "top_chats",
    "hours",
    "weekdays",
    "years",
    "types",
    "active_chats",
    "new_conversations",
    "reply_times",
    "emojis",
    "awards",
    "social_graph",
]
ChatTab = Literal[
    "summary",
    "timeline",
    "when",
    "dynamics",
    "words",
    "emojis",
    "reactions",
    "calls",
    "group",
    "awards",
]
SampleStrategy = Literal["random", "recent", "longest", "spread"]


def _list_chats(ctx: AnalysisContext, a: dict[str, Any]) -> Any:
    return chats.list_chats(
        _ranged(ctx, a),
        q=a.get("query") or None,
        sort=_choice(a, "sort", ChatSortKey, "messages"),
        order=_choice(a, "order", Literal["asc", "desc"], "desc"),
        offset=_int(a, "offset", 0) or 0,
        limit=_int(a, "limit", 25, lo=1, hi=MAX_PAGE) or 25,
    )


def _get_overview(ctx: AnalysisContext, a: dict[str, Any]) -> Any:
    ctx = _ranged(ctx, a)
    section = _choice(a, "section", OverviewSection, "summary")
    bucket = _choice(a, "bucket", Bucket, "month")
    calls: dict[str, Callable[[], Any]] = {
        "summary": lambda: overview.get_overview(ctx),
        "timeline": lambda: overview.get_timeline(ctx, bucket=bucket),
        "sent_received": lambda: overview.get_sent_received(ctx, bucket=bucket),
        "top_chats": lambda: overview.get_contacts(ctx, metric="messages", limit=25),
        "hours": lambda: overview.get_hours(ctx),
        "weekdays": lambda: overview.get_weekdays(ctx),
        "years": lambda: overview.get_years(ctx),
        "types": lambda: overview.get_types(ctx),
        "active_chats": lambda: overview.get_active_chats(ctx),
        "new_conversations": lambda: overview.get_new_conversations(ctx),
        "reply_times": lambda: overview_dynamics.overview_reply_times(ctx, bucket=bucket),
        "emojis": lambda: overview_emojis.overview_emojis(ctx),
        "awards": lambda: overview_awards.overview_awards(ctx),
        "social_graph": lambda: overview_graph.overview_graph(
            ctx, include_owner=False, max_nodes=50
        ),
    }
    return calls[section]()


def _get_chat_stats(ctx: AnalysisContext, a: dict[str, Any]) -> Any:
    ctx = _ranged(ctx, a)
    cid = _req_int(a, "chat_id")
    tab = _choice(a, "tab", ChatTab, "summary")
    calls: dict[str, Callable[[], Any]] = {
        "summary": lambda: chats.get_chat_summary(ctx, cid),
        "timeline": lambda: chats.get_chat_timeline(ctx, cid, bucket="month"),
        "when": lambda: chats.get_chat_when(ctx, cid),
        "dynamics": lambda: chat_dynamics.chat_dynamics(cid, ctx),
        "words": lambda: chat_text.chat_words(cid, ctx, accent_insensitive=False),
        "emojis": lambda: chat_text.chat_emojis(cid, ctx, bucket=None),
        "reactions": lambda: chat_reactions.chat_reactions(cid, ctx),
        "calls": lambda: chat_media.chat_calls(cid, ctx),
        "group": lambda: chat_media.chat_group(cid, ctx, bucket="month"),
        "awards": lambda: chat_awards.chat_awards(cid, ctx),
    }
    return calls[tab]()


def _track_terms(ctx: AnalysisContext, a: dict[str, Any]) -> Any:
    terms = [str(t) for t in (a.get("terms") or []) if str(t).strip()]
    if not terms:
        raise ToolError("Give at least one term.")
    return chat_text.chat_terms(
        _req_int(a, "chat_id"),
        _ranged(ctx, a),
        q=terms,
        mode=_choice(a, "mode", TermMode, "word"),
        case_sensitive=False,
        accent_insensitive=True,
        bucket=None,
    )


def _list_people(ctx: AnalysisContext, a: dict[str, Any]) -> Any:
    return people.list_people(
        _ranged(ctx, a),
        q=a.get("query") or None,
        sort=_choice(a, "sort", PeopleSort, "exchanged"),
        order=_choice(a, "order", Literal["asc", "desc"], "desc"),
        offset=_int(a, "offset", 0) or 0,
        limit=_int(a, "limit", 25, lo=1, hi=MAX_PAGE) or 25,
    )


def _get_person(ctx: AnalysisContext, a: dict[str, Any]) -> Any:
    return people.person_detail(_req_int(a, "person_id"), _ranged(ctx, a))


def _compare(ctx: AnalysisContext, a: dict[str, Any]) -> Any:
    chat_ids = a.get("chat_ids") or []
    person_ids = a.get("person_ids") or []
    if bool(chat_ids) == bool(person_ids):
        raise ToolError("Pass either chat_ids or person_ids (2-4 ids), not both.")
    ids = chat_ids or person_ids
    if not 2 <= len(ids) <= 4:
        raise ToolError("Compare needs 2-4 ids.")
    joined = ",".join(str(int(i)) for i in ids)
    return compare.compare(
        _ranged(ctx, a),
        chats=joined if chat_ids else None,
        people=joined if person_ids else None,
    )


def _search_messages(ctx: AnalysisContext, a: dict[str, Any]) -> Any:
    types = a.get("types") or None
    if types is not None:
        allowed = set(get_args(MessageType))
        bad = [t for t in types if t not in allowed]
        if bad:
            raise ToolError(f"Unknown message types: {bad}.")
    text_mode = a.get("text_mode") or None
    if text_mode == "regex" and a.get("text"):
        try:
            regex.compile(str(a["text"]))
        except regex.error as exc:
            raise ToolError(f"Invalid regex: {exc}") from exc
    try:
        q = MessageQuery(
            thread_id=_int(a, "chat_id"),
            sender_id=_int(a, "sender_id"),
            start=_date(a, "start"),
            end=_date(a, "end"),
            types=types,
            hour=_int(a, "hour", lo=0, hi=23),
            weekday=_int(a, "weekday", lo=1, hi=7),
            text=a.get("text") or None,
            text_mode=text_mode,
            accent_insensitive=True if a.get("text") else None,
            has_reactions=a.get("has_reactions"),
        )
    except ValidationError as exc:
        raise ToolError(f"Invalid search: {exc.errors()[0].get('msg', exc)}") from exc
    return messages.search_messages(
        ctx,
        q,
        cursor=a.get("cursor") or None,
        direction="after",
        order=_choice(a, "order", Literal["asc", "desc"], "desc"),
        limit=_int(a, "limit", 20, lo=1, hi=MAX_PAGE) or 20,
        include_stats=False,
    )


def _get_message_context(ctx: AnalysisContext, a: dict[str, Any]) -> Any:
    return messages.message_context(
        ctx,
        _req_int(a, "message_id"),
        before=_int(a, "before", 10, hi=MAX_CONTEXT) or 0,
        after=_int(a, "after", 10, hi=MAX_CONTEXT) or 0,
    )


def _sample_messages(ctx: AnalysisContext, a: dict[str, Any]) -> Any:
    cid, sid = _int(a, "chat_id"), _int(a, "sender_id")
    if cid is None and sid is None:
        raise ToolError("Give chat_id or sender_id.")
    if cid is not None:
        ctx.ensure_chat(cid)
    strategy = _choice(a, "strategy", SampleStrategy, "spread")
    n = _int(a, "n", 10, lo=1, hi=MAX_SAMPLE) or 10
    q = MessageQuery(
        thread_id=cid, sender_id=sid, start=_date(a, "start"), end=_date(a, "end"), types=["text"]
    )
    ids: list[int] = [int(i) for i in messages.matches(ctx, q)["message_id"].to_list()]
    total = len(ids)
    if strategy == "recent":
        picked = ids[-n:]
    elif strategy == "random":
        import random

        picked = sorted(random.Random(_int(a, "seed", 0)).sample(ids, min(n, total)))
    elif strategy == "longest":
        import polars as pl

        lengths = (
            ctx.dataset.messages.filter(pl.col("message_id").is_in(ids))
            .select("message_id", "char_count")
            .sort(["char_count", "message_id"], descending=[True, False])
            .head(n)
            .collect()
        )
        picked = sorted(int(i) for i in lengths["message_id"].to_list())
    else:  # spread evenly over time
        step = max(1, total // n) if total else 1
        picked = ids[::step][:n]
    return {
        "strategy": strategy,
        "total_matching": total,
        "items": messages.build_items(ctx, picked),
    }


def _get_wrapped(ctx: AnalysisContext, a: dict[str, Any]) -> Any:
    return wrapped.wrapped(ctx, year=_int(a, "year", lo=1970, hi=2100), chat=_int(a, "chat_id"))


# --------------------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[AnalysisContext, dict[str, Any]], Any]

    def definition(self) -> dict[str, Any]:
        """Provider-neutral definition: name, description, JSON schema."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "list_chats",
        "List the chats you may read, with headline numbers per chat (messages, my share, "
        "first/last message, duration, average per day, median reply times, emojis, calls, "
        "trend). Use it first to find chat_id values.",
        _schema(
            {},
            {
                "query": {"type": "string", "description": "Search titles and participant names"},
                "sort": _enum(ChatSortKey),
                "order": {"type": "string", "enum": ["asc", "desc"]},
                "limit": {"type": "integer", "description": f"1-{MAX_PAGE}, default 25"},
                "offset": {"type": "integer"},
                **DATE_RANGE,
            },
        ),
        _list_chats,
    ),
    ToolSpec(
        "get_overview",
        "Totals and one overview section, computed over the allowed chats only. Sections: "
        "summary (headline tiles), timeline, sent_received, top_chats, hours, weekdays, years, "
        "types (message types), active_chats, new_conversations, reply_times (my median reply "
        "time and others' to me per bucket), emojis, awards, social_graph.",
        _schema(
            {"section": _enum(OverviewSection)},
            {"bucket": _enum(Bucket, "Time bucket for timelines (default month)"), **DATE_RANGE},
        ),
        _get_overview,
    ),
    ToolSpec(
        "get_chat_stats",
        "One stats tab of one chat, the same data as the chat's page in the app. Tabs: "
        "summary, timeline, when (hours/weekdays), dynamics (initiations, reply times, double "
        "texts, sessions), words, emojis, reactions, calls, group (group chats only), awards. "
        "Reply times are medians in milliseconds; ratios are 0..1.",
        _schema({"chat_id": CHAT_ID, "tab": _enum(ChatTab)}, dict(DATE_RANGE)),
        _get_chat_stats,
    ),
    ToolSpec(
        "track_terms",
        "How often words or phrases are used over time in one chat, per person, with first "
        "and last use.",
        _schema(
            {
                "chat_id": CHAT_ID,
                "terms": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "1-10 terms",
                },
            },
            {"mode": _enum(TermMode, "word (default), plain substring, or regex"), **DATE_RANGE},
        ),
        _track_terms,
    ),
    ToolSpec(
        "list_people",
        "People in the allowed chats with messages exchanged, messages they sent, chats in "
        "common and last contact.",
        _schema(
            {},
            {
                "query": {"type": "string"},
                "sort": _enum(PeopleSort),
                "order": {"type": "string", "enum": ["asc", "desc"]},
                "limit": {"type": "integer", "description": f"1-{MAX_PAGE}, default 25"},
                "offset": {"type": "integer"},
                **DATE_RANGE,
            },
        ),
        _list_people,
    ),
    ToolSpec(
        "get_person",
        "One person's stats across the allowed chats (activity, reply times, chats in common).",
        _schema({"person_id": PERSON_ID}, dict(DATE_RANGE)),
        _get_person,
    ),
    ToolSpec(
        "compare",
        "Compare 2-4 chats or 2-4 people side by side. Pass exactly one of chat_ids or person_ids.",
        _schema(
            {},
            {
                "chat_ids": {"type": "array", "items": {"type": "integer"}},
                "person_ids": {"type": "array", "items": {"type": "integer"}},
                **DATE_RANGE,
            },
        ),
        _compare,
    ),
    ToolSpec(
        "search_messages",
        f"Search messages in the allowed chats. Returns up to {MAX_PAGE} messages per page "
        "(newest first by default) with total matches and a next_cursor for paging. Text "
        "search ignores accents.",
        _schema(
            {},
            {
                "text": {"type": "string"},
                "text_mode": {"type": "string", "enum": ["plain", "word", "regex"]},
                "chat_id": CHAT_ID,
                "sender_id": PERSON_ID,
                "types": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(get_args(MessageType))},
                },
                "hour": {"type": "integer", "description": "Local hour 0-23"},
                "weekday": {"type": "integer", "description": "1 = Monday ... 7 = Sunday"},
                "has_reactions": {"type": "boolean"},
                "order": {"type": "string", "enum": ["asc", "desc"]},
                "limit": {"type": "integer", "description": f"1-{MAX_PAGE}, default 20"},
                "cursor": {"type": "string", "description": "next_cursor from a previous page"},
                **DATE_RANGE,
            },
        ),
        _search_messages,
    ),
    ToolSpec(
        "get_message_context",
        f"Up to {MAX_CONTEXT} messages before and after one message, in its chat.",
        _schema(
            {"message_id": {"type": "integer"}},
            {
                "before": {"type": "integer", "description": f"0-{MAX_CONTEXT}, default 10"},
                "after": {"type": "integer", "description": f"0-{MAX_CONTEXT}, default 10"},
            },
        ),
        _get_message_context,
    ),
    ToolSpec(
        "sample_messages",
        f"Up to {MAX_SAMPLE} text messages from one chat and/or sender, picked by strategy: "
        "spread (evenly over time, default), recent, longest, random. Good for examples and "
        "for reading a chat's general tone.",
        _schema(
            {},
            {
                "chat_id": CHAT_ID,
                "sender_id": PERSON_ID,
                "strategy": _enum(SampleStrategy),
                "n": {"type": "integer", "description": f"1-{MAX_SAMPLE}, default 10"},
                "seed": {"type": "integer", "description": "For random"},
                **DATE_RANGE,
            },
        ),
        _sample_messages,
    ),
    ToolSpec(
        "get_wrapped",
        "Year-in-review recap for one year (default: latest), overall or for one chat.",
        _schema({}, {"year": {"type": "integer"}, "chat_id": CHAT_ID}),
        _get_wrapped,
    ),
)

TOOLS_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in TOOLS}


@dataclass
class ToolResult:
    """One tool call's outcome, ready to send back to the model."""

    content: Any
    is_error: bool = False
    truncated: bool = False
    summary: str = ""

    def as_text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        return json.dumps(self.content, ensure_ascii=False, separators=(",", ":"))


@dataclass
class ToolBox:
    """The tools of one conversation turn: scope, names, size cap and call limit.

    ``ctx`` is ``None`` when no chats are allowed; every call then returns an error.
    """

    ctx: AnalysisContext | None
    encoder: Encoder | None = None
    max_calls: int = MAX_TOOL_CALLS
    calls_made: int = field(default=0, init=False)
    log: list[dict[str, Any]] = field(default_factory=list, init=False)
    """Every call of this turn as the model saw it: name, args, result, is_error."""
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def definitions(self) -> list[dict[str, Any]]:
        return [t.definition() for t in TOOLS]

    def call(self, name: str, args: dict[str, Any] | None) -> ToolResult:
        """Run one tool synchronously and record it in :attr:`log` (CPU-bound; async
        providers use :meth:`acall`). Thread-safe."""
        with self._lock:
            result = self._run(name, args)
            self.log.append(
                {
                    "name": name,
                    "args": dict(args or {}),
                    "result": result.content,
                    "is_error": result.is_error,
                }
            )
        return result

    def _run(self, name: str, args: dict[str, Any] | None) -> ToolResult:
        self.calls_made += 1
        if self.calls_made > self.max_calls:
            return self._error(
                f"Tool call limit ({self.max_calls}) reached for this answer. "
                "Answer with what you have."
            )
        spec = TOOLS_BY_NAME.get(name)
        if spec is None:
            return self._error(f"Unknown tool {name!r}.")
        if self.ctx is None:
            return self._error(NO_SCOPE_MESSAGE)
        real_args = dict(args or {})
        if self.encoder is not None:
            real_args = decode_args(self.encoder, real_args)
        try:
            raw = spec.handler(self.ctx, real_args)
        except ToolError as exc:
            return self._error(str(exc))
        except ApiError as exc:
            if exc.status == 404:
                return self._error("Not found, or not in the chats you may read.")
            return self._error(exc.message)
        except Exception:  # never let one bad call end the turn
            logger.exception("AI tool %s failed", name)
            return self._error(f"The {name} tool failed on these arguments. Try others.")
        value, truncated = fit(compact(raw))
        if self.encoder is not None:
            value = self.encoder.encode_value(value)
        return ToolResult(content=value, truncated=truncated, summary=_summary(value))

    async def acall(self, name: str, args: dict[str, Any] | None) -> ToolResult:
        return await asyncio.to_thread(self.call, name, args)

    def _error(self, message: str) -> ToolResult:
        if self.encoder is not None:
            message = self.encoder.encode_value(message)
        return ToolResult(content=message, is_error=True, summary=message[:120])
