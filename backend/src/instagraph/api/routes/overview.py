"""Overview (all chats): ``GET /api/overview`` and its chart sections (spec §5.3, §7.1).

Only explicit section routes live here; ``emojis``, ``reply-times`` and ``graph`` are in
other modules. "Contacts" are chats (see :mod:`instagraph.schemas.overview`).

The small helpers at the top (:func:`chat_refs`, :func:`message_refs`, …) are shared
with :mod:`instagraph.api.routes.chats`.
"""

from collections.abc import Hashable, Sequence
from datetime import date, datetime
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import polars as pl
from fastapi import APIRouter, Query

from instagraph.analysis import counts as C
from instagraph.analysis import streaks as S
from instagraph.analysis import time as T
from instagraph.api.deps import AnalysisContext, Context
from instagraph.api.filters import filtered_messages, is_counted_message
from instagraph.errors import ERROR_RESPONSES
from instagraph.schemas.common import (
    Bucket,
    CalendarData,
    CalendarDay,
    CategoryChart,
    DateRange,
    HeatmapData,
    MessageQuery,
    MessageRef,
    Series,
    Tile,
    TimeSeries,
)
from instagraph.schemas.overview import (
    ActiveChatsResponse,
    BusiestDay,
    ChatKind,
    ChatRef,
    ContactItem,
    ContactMetric,
    ContactsResponse,
    CumulativeResponse,
    ExcludedRequests,
    FirstMessage,
    HoursResponse,
    NewConversationsResponse,
    OverviewCalendarResponse,
    OverviewHeatmapResponse,
    OverviewResponse,
    OverviewTimelineResponse,
    SentReceivedResponse,
    SizeBinOut,
    SizeDistributionResponse,
    StreakInfo,
    TopOverTimeResponse,
    TypesResponse,
    WeekdaysResponse,
    YearRow,
    YearsResponse,
)

router = APIRouter(prefix="/api/overview", tags=["overview"], responses=ERROR_RESPONSES)

WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
HOUR_LABELS = [str(h) for h in range(24)]
MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
TOP_CHATS = 10
SNIPPET_CHARS = 200

BucketQuery = Annotated[Bucket, Query(description="Time bucket (coarsened if too many points)")]

# --------------------------------------------------------------------------------------
# Shared helpers (also used by routes/chats.py)
# --------------------------------------------------------------------------------------


def vals(xs: Sequence[float | None]) -> list[float | None]:
    """Series values (``list`` is invariant, so convert ``list[float]`` explicitly)."""
    return list(xs)


def chat_kind(category: str, is_group: bool) -> ChatKind:
    if category == "request":
        return "request"
    return "group" if is_group else "one_to_one"


def thread_title(ctx: AnalysisContext, row: dict[str, Any]) -> str:
    """``ctx.thread_title`` for a threads row."""
    return ctx.thread_title(int(row["thread_id"]))


def chat_refs(ctx: AnalysisContext) -> dict[int, ChatRef]:
    """``thread_id -> ChatRef`` for every thread (titles anonymized if needed)."""
    owner = ctx.owner_id
    out: dict[int, ChatRef] = {}
    for row in ctx.dataset.threads_df().iter_rows(named=True):
        tid = int(row["thread_id"])
        others = [int(p) for p in row["participants"] if p != owner]
        person = ctx.person_ref(others[0]) if not row["is_group"] and len(others) == 1 else None
        out[tid] = ChatRef(
            thread_id=tid,
            title=thread_title(ctx, row),
            kind=chat_kind(str(row["category"]), bool(row["is_group"])),
            person=person,
        )
    return out


def snippet(ctx: AnalysisContext, text: str | None) -> str | None:
    value = ctx.text(text)
    if value is not None and len(value) > SNIPPET_CHARS:
        value = value[: SNIPPET_CHARS - 1] + "…"
    return value


