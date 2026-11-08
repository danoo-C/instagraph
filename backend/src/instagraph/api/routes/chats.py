"""Chats list, chat header and the Summary / Timeline / When tabs (spec §5.5, §5.6, §7.2).

Other chat tabs (dynamics, words, …) live in their own modules under the same
``/api/chats`` prefix. The list applies the thread-category filters; chat-scoped
endpoints use ``ctx.for_chat`` (category filters ignored — an opened chat always shows
its data; date/tz/system/people filters still apply).
"""

import unicodedata
from collections.abc import Hashable, Sequence
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

import polars as pl
from fastapi import APIRouter, Path, Query

from instagraph.analysis import counts as C
from instagraph.analysis import streaks as S
from instagraph.analysis import time as T
from instagraph.analysis import trends as TR
from instagraph.api.deps import AnalysisContext, Context
from instagraph.api.filters import filtered_messages, is_counted_message, is_text_message
from instagraph.api.routes.overview import (
    HOUR_LABELS,
    MONTH_LABELS,
    WEEKDAY_LABELS,
    BucketQuery,
    calendar_data,
    chat_kind,
    counted_messages,
    message_refs,
    streak_info,
    thread_title,
    today_in,
    vals,
    weekday_hour_heatmap,
)
from instagraph.errors import ERROR_RESPONSES
from instagraph.schemas.chats import (
    ChatHeaderResponse,
    ChatListItem,
    ChatListResponse,
    ChatPersonRow,
    ChatSortKey,
    ChatSummaryResponse,
    ChatTimelineResponse,
    ChatWhenResponse,
    DayCount,
    HourCount,
    LifecycleOut,
    PersonHeatmap,
    PersonWhenStats,
    SilenceInfo,
    TimelineEvent,
)
from instagraph.schemas.common import (
    CategoryChart,
    DateRange,
    HeatmapData,
    MessageQuery,
    Series,
    Tile,
    TimeSeries,
)

router = APIRouter(prefix="/api/chats", tags=["chats"], responses=ERROR_RESPONSES)

ChatId = Annotated[int, Path(ge=0, description="thread_id")]
EVENT_SYSTEM = ("rename", "join", "leave", "removed")
N_SILENCES = 3


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def _fold(s: str) -> str:
    """Lowercase + strip accents (search is accent-insensitive)."""
    nfkd = unicodedata.normalize("NFKD", s.casefold())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _thread(ctx: AnalysisContext, chat_id: int) -> dict[str, Any]:
    return ctx.ensure_chat(chat_id)


def _chat_counted(ctx: AnalysisContext, chat_id: int) -> pl.LazyFrame:
    return ctx.chat_messages(chat_id).filter(is_counted_message())


def _people_order(thread: dict[str, Any], by_sender: pl.DataFrame) -> list[int]:
    """Participants and senders, by messages desc then id."""
    n = dict(
        zip(
            (int(s) for s in by_sender["sender_id"].to_list()),
            (int(m) for m in by_sender["messages"].to_list()),
            strict=True,
        )
    )
    ids = {int(p) for p in thread["participants"]} | set(n)
    return sorted(ids, key=lambda p: (-n.get(p, 0), p))


def _person_series(
    ctx: AnalysisContext, people: Sequence[int], data: dict[Hashable, list[float]], size: int
) -> list[Series]:
    zeros = [0.0] * size
    out = []
    for p in people:
        ref = ctx.person_ref(p)
        out.append(
            Series(
                key=str(p),
                name=ref.name,
                person_id=p,
                color_index=ref.color_index,
                values=vals(data.get(p, zeros)),
            )
        )
    return out


def _local_date(ctx: AnalysisContext, ts: datetime) -> date:
    return ts.astimezone(ZoneInfo(ctx.tz)).date()


def _global_end(ctx: AnalysisContext) -> date | None:
    return T.date_span(counted_messages(ctx))[1]


