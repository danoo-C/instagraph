"""Message Browser endpoints (spec §5.9, §8.2).

* ``GET /api/messages`` — search + keyset pagination (+ ``include_stats``)
* ``GET /api/messages/random`` — one random message matching the filters
* ``GET /api/messages/on-this-day`` — same month-day in earlier years
* ``GET /api/messages/{message_id}/context`` — messages around one message

The search query parameters mirror :class:`~instagraph.schemas.common.MessageQuery`
(``start``/``end`` are the global filter dates), so every drilldown from other
endpoints can be sent as-is. List parameters accept repeated keys and/or
comma-separated values (``types=text,photo`` == ``types=text&types=photo``).
"""

from collections import defaultdict
from datetime import date, datetime
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

import polars as pl
from fastapi import APIRouter, Depends, Path, Query
from pydantic import ValidationError

from instagraph.analysis import search
from instagraph.api.deps import AnalysisContext, Context
from instagraph.api.filters import filtered_messages
from instagraph.errors import ERROR_RESPONSES, NotFoundError, ValidationApiError
from instagraph.schemas.common import MessageQuery, Series, TimeSeries
from instagraph.schemas.messages import (
    Attachments,
    CallInfo,
    MediaItem,
    MessageContext,
    MessageItem,
    MessagePage,
    OnThisDay,
    OnThisDayYear,
    PersonCount,
    RandomMessage,
    ReactionItem,
    SearchStats,
    ShareInfo,
    SortOrder,
)
from instagraph.store.cache import ResponseCache, make_key

router = APIRouter(prefix="/api/messages", tags=["messages"], responses=ERROR_RESPONSES)

#: Result sets can be large (2M ids), so they get their own small LRU.
_match_cache = ResponseCache(maxsize=8)


# --------------------------------------------------------------------------------------
# Query parsing
# --------------------------------------------------------------------------------------