def message_refs(ctx: AnalysisContext, ids: Sequence[int]) -> dict[int, MessageRef]:
    """``message_id -> MessageRef`` for the given ids (unknown ids are skipped)."""
    if not ids:
        return {}
    df = (
        ctx.dataset.messages.filter(pl.col("message_id").is_in(list(ids)))
        .select("message_id", "thread_id", "sender_id", "ts", "type", "text")
        .collect()
    )
    return {
        int(r["message_id"]): MessageRef(
            message_id=int(r["message_id"]),
            thread_id=int(r["thread_id"]),
            sender=ctx.person_ref(int(r["sender_id"])),
            ts=r["ts"],
            type=r["type"],
            text=snippet(ctx, r["text"]),
        )
        for r in df.iter_rows(named=True)
    }


def today_in(ctx: AnalysisContext) -> date:
    return datetime.now(ZoneInfo(ctx.tz)).date()


def streak_info(streak: S.Streak, **query: Any) -> StreakInfo:
    drill = MessageQuery(start=streak.start, end=streak.end, **query) if streak.length else None
    return StreakInfo(
        length_days=streak.length, start=streak.start, end=streak.end, drilldown=drill
    )


def bucket_query(start: date, bucket: Bucket, **query: Any) -> MessageQuery:
    return MessageQuery(start=start, end=T.last_day_of_bucket(start, bucket), **query)


def other_sender_ids(counted: pl.LazyFrame, owner: int | None) -> list[int]:
    ids: list[int] = (
        counted.select(pl.col("sender_id").unique()).collect()["sender_id"].sort().to_list()
    )
    return [int(i) for i in ids if i != owner]


def counted_messages(ctx: AnalysisContext) -> pl.LazyFrame:
    return ctx.messages().filter(is_counted_message())


def _me_flag(owner: int | None) -> pl.Expr:
    return (pl.col("sender_id") == (owner if owner is not None else -1)).alias("_me")


# --------------------------------------------------------------------------------------
# Headline tiles
# --------------------------------------------------------------------------------------


def _excluded_requests(ctx: AnalysisContext) -> ExcludedRequests | None:
    if ctx.filters.include_requests:
        return None
    req_ids = (
        ctx.dataset.threads_df().filter(pl.col("category") == "request")["thread_id"].to_list()
    )
    if not req_ids:
        return None
    f = ctx.filters.model_copy(update={"include_requests": True})
    df = (
        filtered_messages(ctx.dataset, f)
        .filter(is_counted_message() & pl.col("thread_id").is_in(req_ids))
        .select(pl.len().alias("n"), pl.col("thread_id").n_unique().alias("t"))
        .collect()
    )
    n, t = (int(v) for v in df.row(0))
    if n == 0:
        return None
    word = "request" if t == 1 else "requests"
    return ExcludedRequests(
        threads=t, messages=n, note=f"+{t} message {word} ({n} messages) not included"
    )