def _monthly_to(lf: pl.LazyFrame, end: date | None) -> T.Bucketed:
    start, last = T.date_span(lf)
    return T.bucket_values(lf, "month", start=start, end=end or last)


# --------------------------------------------------------------------------------------
# List
# --------------------------------------------------------------------------------------


def _list_rows(ctx: AnalysisContext) -> tuple[list[ChatListItem], list[str], date | None]:
    owner = ctx.owner_id if ctx.owner_id is not None else -1
    counted = counted_messages(ctx)
    start, ref = T.date_span(counted)
    if ref is None:
        return [], [], None
    since = ref - timedelta(days=29)
    mine = pl.col("sender_id") == owner
    lat = pl.col("reply_latency_ms")
    extra = (
        counted.group_by("thread_id")
        .agg(
            mine.sum().alias("mine"),
            pl.col("ts").min().alias("first_ts"),
            pl.col("ts").max().alias("last_ts"),
            pl.col("local_date").min().alias("first_d"),
            pl.col("local_date").max().alias("last_d"),
            (pl.col("local_date") >= since).sum().alias("last30"),
            lat.filter(mine).median().alias("rep_me"),
            lat.filter(~mine).median().alias("rep_them"),
        )
        .collect()
    )
    base = C.counts_by(counted, "thread_id").join(extra, on="thread_id")
    monthly = T.bucket_values(counted, "month", by="thread_id", start=start, end=ref)
    threads = {int(r["thread_id"]): r for r in ctx.dataset.threads_df().iter_rows(named=True)}
    rows: list[ChatListItem] = []
    for r in base.iter_rows(named=True):
        tid = int(r["thread_id"])
        th = threads[tid]
        spark = monthly.series.get(tid, [0.0] * len(monthly.x))
        label, slope = TR.trend(spark)
        days = (r["last_d"] - r["first_d"]).days + 1
        parts = [ctx.person_ref(int(p)) for p in th["participants"]]
        rows.append(
            ChatListItem(
                thread_id=tid,
                title=thread_title(ctx, th),
                kind=chat_kind(str(th["category"]), bool(th["is_group"])),
                category=th["category"],
                is_group=bool(th["is_group"]),
                participants=parts,
                n_participants=len(parts),
                messages=int(r["messages"]),
                my_share=C.share(int(r["mine"]), int(r["messages"])),
                messages_last_30d=int(r["last30"]),
                first_ts=r["first_ts"],
                last_ts=r["last_ts"],
                duration_days=days,
                avg_per_day=int(r["messages"]) / days,
                median_reply_me_ms=r["rep_me"],
                median_reply_them_ms=r["rep_them"],
                emojis=int(r["emojis"]),
                calls=int(r["calls"]),
                sparkline=[int(v) for v in spark],
                trend=label,
                trend_slope=slope,
                drilldown=MessageQuery(thread_id=tid),
            )
        )
    return rows, monthly.labels(), ref


def _sort_value(item: ChatListItem, key: ChatSortKey) -> Any:
    if key == "title":
        return _fold(item.title)
    if key == "participants":
        return item.n_participants
    return getattr(item, key)