def _split(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    return [p.strip() for v in values for p in v.split(",") if p.strip()]


def _split_ints(name: str, values: list[str] | None) -> list[int] | None:
    parts = _split(values)
    if parts is None:
        return None
    try:
        return [int(p) for p in parts]
    except ValueError as exc:
        raise ValidationApiError(f"{name} must be a list of integers") from exc


def get_message_query(
    thread_id: int | None = None,
    thread_ids: Annotated[list[str] | None, Query()] = None,
    sender_id: int | None = None,
    sender_ids: Annotated[list[str] | None, Query()] = None,
    types: Annotated[list[str] | None, Query(description="Message types")] = None,
    hour: Annotated[int | None, Query(ge=0, le=23)] = None,
    weekday: Annotated[int | None, Query(ge=1, le=7, description="ISO 1=Mon")] = None,
    month: Annotated[int | None, Query(ge=1, le=12)] = None,
    emoji: str | None = None,
    emoticon: str | None = None,
    text: Annotated[str | None, Query(max_length=search.MAX_TEXT_LEN)] = None,
    text_mode: Literal["plain", "word", "regex"] | None = None,
    case_sensitive: bool | None = None,
    accent_insensitive: bool | None = None,
    has_reactions: bool | None = None,
    reaction_emoji: str | None = None,
    reactor_id: int | None = None,
    share_kind: str | None = None,
    share_owner: str | None = None,
    link_domain: str | None = None,
    call_kind: str | None = None,
    call_missed: bool | None = None,
    system_event: str | None = None,
    session_id: int | None = None,
    message_ids: Annotated[list[str] | None, Query()] = None,
) -> MessageQuery:
    """Message Browser filters (= ``MessageQuery`` minus ``start``/``end``)."""
    data: dict[str, Any] = {
        "thread_id": thread_id,
        "thread_ids": _split_ints("thread_ids", thread_ids),
        "sender_id": sender_id,
        "sender_ids": _split_ints("sender_ids", sender_ids),
        "types": _split(types),
        "hour": hour,
        "weekday": weekday,
        "month": month,
        "emoji": emoji or None,
        "emoticon": emoticon or None,
        "text": text or None,
        "text_mode": text_mode,
        "case_sensitive": case_sensitive,
        "accent_insensitive": accent_insensitive,
        "has_reactions": has_reactions,
        "reaction_emoji": reaction_emoji or None,
        "reactor_id": reactor_id,
        "share_kind": share_kind or None,
        "share_owner": share_owner or None,
        "link_domain": link_domain or None,
        "call_kind": call_kind or None,
        "call_missed": call_missed,
        "system_event": system_event or None,
        "session_id": session_id,
        "message_ids": _split_ints("message_ids", message_ids),
    }
    try:
        q = MessageQuery.model_validate(data)
    except ValidationError as exc:
        err = exc.errors()[0]
        loc = ".".join(str(p) for p in err.get("loc", ()))
        raise ValidationApiError(f"{loc}: {err.get('msg', 'invalid')}") from exc
    search.validate_query(q)
    return q


QueryDep = Annotated[MessageQuery, Depends(get_message_query)]


# --------------------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------------------


def _emoji_options(ctx: AnalysisContext) -> search.EmojiOptions:
    return search.EmojiOptions(
        merge_skin_tones=ctx.settings.emoji_merge_skin_tones,
        merge_gendered=ctx.settings.emoji_merge_gendered,
    )


def _base_messages(ctx: AnalysisContext, q: MessageQuery) -> pl.LazyFrame:
    """Globally filtered messages; asking for system messages lifts ``exclude_system``."""
    f = ctx.filters
    wants_system = q.system_event is not None or (q.types is not None and "system" in q.types)
    if wants_system and f.exclude_system:
        f = f.model_copy(update={"exclude_system": False})
    return filtered_messages(ctx.dataset, f)


def matches(ctx: AnalysisContext, q: MessageQuery) -> pl.DataFrame:
    """Cached result set (``message_id, sender_id, local_date`` sorted by id)."""
    key = make_key(
        ctx.dataset_version,
        "messages.matches",
        ctx.filters.cache_key(),
        ctx.settings.settings_hash(),
        q.model_dump_json(),
    )
    return _match_cache.get_or_compute(
        key,
        lambda: search.match_frame(
            _base_messages(ctx, q),
            q,
            reactions=ctx.dataset.reactions,
            emoji=_emoji_options(ctx),
        ),
    )


def thread_titles(ctx: AnalysisContext, thread_ids: list[int]) -> dict[int, str]:
    """``ctx.thread_title`` for many threads at once."""
    return {tid: ctx.thread_title(tid) for tid in thread_ids}


def build_items(ctx: AnalysisContext, ids: list[int]) -> list[MessageItem]:
    """Full :class:`MessageItem` objects for ``ids`` (in that order), anonymize-aware."""
    if not ids:
        return []
    ds = ctx.dataset
    rows, reactions, media = search.fetch_page(ds.messages, ds.reactions, ds.media, ids, ctx.tz)
    assert reactions is not None and media is not None
    reacts: dict[int, list[ReactionItem]] = defaultdict(list)
    for r in reactions.iter_rows(named=True):
        reacts[r["message_id"]].append(
            ReactionItem(emoji=r["emoji"], actor=ctx.person_ref(r["actor_id"]))
        )
    medias: dict[int, list[MediaItem]] = defaultdict(list)
    for m in media.iter_rows(named=True):
        medias[m["message_id"]].append(
            MediaItem(media_id=m["media_id"], kind=m["kind"], exists=bool(m["exists"]))
        )
    titles = thread_titles(ctx, rows["thread_id"].unique().to_list())
    anon = ctx.settings.anonymize
    items: list[MessageItem] = []
    for row in rows.iter_rows(named=True):
        tid = row["thread_id"]
        mtype = row["type"]
        share = None
        if mtype == "share" or row["share_link"] or row["share_kind"]:
            share = ShareInfo(
                kind=row["share_kind"],
                link=None if anon else row["share_link"],
                text=None if anon else row["share_text"],
                owner=None if anon else row["share_owner"],
            )
        call = None
        if mtype == "call":
            call = CallInfo(
                kind=row["call_kind"],
                duration_s=row["call_duration_s"],
                missed=bool(row["call_missed"]),
            )
        items.append(
            MessageItem(
                message_id=row["message_id"],
                thread_id=tid,
                thread_title=titles[tid],
                sender=ctx.person_ref(row["sender_id"]),
                ts=row["local_ts"],
                type=mtype,
                text=ctx.text(row["text"]),
                links=[] if anon else list(row["links"] or []),
                reactions=reacts.get(row["message_id"], []),
                attachments=Attachments(
                    photos=row["n_photos"],
                    videos=row["n_videos"],
                    audios=row["n_audios"],
                    gifs=row["n_gifs"],
                    stickers=row["n_stickers"],
                    media=medias.get(row["message_id"], []),
                ),
                share=share,
                call=call,
                system_event=row["system_event"],
                system_payload=None if anon else row["system_payload"],
            )
        )
    return items


def _cursor_for(item: MessageItem) -> str:
    return search.encode_cursor(int(item.ts.timestamp() * 1000), item.message_id)


def _stats(ctx: AnalysisContext, q: MessageQuery, df: pl.DataFrame) -> SearchStats:
    def compute() -> SearchStats:
        bucket, xs, values = search.match_timeline(df)
        timeline = TimeSeries(
            bucket=bucket,
            x=xs,
            series=[Series(key="matches", name="Matches", values=[float(v) for v in values])],
            unit="count",
        )
        per_person = []
        for pid, n in search.per_person_counts(df):
            drill = q.model_copy(update={"sender_id": pid, "sender_ids": None})
            per_person.append(PersonCount(person=ctx.person_ref(pid), count=n, drilldown=drill))
        return SearchStats(timeline=timeline, per_person=per_person)

    return ctx.cached("messages.stats", compute, extra=q.model_dump_json())


# --------------------------------------------------------------------------------------
# Routes (static paths BEFORE /{message_id})
# --------------------------------------------------------------------------------------


@router.get("", response_model=MessagePage)
def search_messages(
    ctx: Context,
    q: QueryDep,
    cursor: Annotated[str | None, Query(description="Opaque cursor from a previous page")] = None,
    direction: Annotated[
        Literal["after", "before"],
        Query(description="'after' = next page (next_cursor), 'before' = prev_cursor"),
    ] = "after",
    order: Annotated[SortOrder, Query(description="desc = newest first")] = "desc",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    include_stats: bool = False,
) -> MessagePage:
    """Search messages (global filters + Message Browser filters), keyset-paginated."""
    df = matches(ctx, q)
    cursor_id = search.decode_cursor(cursor)[1] if cursor else None
    page = search.page_slice(
        df["message_id"], limit=limit, order=order, cursor_id=cursor_id, direction=direction
    )
    items = build_items(ctx, page.ids)
    return MessagePage(
        dataset_version=ctx.dataset_version,
        items=items,
        total=df.height,
        order=order,
        next_cursor=_cursor_for(items[-1]) if items and page.has_next else None,
        prev_cursor=_cursor_for(items[0]) if items and page.has_prev else None,
        has_next=page.has_next,
        has_prev=page.has_prev,
        stats=_stats(ctx, q, df) if include_stats else None,
    )


@router.get("/random", response_model=RandomMessage)
def random_message(
    ctx: Context,
    q: QueryDep,
    seed: Annotated[int | None, Query(description="For reproducible picks")] = None,
) -> RandomMessage:
    """One random message matching the filters (``message`` null if none)."""
    mid = search.random_id(matches(ctx, q)["message_id"], seed)
    items = build_items(ctx, [mid]) if mid is not None else []
    return RandomMessage(dataset_version=ctx.dataset_version, message=items[0] if items else None)


@router.get("/on-this-day", response_model=OnThisDay)
def messages_on_this_day(
    ctx: Context,
    q: QueryDep,
    day: Annotated[
        date | None, Query(alias="date", description="Default: today in the display tz")
    ] = None,
    limit: Annotated[int, Query(ge=0, le=200, description="Messages per year")] = 20,
) -> OnThisDay:
    """Messages from the same month-day in earlier years (local dates in ``tz``)."""
    today = day or datetime.now(ZoneInfo(ctx.tz)).date()
    by_year = search.on_this_day(matches(ctx, q), today)
    years: list[OnThisDayYear] = []
    for year in sorted(by_year, reverse=True):
        ids = by_year[year]
        d = date(year, today.month, today.day)
        years.append(
            OnThisDayYear(
                year=year,
                date=d,
                years_ago=today.year - year,
                count=len(ids),
                messages=build_items(ctx, ids[:limit]),
                drilldown=q.model_copy(update={"start": d, "end": d}),
            )
        )
    return OnThisDay(dataset_version=ctx.dataset_version, date=today, years=years)


@router.get("/{message_id}/context", response_model=MessageContext)
def message_context(
    ctx: Context,
    message_id: Annotated[int, Path(ge=0)],
    before: Annotated[int, Query(ge=0, le=500)] = 25,
    after: Annotated[int, Query(ge=0, le=500)] = 25,
) -> MessageContext:
    """``before``/``after`` messages around one message in its thread.

    Ignores the thread/people/date filters (it shows the real conversation); system
    messages are hidden when ``exclude_system`` (except the target itself).
    """
    sl = search.context_slice(
        ctx.dataset.messages,
        message_id,
        before=before,
        after=after,
        exclude_system=ctx.filters.exclude_system,
    )
    if sl is None:
        raise NotFoundError(f"Message {message_id} not found.")
    return MessageContext(
        dataset_version=ctx.dataset_version,
        thread_id=sl.thread_id,
        thread_title=ctx.thread_title(sl.thread_id),
        target_id=message_id,
        target_index=sl.target_index,
        items=build_items(ctx, sl.ids),
        has_more_before=sl.has_more_before,
        has_more_after=sl.has_more_after,
    )