def _overview(ctx: AnalysisContext) -> OverviewResponse:
    owner = ctx.owner_id
    counted = counted_messages(ctx)
    tot = C.totals(counted)
    n = tot["messages"]
    by_sender = C.counts_by(counted, "sender_id")
    sent = (
        int(by_sender.filter(pl.col("sender_id") == owner)["messages"].sum())
        if owner is not None
        else 0
    )
    received = n - sent
    others = [int(p) for p in by_sender["sender_id"].to_list() if p != owner]
    refs = chat_refs(ctx)
    thread_ids = counted.select(pl.col("thread_id").unique()).collect()["thread_id"].to_list()
    kinds = [refs[int(t)].kind for t in thread_ids if int(t) in refs]
    reactions = ctx.reactions().select("actor_id", "target_id").collect()
    given = reactions.filter(pl.col("actor_id") == owner).height if owner is not None else 0
    got = (
        reactions.filter((pl.col("target_id") == owner) & (pl.col("actor_id") != owner)).height
        if owner is not None
        else 0
    )
    start, end = T.date_span(counted)
    dates = T.active_dates(counted)
    _, per_active = T.per_day_averages(n, start, end, len(dates))
    today = today_in(ctx)

    # first message ever
    first: FirstMessage | None = None
    first_row = counted.sort("message_id").head(1).select("message_id", "thread_id").collect()
    if first_row.height:
        mid, tid = (int(v) for v in first_row.row(0))
        mref = message_refs(ctx, [mid])[mid]
        first_day = start or today
        first = FirstMessage(
            message=mref, chat=refs[tid], days_since=max((today - first_day).days, 0)
        )

    # busiest day + its top chat
    busiest: BusiestDay | None = None
    top_days = T.busiest_days(counted, 1)
    if top_days:
        d, c = top_days[0]
        day_chats = (
            counted.filter(pl.col("local_date") == d)
            .group_by("thread_id")
            .agg(pl.len().alias("n"))
            .sort(["n", "thread_id"], descending=[True, False])
            .head(1)
            .collect()
        )
        tchat, tcount = (int(v) for v in day_chats.row(0))
        busiest = BusiestDay(
            date=d,
            count=c,
            top_chat=refs.get(tchat),
            top_chat_count=tcount,
            drilldown=MessageQuery(start=d, end=d),
        )

    longest = S.longest_streak(dates)
    current = S.current_streak(dates, today)
    me_q = MessageQuery(sender_id=owner) if owner is not None else None
    others_q = MessageQuery(sender_ids=others) if others else None

    def tile(key: str, label: str, value: Any, unit: Any = "count", **kw: Any) -> Tile:
        return Tile(key=key, label=label, value=value, unit=unit, **kw)

    tiles = [
        tile("total_messages", "Total messages", n, drilldown=MessageQuery()),
        tile("sent", "Sent by me", sent, drilldown=me_q),
        tile("received", "Received", received, drilldown=others_q),
        tile("my_share", "My share", C.share(sent, n), "ratio"),
        tile(
            "chats",
            "Chats",
            len(kinds),
            detail=(
                f"{kinds.count('one_to_one')} 1:1 · {kinds.count('group')} groups · "
                f"{kinds.count('request')} requests"
            ),
        ),
        tile("chats_one_to_one", "1:1 chats", kinds.count("one_to_one")),
        tile("chats_group", "Group chats", kinds.count("group")),
        tile("chats_request", "Message requests", kinds.count("request")),
        tile("people", "People", len(others)),
        tile("words", "Words", tot["words"]),
        tile("characters", "Characters", tot["characters"]),
        tile("emojis", "Emojis", tot["emojis"]),
        tile("reactions_given", "Reactions given", given),
        tile("reactions_received", "Reactions received", got),
        tile("photos", "Photos", tot["photos"], drilldown=MessageQuery(types=["photo"])),
        tile("videos", "Videos", tot["videos"], drilldown=MessageQuery(types=["video"])),
        tile(
            "voice_notes",
            "Voice notes",
            tot["voice_notes"],
            drilldown=MessageQuery(types=["audio"]),
        ),
        tile(
            "shares",
            "Reels/posts shared",
            tot["reels"] + tot["posts"],
            detail=f"{tot['reels']} reels · {tot['posts']} posts",
            drilldown=MessageQuery(types=["share"]),
        ),
        tile("links", "Links", tot["links"], drilldown=MessageQuery(types=["link"])),
        tile("calls", "Calls", tot["calls"], drilldown=MessageQuery(types=["call"])),
        tile("call_time", "Total call time", tot["call_duration_s"] * 1000, "ms"),
        tile(
            "first_message",
            "First message ever",
            start.isoformat() if start else None,
            "date",
            detail=first.chat.title if first else None,
            person=(first.chat.person or first.message.sender) if first else None,
            drilldown=MessageQuery(message_ids=[first.message.message_id]) if first else None,
        ),
        tile("days_since_first", "Days since", first.days_since if first else None),
        tile("active_days", "Active days", len(dates)),
        tile("avg_per_active_day", "Avg messages per active day", per_active, "per_day"),
        tile(
            "busiest_day",
            "Busiest day ever",
            busiest.count if busiest else None,
            detail=(
                f"{busiest.date.isoformat()}"
                + (f" · {busiest.top_chat.title}" if busiest.top_chat else "")
                if busiest
                else None
            ),
            drilldown=busiest.drilldown if busiest else None,
        ),
        tile(
            "longest_streak",
            "Longest daily streak",
            longest.length,
            detail=(f"{longest.start} – {longest.end}" if longest.length else None),
            drilldown=streak_info(longest).drilldown,
        ),
    ]
    return OverviewResponse(
        dataset_version=ctx.dataset_version,
        tiles=tiles,
        span=DateRange(start=start, end=end),
        first_message=first,
        busiest_day=busiest,
        longest_streak=streak_info(longest),
        current_streak=streak_info(current),
        excluded_requests=_excluded_requests(ctx),
    )