@router.get("", response_model=ChatListResponse)
def list_chats(
    ctx: Context,
    q: Annotated[str | None, Query(description="Search in titles and participant names")] = None,
    sort: ChatSortKey = "messages",
    order: Literal["asc", "desc"] = "desc",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> ChatListResponse:
    """All chats with ≥ 1 message in scope: sortable, searchable, paginated (§5.5)."""
    rows, months, ref = ctx.cached("chats/rows", lambda: _list_rows(ctx))
    if q and q.strip():
        needle = _fold(q.strip())
        rows = [
            r
            for r in rows
            if needle in _fold(r.title) or any(needle in _fold(p.name) for p in r.participants)
        ]
    present = [r for r in rows if _sort_value(r, sort) is not None]
    missing = [r for r in rows if _sort_value(r, sort) is None]
    present.sort(key=lambda r: r.thread_id)
    present.sort(key=lambda r: _sort_value(r, sort), reverse=order == "desc")
    ordered = present + missing  # nulls last in both directions
    return ChatListResponse(
        dataset_version=ctx.dataset_version,
        total=len(ordered),
        offset=offset,
        limit=limit,
        sort=sort,
        order=order,
        reference_date=ref,
        sparkline_months=months,
        items=ordered[offset : offset + limit],
    )


# --------------------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------------------


@router.get("/{chat_id}", response_model=ChatHeaderResponse)
def get_chat(ctx: Context, chat_id: ChatId) -> ChatHeaderResponse:
    """Chat header: title, participants, span, totals, my share."""
    ctx = ctx.for_chat(chat_id)

    def compute() -> ChatHeaderResponse:
        th = _thread(ctx, chat_id)
        owner = ctx.owner_id
        counted = _chat_counted(ctx, chat_id)
        df = counted.select(
            pl.len().alias("n"),
            (pl.col("sender_id") == (owner if owner is not None else -1)).sum().alias("mine"),
            pl.col("ts").min().alias("a"),
            pl.col("ts").max().alias("b"),
            pl.col("local_date").min().alias("da"),
            pl.col("local_date").max().alias("db"),
        ).collect()
        n, mine, a, b, da, db = df.row(0)
        by_sender = C.counts_by(counted, "sender_id")
        people = _people_order(th, by_sender)
        if owner in people:  # owner first
            people = [owner, *(p for p in people if p != owner)]
        return ChatHeaderResponse(
            dataset_version=ctx.dataset_version,
            thread_id=chat_id,
            title=ctx.thread_title(chat_id),
            kind=chat_kind(str(th["category"]), bool(th["is_group"])),
            category=th["category"],
            is_group=bool(th["is_group"]),
            participants=[ctx.person_ref(p) for p in people],
            span=DateRange(start=da, end=db),
            first_ts=a,
            last_ts=b,
            span_days=(db - da).days + 1 if da is not None else 0,
            total_messages=int(n),
            my_messages=int(mine or 0),
            my_share=C.share(int(mine or 0), int(n)),
            drilldown=MessageQuery(thread_id=chat_id),
        )

    return ctx.cached("chats/header", compute, extra=chat_id)


# --------------------------------------------------------------------------------------
# Summary tab
# --------------------------------------------------------------------------------------


def _summary(ctx: AnalysisContext, chat_id: int) -> ChatSummaryResponse:
    th = _thread(ctx, chat_id)
    counted = _chat_counted(ctx, chat_id)
    tot = C.totals(counted)
    n = tot["messages"]
    by_sender = C.counts_by(counted, "sender_id")
    people = _people_order(th, by_sender)
    stats = {int(r["sender_id"]): r for r in by_sender.iter_rows(named=True)}
    extra = (
        counted.group_by("sender_id")
        .agg(
            pl.col("reply_latency_ms").median().alias("rep"),
            pl.col("is_session_start").sum().alias("starts"),
        )
        .collect()
    )
    rep = {int(s): r for s, r, _ in extra.iter_rows()}
    starts = {int(s): int(k) for s, _, k in extra.iter_rows()}
    sessions = sum(starts.values())
    reactions = ctx.chat_reactions(chat_id).collect()
    given = reactions.group_by("actor_id").agg(pl.len().alias("n"))
    got = reactions.group_by("target_id").agg(pl.len().alias("n"))
    given_d = {int(a): int(k) for a, k in given.iter_rows()}
    got_d = {int(a): int(k) for a, k in got.iter_rows()}

    rows: list[ChatPersonRow] = []
    for p in people:
        s = stats.get(p, {})

        def g(k: str, s: dict[str, Any] = s) -> int:
            return int(s.get(k) or 0)

        rows.append(
            ChatPersonRow(
                person=ctx.person_ref(p),
                messages=g("messages"),
                share=C.share(g("messages"), n),
                words=g("words"),
                avg_words_per_message=C.safe_div(g("words"), g("text_messages")),
                characters=g("characters"),
                emojis=g("emojis"),
                emojis_per_message=C.safe_div(g("emojis"), g("messages")),
                reactions_given=given_d.get(p, 0),
                reactions_received=got_d.get(p, 0),
                photos=g("photos"),
                videos=g("videos"),
                voice_notes=g("voice_notes"),
                reels=g("reels"),
                links=g("links"),
                calls_started=g("calls"),
                unsent=g("unsent"),
                median_reply_ms=rep.get(p),
                initiation_rate=C.safe_div(starts.get(p, 0), sessions),
                drilldown=MessageQuery(thread_id=chat_id, sender_id=p),
            )
        )

    start, end = T.date_span(counted)
    dates = T.active_dates(counted)
    per_day, _ = T.per_day_averages(n, start, end, len(dates))
    longest = S.longest_streak(dates)
    current = S.current_streak(dates, today_in(ctx))
    mutual = None
    if not th["is_group"] and len(th["participants"]) == 2:
        mutual = streak_info(
            S.longest_streak(S.mutual_dates(counted, [int(p) for p in th["participants"]])),
            thread_id=chat_id,
        )
    ends = counted.select(
        pl.col("message_id").min().alias("a"), pl.col("message_id").max().alias("b")
    ).collect()
    first_id, last_id = ends.row(0)
    gaps = S.longest_gaps(counted, 1)
    ids = [i for i in (first_id, last_id) if i is not None]
    for gp in gaps:
        ids += [gp.before_id, gp.after_id]
    refs = message_refs(ctx, ids)
    silence = None
    if gaps:
        gp = gaps[0]
        silence = SilenceInfo(
            gap_ms=gp.gap_ms,
            start=gp.start,
            end=gp.end,
            before=refs[gp.before_id],
            after=refs[gp.after_id],
        )
    q = MessageQuery(thread_id=chat_id)
    media = tot["photos"] + tot["videos"] + tot["voice_notes"] + tot["gifs"] + tot["stickers"]

    def tile(key: str, label: str, value: Any, unit: Any = "count", **kw: Any) -> Tile:
        return Tile(key=key, label=label, value=value, unit=unit, **kw)

    first_ref = refs.get(first_id) if first_id is not None else None
    last_ref = refs.get(last_id) if last_id is not None else None
    tiles = [
        tile("total_messages", "Messages", n, drilldown=q),
        tile("words", "Words", tot["words"]),
        tile("emojis", "Emojis", tot["emojis"]),
        tile(
            "reactions",
            "Reactions",
            reactions.height,
            drilldown=MessageQuery(thread_id=chat_id, has_reactions=True),
        ),
        tile(
            "media",
            "Media",
            media,
            drilldown=MessageQuery(
                thread_id=chat_id, types=["photo", "video", "audio", "gif", "sticker"]
            ),
        ),
        tile(
            "calls",
            "Calls",
            tot["calls"],
            drilldown=MessageQuery(thread_id=chat_id, types=["call"]),
        ),
        tile(
            "first_message",
            "First message",
            start.isoformat() if start else None,
            "date",
            person=first_ref.sender if first_ref else None,
            drilldown=MessageQuery(message_ids=[first_id]) if first_id is not None else None,
        ),
        tile(
            "last_message",
            "Last message",
            end.isoformat() if end else None,
            "date",
            person=last_ref.sender if last_ref else None,
            drilldown=MessageQuery(message_ids=[last_id]) if last_id is not None else None,
        ),
        tile("span_days", "Span (days)", (end - start).days + 1 if start and end else 0),
        tile("active_days", "Active days", len(dates)),
        tile("avg_per_day", "Avg messages per day", per_day, "per_day"),
        tile(
            "longest_streak",
            "Longest streak (days)",
            longest.length,
            detail=f"{longest.start} – {longest.end}" if longest.length else None,
            drilldown=streak_info(longest, thread_id=chat_id).drilldown,
        ),
        tile(
            "longest_silence",
            "Longest silence",
            silence.gap_ms if silence else None,
            "ms",
            detail=(
                f"{_local_date(ctx, silence.start)} – {_local_date(ctx, silence.end)}"
                if silence
                else None
            ),
            drilldown=(
                MessageQuery(message_ids=[silence.before.message_id, silence.after.message_id])
                if silence
                else None
            ),
        ),
    ]
    person_cats = [r.person.name for r in rows]
    person_q: list[MessageQuery | None] = [r.drilldown for r in rows]
    return ChatSummaryResponse(
        dataset_version=ctx.dataset_version,
        tiles=tiles,
        people=rows,
        messages_by_person=CategoryChart(
            categories=person_cats,
            series=[
                Series(key="messages", name="Messages", values=[float(r.messages) for r in rows])
            ],
            unit="count",
            drilldowns=person_q,
        ),
        words_by_person=CategoryChart(
            categories=person_cats,
            series=[Series(key="words", name="Words", values=[float(r.words) for r in rows])],
            unit="count",
            drilldowns=person_q,
        ),
        first_message=first_ref,
        last_message=last_ref,
        longest_streak=streak_info(longest, thread_id=chat_id),
        current_streak=streak_info(current, thread_id=chat_id),
        mutual_streak=mutual,
        longest_silence=silence,
    )


@router.get("/{chat_id}/summary", response_model=ChatSummaryResponse)
def get_chat_summary(ctx: Context, chat_id: ChatId) -> ChatSummaryResponse:
    """Summary tab: tiles, per-person table, donuts, first/last message (§7.2)."""
    ctx = ctx.for_chat(chat_id)
    return ctx.cached("chats/summary", lambda: _summary(ctx, chat_id), extra=chat_id)


# --------------------------------------------------------------------------------------
# Timeline tab
# --------------------------------------------------------------------------------------


def _events(
    ctx: AnalysisContext,
    chat_id: int,
    th: dict[str, Any],
    counted: pl.LazyFrame,
    lifecycle: TR.Lifecycle,
) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    first = (
        counted.sort("message_id").head(1).select("message_id", "sender_id", "local_date").collect()
    )
    if first.height:
        mid, sid, d = first.row(0)
        events.append(
            TimelineEvent(
                kind="first_message",
                date=d,
                label="First message",
                person=ctx.person_ref(int(sid)),
                message_id=int(mid),
                drilldown=MessageQuery(message_ids=[int(mid)]),
            )
        )
    for d, c in T.busiest_days(counted, 1):
        events.append(
            TimelineEvent(
                kind="peak_day",
                date=d,
                label=f"Busiest day ({c} messages)",
                value=float(c),
                drilldown=MessageQuery(thread_id=chat_id, start=d, end=d),
            )
        )
    if lifecycle.peak_month is not None:
        pm = lifecycle.peak_month
        events.append(
            TimelineEvent(
                kind="peak_month",
                date=pm,
                label=f"Peak month ({lifecycle.peak_month_count} messages)",
                value=float(lifecycle.peak_month_count),
                drilldown=MessageQuery(
                    thread_id=chat_id, start=pm, end=T.last_day_of_bucket(pm, "month")
                ),
            )
        )
    for gp in S.longest_gaps(counted, N_SILENCES):
        if gp.gap_ms < 86_400_000:
            continue
        days = gp.gap_ms // 86_400_000
        events.append(
            TimelineEvent(
                kind="silence",
                date=_local_date(ctx, gp.start),
                label=f"{days} days of silence",
                value=float(gp.gap_ms),
                message_id=gp.after_id,
                drilldown=MessageQuery(message_ids=[gp.before_id, gp.after_id]),
            )
        )
    call = (
        counted.filter(pl.col("type") == "call")
        .sort("message_id")
        .head(1)
        .select("message_id", "sender_id", "local_date")
        .collect()
    )
    if call.height:
        mid, sid, d = call.row(0)
        events.append(
            TimelineEvent(
                kind="first_call",
                date=d,
                label="First call",
                person=ctx.person_ref(int(sid)),
                message_id=int(mid),
                drilldown=MessageQuery(message_ids=[int(mid)]),
            )
        )
    # Group events come from system messages (shown even when exclude_system is on).
    f = ctx.filters.model_copy(update={"exclude_system": False})
    sys_df = (
        filtered_messages(ctx.dataset, f, thread_id=chat_id)
        .filter(pl.col("system_event").cast(pl.String).is_in(EVENT_SYSTEM))
        .sort("message_id")
        .select("message_id", "sender_id", "local_date", "system_event", "system_payload")
        .collect()
    )
    for mid, sid, d, ev, payload in sys_df.iter_rows():
        shown = ctx.text(payload)
        label = {
            "rename": f"Renamed to “{shown}”" if shown else "Group renamed",
            "join": f"{shown} joined" if shown else "Member joined",
            "leave": f"{shown} left" if shown else "Member left",
            "removed": f"{shown} removed" if shown else "Member removed",
        }[str(ev)]
        events.append(
            TimelineEvent(
                kind=ev,
                date=d,
                label=label,
                person=ctx.person_ref(int(sid)),
                message_id=int(mid),
                drilldown=MessageQuery(message_ids=[int(mid)]),
            )
        )
    terms = ctx.settings.custom_terms.get(str(th["folder"]), [])
    for term in terms:
        if not term.strip():
            continue
        hit = (
            counted.filter(
                pl.col("text").str.to_lowercase().str.contains(term.lower(), literal=True)
            )
            .sort("message_id")
            .head(1)
            .select("message_id", "sender_id", "local_date")
            .collect()
        )
        if hit.height:
            mid, sid, d = hit.row(0)
            events.append(
                TimelineEvent(
                    kind="custom_term",
                    date=d,
                    label=f"First “{term}”",
                    person=ctx.person_ref(int(sid)),
                    message_id=int(mid),
                    drilldown=MessageQuery(message_ids=[int(mid)]),
                )
            )
    events.sort(key=lambda e: (e.date, e.message_id if e.message_id is not None else 2**32))
    return events


def _timeline(ctx: AnalysisContext, chat_id: int, bucket: Any) -> ChatTimelineResponse:
    th = _thread(ctx, chat_id)
    owner = ctx.owner_id
    counted = _chat_counted(ctx, chat_id)
    by_sender = C.counts_by(counted, "sender_id")
    people = _people_order(th, by_sender)
    start, end = T.date_span(counted)
    b = T.choose_bucket(start, end, bucket)

    msgs = T.bucket_values(counted, b, by="sender_id", start=start, end=end)
    monthly = T.bucket_values(counted, "month", by="sender_id", start=start, end=end)
    month_total = [sum(col) for col in zip(*monthly.series.values(), strict=True)] or [0.0] * len(
        monthly.x
    )
    share_data = {
        k: [v / t if t else 0.0 for v, t in zip(vs, month_total, strict=True)]
        for k, vs in monthly.series.items()
    }
    balance = None
    balance_overall = None
    if not th["is_group"] and owner is not None:
        mine = monthly.series.get(owner, [0.0] * len(monthly.x))
        balance = TimeSeries(
            bucket="month",
            x=monthly.labels(),
            series=[
                Series(key="balance", name="Balance", values=TR.balance_series(mine, month_total))
            ],
            unit="ratio",
        )
        n = sum(month_total)
        if n:
            balance_overall = TR.balance_index(sum(mine) / n)

    cb = T.choose_bucket(start, end, "day")
    daily_people = T.bucket_values(counted, cb, by="sender_id", start=start, end=end)
    cum = {k: T.cumulative(v) for k, v in daily_people.series.items()}
    daily = T.bucket_values(counted, cb, start=start, end=end).series.get(T.TOTAL, [])

    text = counted.filter(is_text_message())
    words = T.bucket_values(
        text, "month", by="sender_id", value=pl.col("word_count"), start=start, end=end
    )
    text_n = T.bucket_values(text, "month", by="sender_id", start=start, end=end)
    wpm: dict[Hashable, list[float | None]] = {
        k: [w / c if c else None for w, c in zip(ws, text_n.series[k], strict=True)]
        for k, ws in words.series.items()
    }
    nulls: list[float | None] = [None] * len(monthly.x)
    wpm_series = [
        Series(
            key=str(p),
            name=ctx.person_ref(p).name,
            person_id=p,
            color_index=ctx.person_ref(p).color_index,
            values=wpm.get(p, nulls),
        )
        for p in people
    ]

    trend_months = _monthly_to(counted, _global_end(ctx))
    label, slope = TR.trend(trend_months.series.get(T.TOTAL, []))
    life = TR.lifecycle(T.active_dates(counted), monthly.x, month_total if monthly.x else [])
    cal, _ = calendar_data(counted)
    return ChatTimelineResponse(
        dataset_version=ctx.dataset_version,
        requested_bucket=bucket,
        messages=TimeSeries(
            bucket=b,
            x=msgs.labels(),
            series=_person_series(ctx, people, msgs.series, len(msgs.x)),
            unit="count",
        ),
        share=TimeSeries(
            bucket="month",
            x=monthly.labels(),
            series=_person_series(ctx, people, share_data, len(monthly.x)),
            unit="ratio",
        ),
        balance=balance,
        balance_overall=balance_overall,
        cumulative=TimeSeries(
            bucket=cb,
            x=daily_people.labels(),
            series=_person_series(ctx, people, cum, len(daily_people.x)),
            unit="count",
        ),
        rolling=TimeSeries(
            bucket=cb,
            x=daily_people.labels(),
            series=[
                Series(key="daily", name="Messages", values=vals(daily)),
                Series(key="avg_7", name="7-day average", values=vals(T.rolling_mean(daily, 7))),
                Series(key="avg_30", name="30-day average", values=vals(T.rolling_mean(daily, 30))),
            ],
            unit="count",
        ),
        words_per_message=TimeSeries(
            bucket="month", x=monthly.labels(), series=wpm_series, unit="per_message"
        ),
        calendar=cal,
        events=_events(ctx, chat_id, th, counted, life),
        trend=label,
        trend_slope=slope,
        lifecycle=LifecycleOut(
            first=life.first,
            last=life.last,
            span_days=life.span_days,
            peak_month=life.peak_month,
            peak_month_count=life.peak_month_count,
            active_months=life.active_months,
            longest_break_days=life.longest_break_days,
            break_start=life.break_start,
            comeback=life.comeback,
        ),
    )


@router.get("/{chat_id}/timeline", response_model=ChatTimelineResponse)
def get_chat_timeline(
    ctx: Context, chat_id: ChatId, bucket: BucketQuery = "week"
) -> ChatTimelineResponse:
    """Timeline tab (§7.2): per-person activity, share, balance, cumulative, rolling
    averages, words/message, calendar and annotated events."""
    ctx = ctx.for_chat(chat_id)
    return ctx.cached(
        "chats/timeline", lambda: _timeline(ctx, chat_id, bucket), extra=(chat_id, bucket)
    )


# --------------------------------------------------------------------------------------
# When tab
# --------------------------------------------------------------------------------------


def _when(ctx: AnalysisContext, chat_id: int) -> ChatWhenResponse:
    th = _thread(ctx, chat_id)
    counted = _chat_counted(ctx, chat_id)
    by_sender = C.counts_by(counted, "sender_id")
    people = _people_order(th, by_sender)
    q = MessageQuery(thread_id=chat_id)

    mats = T.hour_weekday_matrices(counted, "sender_id")
    empty = [[0.0] * 24 for _ in range(7)]
    heatmaps = [
        PersonHeatmap(person=ctx.person_ref(p), heatmap=weekday_hour_heatmap(mats.get(p, empty)))
        for p in people
    ]
    hours = T.hour_counts(counted, by="sender_id")
    wds = T.weekday_counts(counted, by="sender_id")
    months = T.month_of_year_counts(counted, by="sender_id")

    stats: list[PersonWhenStats] = []
    ww: dict[Hashable, list[float]] = {}
    for p in people:
        h = hours.get(p, [0.0] * 24)
        w = wds.get(p, [0.0] * 7)
        n = sum(h)
        weekend = C.share(w[5] + w[6], n)
        ww[p] = [1.0 - weekend if n else 0.0, weekend]
        stats.append(
            PersonWhenStats(
                person=ctx.person_ref(p),
                messages=int(n),
                night_owl_ratio=C.share(sum(h[i] for i in T.NIGHT_HOURS), n),
                early_bird_ratio=C.share(sum(h[i] for i in T.EARLY_HOURS), n),
                weekend_ratio=weekend,
            )
        )
    by_year = T.hours_by_year(counted)
    year_rows = [[C.share(v, sum(row)) for v in row] for row in by_year.values()]
    days = [
        DayCount(date=d, count=c, drilldown=MessageQuery(thread_id=chat_id, start=d, end=d))
        for d, c in T.busiest_days(counted, 10)
    ]
    bh = T.busiest_hour(counted)
    busiest_hour = (
        HourCount(
            date=bh[0],
            hour=bh[1],
            count=bh[2],
            drilldown=MessageQuery(thread_id=chat_id, start=bh[0], end=bh[0], hour=bh[1]),
        )
        if bh
        else None
    )
    return ChatWhenResponse(
        dataset_version=ctx.dataset_version,
        heatmap=weekday_hour_heatmap(T.hour_weekday_matrix(counted)),
        heatmaps=heatmaps,
        hours=CategoryChart(
            categories=HOUR_LABELS,
            series=_person_series(ctx, people, hours, 24),
            unit="count",
            drilldowns=[q.model_copy(update={"hour": h}) for h in range(24)],
        ),
        weekdays=CategoryChart(
            categories=WEEKDAY_LABELS,
            series=_person_series(ctx, people, wds, 7),
            unit="count",
            drilldowns=[q.model_copy(update={"weekday": w}) for w in range(1, 8)],
        ),
        months=CategoryChart(
            categories=MONTH_LABELS,
            series=_person_series(ctx, people, months, 12),
            unit="count",
            drilldowns=[q.model_copy(update={"month": m}) for m in range(1, 13)],
        ),
        weekday_weekend=CategoryChart(
            categories=["Weekdays", "Weekend"],
            series=_person_series(ctx, people, ww, 2),
            unit="ratio",
        ),
        person_stats=stats,
        hours_by_year=HeatmapData(
            x_labels=HOUR_LABELS,
            y_labels=[str(y) for y in by_year],
            values=year_rows,
            unit="ratio",
        ),
        busiest_days=days,
        busiest_hour=busiest_hour,
    )


@router.get("/{chat_id}/when", response_model=ChatWhenResponse)
def get_chat_when(ctx: Context, chat_id: ChatId) -> ChatWhenResponse:
    """When tab (§7.2): hour/weekday/month patterns, night owl/early bird, busiest days."""
    ctx = ctx.for_chat(chat_id)
    return ctx.cached("chats/when", lambda: _when(ctx, chat_id), extra=chat_id)