@router.get("", response_model=OverviewResponse)
def get_overview(ctx: Context) -> OverviewResponse:
    """Headline tiles (§7.1)."""
    return ctx.cached("overview", lambda: _overview(ctx))


# --------------------------------------------------------------------------------------
# Charts
# --------------------------------------------------------------------------------------


def _series_for_chat(ref: ChatRef, values: list[float]) -> Series:
    return Series(
        key=str(ref.thread_id),
        name=ref.title,
        person_id=ref.person.person_id if ref.person else None,
        color_index=ref.person.color_index if ref.person else None,
        values=vals(values),
    )


def _top_threads(counted: pl.LazyFrame, n: int) -> list[int]:
    df = (
        counted.group_by("thread_id")
        .agg(pl.len().alias("n"))
        .sort(["n", "thread_id"], descending=[True, False])
        .head(n)
        .collect()
    )
    return [int(t) for t in df["thread_id"].to_list()]


@router.get("/timeline", response_model=OverviewTimelineResponse)
def get_timeline(ctx: Context, bucket: BucketQuery = "week") -> OverviewTimelineResponse:
    """Messages over time stacked by the top 10 chats + others (chart 1)."""

    def compute() -> OverviewTimelineResponse:
        counted = counted_messages(ctx)
        start, end = T.date_span(counted)
        b = T.choose_bucket(start, end, bucket)
        top = _top_threads(counted, TOP_CHATS)
        refs = chat_refs(ctx)
        grouped = counted.with_columns(
            pl.when(pl.col("thread_id").is_in(top))
            .then(pl.col("thread_id").cast(pl.Int64))
            .otherwise(-1)
            .alias("_g")
        )
        data = T.bucket_values(grouped, b, by="_g", start=start, end=end)
        zeros = [0.0] * len(data.x)
        series = [_series_for_chat(refs[t], data.series.get(t, zeros)) for t in top]
        drill = {str(t): MessageQuery(thread_id=t) for t in top}
        all_ids = counted.select(pl.col("thread_id").unique()).collect()["thread_id"].to_list()
        rest = sorted(int(t) for t in all_ids if t not in top)
        if rest:
            series.append(
                Series(key="others", name="Others", values=vals(data.series.get(-1, zeros)))
            )
            drill["others"] = MessageQuery(thread_ids=rest)
        return OverviewTimelineResponse(
            dataset_version=ctx.dataset_version,
            requested_bucket=bucket,
            timeline=TimeSeries(bucket=b, x=data.labels(), series=series, unit="count"),
            chats=[refs[t] for t in top],
            drilldowns=drill,
        )

    return ctx.cached("overview/timeline", compute, extra=bucket)


@router.get("/sent-received", response_model=SentReceivedResponse)
def get_sent_received(ctx: Context, bucket: BucketQuery = "month") -> SentReceivedResponse:
    """Sent vs received over time (chart 2)."""

    def compute() -> SentReceivedResponse:
        owner = ctx.owner_id
        counted = counted_messages(ctx)
        start, end = T.date_span(counted)
        b = T.choose_bucket(start, end, bucket)
        data = T.bucket_values(
            counted.with_columns(_me_flag(owner)), b, by="_me", start=start, end=end
        )
        zeros = [0.0] * len(data.x)
        series = [
            Series(
                key="sent", name="Sent", color_index=0, values=vals(data.series.get(True, zeros))
            ),
            Series(key="received", name="Received", values=vals(data.series.get(False, zeros))),
        ]
        drill: dict[str, MessageQuery] = {}
        if owner is not None:
            drill["sent"] = MessageQuery(sender_id=owner)
        others = other_sender_ids(counted, owner)
        if others:
            drill["received"] = MessageQuery(sender_ids=others)
        return SentReceivedResponse(
            dataset_version=ctx.dataset_version,
            requested_bucket=bucket,
            timeline=TimeSeries(bucket=b, x=data.labels(), series=series, unit="count"),
            drilldowns=drill,
        )

    return ctx.cached("overview/sent-received", compute, extra=bucket)


@router.get("/contacts", response_model=ContactsResponse)
def get_contacts(
    ctx: Context,
    metric: ContactMetric = "messages",
    limit: Annotated[int, Query(ge=1, le=500)] = 25,
) -> ContactsResponse:
    """Top chats leaderboard by metric (chart 3)."""

    def compute() -> ContactsResponse:
        counted = counted_messages(ctx)
        values: dict[int, float]
        if metric == "reactions":
            df = ctx.reactions().group_by("thread_id").agg(pl.len().alias("v")).collect()
        else:
            df = C.counts_by(counted, "thread_id").select("thread_id", pl.col(metric).alias("v"))
        values = {int(t): float(v) for t, v in df.iter_rows() if v}
        total = sum(values.values())
        refs = chat_refs(ctx)
        ranked = sorted(values.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
        types: Any = ["call"] if metric == "calls" else None
        items = [
            ContactItem(
                chat=refs[t],
                value=v,
                share=C.share(v, total),
                drilldown=MessageQuery(
                    thread_id=t, types=types, has_reactions=True if metric == "reactions" else None
                ),
            )
            for t, v in ranked
            if t in refs
        ]
        return ContactsResponse(
            dataset_version=ctx.dataset_version, metric=metric, total=total, items=items
        )

    return ctx.cached("overview/contacts", compute, extra=(metric, limit))


@router.get("/top-over-time", response_model=TopOverTimeResponse)
def get_top_over_time(
    ctx: Context, top: Annotated[int, Query(ge=1, le=30)] = TOP_CHATS
) -> TopOverTimeResponse:
    """Rank per month (bump chart) and monthly counts (stream graph) of the top chats."""

    def compute() -> TopOverTimeResponse:
        counted = counted_messages(ctx)
        start, end = T.date_span(counted)
        ids = _top_threads(counted, top)
        refs = chat_refs(ctx)
        data = T.bucket_values(counted, "month", by="thread_id", start=start, end=end)
        ranks = T.rank_per_bucket(counted, "month", by="thread_id", x=data.x)
        zeros = [0.0] * len(data.x)
        nulls: list[float | None] = [None] * len(data.x)
        labels = data.labels()
        return TopOverTimeResponse(
            dataset_version=ctx.dataset_version,
            chats=[refs[t] for t in ids],
            ranks=TimeSeries(
                bucket="month",
                x=labels,
                series=[
                    _series_for_chat(refs[t], []).model_copy(update={"values": ranks.get(t, nulls)})
                    for t in ids
                ],
            ),
            stream=TimeSeries(
                bucket="month",
                x=labels,
                series=[_series_for_chat(refs[t], data.series.get(t, zeros)) for t in ids],
                unit="count",
            ),
        )

    return ctx.cached("overview/top-over-time", compute, extra=top)


def calendar_data(lf: pl.LazyFrame) -> tuple[CalendarData, list[int]]:
    daily = T.daily_counts(lf)
    days = [CalendarDay(date=d, value=float(v)) for d, v in daily.iter_rows()]
    max_value = max((d.value for d in days), default=0.0)
    years = sorted({d.date.year for d in days})
    return CalendarData(days=days, max_value=max_value), years


@router.get("/calendar", response_model=OverviewCalendarResponse)
def get_calendar(ctx: Context) -> OverviewCalendarResponse:
    """Calendar heatmap of all messages (chart 5)."""

    def compute() -> OverviewCalendarResponse:
        cal, years = calendar_data(counted_messages(ctx))
        return OverviewCalendarResponse(
            dataset_version=ctx.dataset_version,
            calendar=cal,
            years=years,
            active_days=len(cal.days),
        )

    return ctx.cached("overview/calendar", compute)


def weekday_hour_heatmap(values: list[list[float]]) -> HeatmapData:
    return HeatmapData(x_labels=HOUR_LABELS, y_labels=WEEKDAY_LABELS, values=values, unit="count")


@router.get("/heatmap", response_model=OverviewHeatmapResponse)
def get_heatmap(ctx: Context) -> OverviewHeatmapResponse:
    """Hour × weekday punch card (chart 6)."""

    def compute() -> OverviewHeatmapResponse:
        return OverviewHeatmapResponse(
            dataset_version=ctx.dataset_version,
            heatmap=weekday_hour_heatmap(T.hour_weekday_matrix(counted_messages(ctx))),
        )

    return ctx.cached("overview/heatmap", compute)


def _me_others_series(
    data: dict[Hashable, list[float]], size: int, with_total: bool = False
) -> list[Series]:
    zeros = [0.0] * size
    me = data.get(True, zeros)
    others = data.get(False, zeros)
    out = []
    if with_total:
        out.append(
            Series(
                key="total", name="Total", values=[a + b for a, b in zip(me, others, strict=True)]
            )
        )
    out.append(Series(key="me", name="Me", color_index=0, values=vals(me)))
    out.append(Series(key="others", name="Others", values=vals(others)))
    return out


@router.get("/hours", response_model=HoursResponse)
def get_hours(ctx: Context) -> HoursResponse:
    """Hour of day distribution, me vs others (chart 7)."""

    def compute() -> HoursResponse:
        counted = counted_messages(ctx).with_columns(_me_flag(ctx.owner_id))
        data = T.hour_counts(counted, by="_me")
        return HoursResponse(
            dataset_version=ctx.dataset_version,
            chart=CategoryChart(
                categories=HOUR_LABELS,
                series=_me_others_series(data, 24),
                unit="count",
                drilldowns=[MessageQuery(hour=h) for h in range(24)],
            ),
        )

    return ctx.cached("overview/hours", compute)


@router.get("/weekdays", response_model=WeekdaysResponse)
def get_weekdays(ctx: Context) -> WeekdaysResponse:
    """Day of week distribution (chart 8)."""

    def compute() -> WeekdaysResponse:
        counted = counted_messages(ctx).with_columns(_me_flag(ctx.owner_id))
        data = T.weekday_counts(counted, by="_me")
        return WeekdaysResponse(
            dataset_version=ctx.dataset_version,
            chart=CategoryChart(
                categories=WEEKDAY_LABELS,
                series=_me_others_series(data, 7, with_total=True),
                unit="count",
                drilldowns=[MessageQuery(weekday=w) for w in range(1, 8)],
            ),
        )

    return ctx.cached("overview/weekdays", compute)


@router.get("/cumulative", response_model=CumulativeResponse)
def get_cumulative(ctx: Context, bucket: BucketQuery = "day") -> CumulativeResponse:
    """Cumulative messages (chart 9)."""

    def compute() -> CumulativeResponse:
        counted = counted_messages(ctx)
        start, end = T.date_span(counted)
        b = T.choose_bucket(start, end, bucket)
        data = T.bucket_values(counted, b, start=start, end=end)
        values = T.cumulative(data.series.get(T.TOTAL, []))
        return CumulativeResponse(
            dataset_version=ctx.dataset_version,
            timeline=TimeSeries(
                bucket=b,
                x=data.labels(),
                series=[Series(key="total", name="Messages", values=list(values))],
                unit="count",
            ),
        )

    return ctx.cached("overview/cumulative", compute, extra=bucket)


def first_contact_dates(ctx: AnalysisContext, counted: pl.LazyFrame) -> pl.DataFrame:
    """``person_id, first_date``: first day with a message in any chat with the person
    (chats they participate in, or where they wrote)."""
    owner = ctx.owner_id if ctx.owner_id is not None else -1
    thread_first = counted.group_by("thread_id").agg(pl.col("local_date").min().alias("d"))
    parts = (
        ctx.dataset.threads.select("thread_id", pl.col("participants").alias("person_id"))
        .explode("person_id")
        .drop_nulls("person_id")
        .join(thread_first, on="thread_id", how="inner")
        .select("person_id", "d")
    )
    senders = counted.group_by(pl.col("sender_id").alias("person_id")).agg(
        pl.col("local_date").min().alias("d")
    )
    return (
        pl.concat([parts, senders.select("person_id", "d")])
        .filter(pl.col("person_id") != owner)
        .group_by("person_id")
        .agg(pl.col("d").min().alias("first_date"))
        .collect()
    )


@router.get("/new-conversations", response_model=NewConversationsResponse)
def get_new_conversations(ctx: Context) -> NewConversationsResponse:
    """New people per month (chart 10)."""

    def compute() -> NewConversationsResponse:
        counted = counted_messages(ctx)
        start, end = T.date_span(counted)
        firsts = first_contact_dates(ctx, counted).select(pl.col("first_date").alias("local_date"))
        data = T.bucket_values(firsts.lazy(), "month", start=start, end=end)
        return NewConversationsResponse(
            dataset_version=ctx.dataset_version,
            timeline=TimeSeries(
                bucket="month",
                x=data.labels(),
                series=[
                    Series(key="new", name="New people", values=vals(data.series.get(T.TOTAL, [])))
                ],
                unit="count",
            ),
            total=firsts.height,
        )

    return ctx.cached("overview/new-conversations", compute)


@router.get("/active-chats", response_model=ActiveChatsResponse)
def get_active_chats(ctx: Context) -> ActiveChatsResponse:
    """Number of chats with ≥ 1 message per month (chart 11)."""

    def compute() -> ActiveChatsResponse:
        counted = counted_messages(ctx)
        start, end = T.date_span(counted)
        pairs = counted.select(
            "thread_id", T.bucket_start_expr("month").alias("local_date")
        ).unique()
        data = T.bucket_values(pairs, "month", start=start, end=end)
        return ActiveChatsResponse(
            dataset_version=ctx.dataset_version,
            timeline=TimeSeries(
                bucket="month",
                x=data.labels(),
                series=[
                    Series(
                        key="active", name="Active chats", values=vals(data.series.get(T.TOTAL, []))
                    )
                ],
                unit="count",
            ),
        )

    return ctx.cached("overview/active-chats", compute)


def type_mix(counted: pl.LazyFrame, bucket: Bucket = "month") -> tuple[TimeSeries, CategoryChart]:
    """Monthly counts per message type + totals (types with 0 messages omitted)."""
    start, end = T.date_span(counted)
    data = T.bucket_values(
        counted.with_columns(pl.col("type").cast(pl.String).alias("_t")),
        bucket,
        by="_t",
        start=start,
        end=end,
    )
    totals = C.type_counts(counted)
    present = [t for t in C.MIX_TYPES if totals.get(t)]
    timeline = TimeSeries(
        bucket=bucket,
        x=data.labels(),
        series=[Series(key=t, name=t, values=vals(data.series.get(t, []))) for t in present],
        unit="count",
    )
    chart = CategoryChart(
        categories=present,
        series=[
            Series(key="messages", name="Messages", values=[float(totals[t]) for t in present])
        ],
        unit="count",
        drilldowns=[MessageQuery(types=[t]) for t in present],  # type: ignore[list-item]
    )
    return timeline, chart


@router.get("/types", response_model=TypesResponse)
def get_types(ctx: Context) -> TypesResponse:
    """Message type mix over time (chart 12)."""

    def compute() -> TypesResponse:
        timeline, totals = type_mix(counted_messages(ctx))
        return TypesResponse(dataset_version=ctx.dataset_version, timeline=timeline, totals=totals)

    return ctx.cached("overview/types", compute)


@router.get("/size-distribution", response_model=SizeDistributionResponse)
def get_size_distribution(ctx: Context) -> SizeDistributionResponse:
    """Chats by number of messages, log bins (chart 14)."""

    def compute() -> SizeDistributionResponse:
        df = counted_messages(ctx).group_by("thread_id").agg(pl.len().alias("n")).collect()
        bins = C.size_distribution({int(t): int(n) for t, n in df.iter_rows()})
        out = [
            SizeBinOut(
                lo=b.lo,
                hi=b.hi,
                label=str(b.lo) if b.lo == b.hi else f"{b.lo}–{b.hi}",
                chats=b.count,
                drilldown=MessageQuery(thread_ids=b.thread_ids),
            )
            for b in bins
        ]
        return SizeDistributionResponse(
            dataset_version=ctx.dataset_version,
            bins=out,
            chart=CategoryChart(
                categories=[b.label for b in out],
                series=[Series(key="chats", name="Chats", values=[float(b.chats) for b in out])],
                unit="count",
                drilldowns=[b.drilldown for b in out],
            ),
        )

    return ctx.cached("overview/size-distribution", compute)


@router.get("/years", response_model=YearsResponse)
def get_years(ctx: Context) -> YearsResponse:
    """Year-over-year table (chart 17)."""

    def compute() -> YearsResponse:
        owner = ctx.owner_id if ctx.owner_id is not None else -1
        counted = counted_messages(ctx).with_columns(pl.col("local_date").dt.year().alias("_y"))
        base = (
            counted.group_by("_y")
            .agg(
                pl.len().alias("messages"),
                (pl.col("sender_id") == owner).sum().alias("sent"),
                pl.col("sender_id").filter(pl.col("sender_id") != owner).n_unique().alias("people"),
                pl.col("thread_id").n_unique().alias("chats"),
                pl.col("local_date").n_unique().alias("active_days"),
            )
            .sort("_y")
            .collect()
        )
        top_chats = (
            counted.group_by("_y", "thread_id")
            .agg(pl.len().alias("n"))
            .sort(["_y", "n", "thread_id"], descending=[False, True, False])
            .group_by("_y", maintain_order=True)
            .first()
            .collect()
        )
        top_chat = {int(y): (int(t), int(n)) for y, t, n in top_chats.iter_rows()}
        emo = C.emoji_counts(
            counted,
            by="_y",
            merge_skin_tones=ctx.settings.emoji_merge_skin_tones,
            merge_gendered=ctx.settings.emoji_merge_gendered,
        )
        top_emoji: dict[int, tuple[str, int]] = {}
        for y, e, n in emo.iter_rows():
            top_emoji.setdefault(int(y), (str(e), int(n)))
        refs = chat_refs(ctx)
        rows = []
        for r in base.iter_rows(named=True):
            y = int(r["_y"])
            tc = top_chat.get(y)
            te = top_emoji.get(y)
            rows.append(
                YearRow(
                    year=y,
                    messages=int(r["messages"]),
                    sent=int(r["sent"]),
                    received=int(r["messages"]) - int(r["sent"]),
                    people=int(r["people"]),
                    chats=int(r["chats"]),
                    active_days=int(r["active_days"]),
                    top_chat=refs.get(tc[0]) if tc else None,
                    top_chat_messages=tc[1] if tc else 0,
                    top_emoji=te[0] if te else None,
                    top_emoji_count=te[1] if te else 0,
                    drilldown=MessageQuery(start=date(y, 1, 1), end=date(y, 12, 31)),
                )
            )
        return YearsResponse(dataset_version=ctx.dataset_version, years=rows)

    return ctx.cached("overview/years", compute)
