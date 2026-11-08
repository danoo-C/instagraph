"""``GET /api/chats/{chat_id}/dynamics`` — the Dynamics tab (spec §6.4, §6.9, §7.2).

Scope rules (shared with ``chat_reactions``):

* The chat is opened explicitly, so the thread-category filters (groups / requests /
  archived) don't hide it; date / system / people filters apply.
* Conversation structure (sessions, turns, replies) needs both sides, so it is computed
  over everyone in the chat; the ``people`` filter only narrows the per-person rows.
* Sessions / turns / reply latencies are derived at import with the session gap and
  reply window from settings. A date filter keeps those derived values as-is (no
  recomputation): sessions crossing the range edge are cut to their visible part.
"""

from collections.abc import Iterable, Sequence
from datetime import date

import polars as pl
from fastapi import APIRouter

from instagraph.analysis import replies as rp
from instagraph.analysis import sessions as ss
from instagraph.api.deps import AnalysisContext, Context
from instagraph.api.filters import (
    FilterParams,
    bucket_labels,
    bucket_range,
    bucket_start,
)
from instagraph.errors import ERROR_RESPONSES
from instagraph.schemas.common import (
    CategoryChart,
    MessageQuery,
    MessageRef,
    Series,
    TimeSeries,
)
from instagraph.schemas.dynamics import (
    ConversationStarter,
    DynamicsResponse,
    EffortPart,
    LongestTurn,
    LongestWait,
    PersonEffort,
    PersonReplyStats,
    PersonSessionStats,
    PersonTurnStats,
    SessionRef,
    SessionStats,
)

router = APIRouter(prefix="/api/chats", tags=["chats"], responses=ERROR_RESPONSES)

SNIPPET_CHARS = 280
MAX_DRILLDOWN_IDS = 500

# --------------------------------------------------------------------------------------
# Shared helpers (also used by chat_reactions)
# --------------------------------------------------------------------------------------


def ensure_chat(ctx: AnalysisContext, chat_id: int) -> None:
    """Raise 404 when ``chat_id`` is not a thread of the dataset."""
    ctx.ensure_chat(chat_id)


def chat_filters(ctx: AnalysisContext, *, keep_people: bool = False) -> FilterParams:
    """``ctx.chat_filters()`` (category flags opened), people filter dropped unless
    ``keep_people``."""
    f = ctx.chat_filters()
    return f if keep_people else f.model_copy(update={"people": None})


def chat_messages(ctx: AnalysisContext, chat_id: int) -> pl.DataFrame:
    """All filtered messages of the chat, people filter NOT applied (eager)."""
    return ctx.chat_messages(chat_id, people=False).collect()


def ordered_people(ctx: AnalysisContext, ids: Iterable[int | None]) -> list[int]:
    """Distinct ids passing the people filter, ordered by stable color slot."""
    wanted = ctx.filters.people
    uniq = {int(i) for i in ids if i is not None}
    if wanted is not None:
        uniq &= set(wanted)
    return sorted(uniq, key=lambda p: (ctx.person_ref(p).color_index, p))


def snippet(ctx: AnalysisContext, text: str | None) -> str | None:
    if text is None:
        return None
    if len(text) > SNIPPET_CHARS:
        text = text[: SNIPPET_CHARS - 1] + "…"
    return ctx.text(text)


def message_refs(ctx: AnalysisContext, ids: Iterable[int | None]) -> dict[int, MessageRef]:
    """``message_id -> MessageRef`` read from the unfiltered messages table."""
    wanted = sorted({int(i) for i in ids if i is not None})
    if not wanted:
        return {}
    df = (
        ctx.dataset.messages.filter(pl.col("message_id").is_in(wanted))
        .select("message_id", "thread_id", "sender_id", "ts", "type", "text")
        .collect()
    )
    return {
        int(r["message_id"]): MessageRef(
            message_id=int(r["message_id"]),
            thread_id=int(r["thread_id"]),
            sender=ctx.person_ref(int(r["sender_id"])),
            ts=r["ts"],
            type=str(r["type"]),  # type: ignore[arg-type]
            text=snippet(ctx, r["text"]),
        )
        for r in df.iter_rows(named=True)
    }


def month_axis(dates: pl.Series) -> list[date]:
    """Contiguous month bucket starts covering ``dates`` (empty when no dates)."""
    clean = dates.drop_nulls()
    if clean.is_empty():
        return []
    lo, hi = clean.min(), clean.max()
    assert isinstance(lo, date) and isinstance(hi, date)
    return bucket_range(lo, hi, "month")


def person_series(
    ctx: AnalysisContext,
    people: Sequence[int],
    values: dict[int, list[float | None]],
) -> list[Series]:
    out: list[Series] = []
    for pid in people:
        ref = ctx.person_ref(pid)
        out.append(
            Series(
                key=str(pid),
                name=ref.name,
                person_id=pid,
                color_index=ref.color_index,
                values=values.get(pid, []),
            )
        )
    return out


def bucketed_by_person(
    df: pl.DataFrame,
    agg: pl.Expr,
    x: list[date],
    people: Sequence[int],
    *,
    person_col: str = "sender_id",
    fill: float | None = 0.0,
) -> dict[int, list[float | None]]:
    """``agg`` per (person, month of ``local_date``) aligned to ``x``."""
    result: dict[int, list[float | None]] = {p: [fill] * len(x) for p in people}
    if df.is_empty() or not x:
        return result
    pos = {d: i for i, d in enumerate(x)}
    grouped = (
        df.with_columns(bucket_start(pl.col("local_date"), "month").alias("_b"))
        .group_by(person_col, "_b")
        .agg(agg.alias("_v"))
    )
    for pid, b, v in grouped.iter_rows():
        if pid in result and b in pos and v is not None:
            result[pid][pos[b]] = float(v)
    return result


def _f(v: object) -> float | None:
    return None if v is None else float(v)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# Dynamics
# --------------------------------------------------------------------------------------

_DURATION_LABELS = [
    "0",
    "≤1 min",
    "≤5 min",
    "≤15 min",
    "≤30 min",
    "≤1 h",
    "≤2 h",
    "≤4 h",
    "≤8 h",
    ">8 h",
]


def fmt_duration(ms: int) -> str:
    """Compact label: ``1 s``, ``30 s``, ``5 min``, ``12 h``, ``2 d``."""
    s = ms / 1000
    if s < 60:
        return f"{s:g} s"
    if s < 3600:
        return f"{s / 60:g} min"
    if s < 86400:
        return f"{s / 3600:g} h"
    return f"{s / 86400:g} d"


def _length_labels(edges: list[int]) -> list[str]:
    labels: list[str] = []
    prev = 0
    for e in edges:
        labels.append(str(e) if e == prev + 1 else f"{prev + 1}–{e}")
        prev = e
    return labels


def _session_ref(
    ctx: AnalysisContext, row: dict[str, object], refs: dict[int, MessageRef], chat_id: int
) -> SessionRef:
    initiator = row["initiator_id"]
    first_id = int(row["first_message_id"])  # type: ignore[call-overload]
    return SessionRef(
        session_id=int(row["session_id"]),  # type: ignore[call-overload]
        start=row["start_ts"],  # type: ignore[arg-type]
        end=row["end_ts"],  # type: ignore[arg-type]
        duration_ms=int(row["duration_ms"]),  # type: ignore[call-overload]
        messages=int(row["messages"]),  # type: ignore[call-overload]
        initiator=None if initiator is None else ctx.person_ref(int(initiator)),  # type: ignore[call-overload]
        first_message=refs.get(first_id),
        drilldown=MessageQuery(thread_id=chat_id, session_id=int(row["session_id"])),  # type: ignore[call-overload]
    )


def compute_dynamics(ctx: AnalysisContext, chat_id: int) -> DynamicsResponse:
    settings = ctx.settings
    window_ms = settings.reply_window_ms
    msgs = chat_messages(ctx, chat_id)
    c = ss.counted(msgs).collect()
    people = ordered_people(ctx, c["sender_id"].to_list())
    names = [ctx.person_ref(p).name for p in people]
    q = {p: MessageQuery(thread_id=chat_id, sender_id=p) for p in people}
    x_months = month_axis(c["local_date"])
    x_labels = bucket_labels(x_months)

    # ---- sessions ---------------------------------------------------------------
    sessions = ss.session_table(c)
    summary = ss.session_summary(sessions)
    longest_row = most_row = None
    if not sessions.is_empty():
        longest_row = sessions.sort(
            ["duration_ms", "first_message_id"], descending=[True, False]
        ).row(0, named=True)
        most_row = sessions.sort(["messages", "first_message_id"], descending=[True, False]).row(
            0, named=True
        )
    refs = message_refs(
        ctx, [r["first_message_id"] for r in (longest_row, most_row) if r is not None]
    )
    session_stats = SessionStats(
        count=summary.count,
        avg_messages=summary.avg_messages,
        median_messages=summary.median_messages,
        avg_duration_ms=summary.avg_duration_ms,
        median_duration_ms=summary.median_duration_ms,
        longest=None if longest_row is None else _session_ref(ctx, longest_row, refs, chat_id),
        most_messages=None if most_row is None else _session_ref(ctx, most_row, refs, chat_id),
        drilldown=MessageQuery(thread_id=chat_id),
    )
    len_values = sessions["messages"].to_list()
    len_edges = ss.session_length_edges(max(len_values, default=1))
    len_counts = ss.bin_counts(len_values, len_edges)[: len(len_edges)]
    dur_counts = ss.bin_counts(sessions["duration_ms"].to_list(), ss.SESSION_DURATION_EDGES_MS)

    init = {int(r[0]): r for r in ss.initiation_rates(c).iter_rows()}
    last = {int(r[0]): r for r in ss.last_word_counts(c).iter_rows()}
    person_sessions = [
        PersonSessionStats(
            person=ctx.person_ref(p),
            sessions_started=int(init[p][1]) if p in init else 0,
            initiation_rate=float(init[p][2]) if p in init else 0.0,
            last_words=int(last[p][1]) if p in last else 0,
            last_word_rate=float(last[p][2]) if p in last else 0.0,
            drilldown=q[p],
        )
        for p in people
    ]
    starts = c.filter(pl.col("is_session_start"))
    initiators_over_time = TimeSeries(
        bucket="month",
        x=x_labels,
        series=person_series(ctx, people, bucketed_by_person(starts, pl.len(), x_months, people)),
        unit="count",
    )

    # ---- turns ------------------------------------------------------------------
    tstats = {int(r["sender_id"]): r for r in ss.turn_stats(c).iter_rows(named=True)}
    turns = [
        PersonTurnStats(
            person=ctx.person_ref(p),
            turns=int(tstats[p]["turns"]),
            messages=int(tstats[p]["messages"]),
            avg_messages_per_turn=float(tstats[p]["avg_messages_per_turn"]),
            double_texts=int(tstats[p]["double_texts"]),
            triple_texts=int(tstats[p]["triple_texts"]),
            drilldown=q[p],
        )
        for p in people
        if p in tstats
    ]
    turn_people = [t.person.person_id for t in turns]
    turns_chart = CategoryChart(
        categories=[t.person.name for t in turns],
        series=[
            Series(
                key="avg_messages_per_turn",
                name="Messages per turn",
                values=[t.avg_messages_per_turn for t in turns],
            ),
            Series(key="double_texts", name="Double texts", values=[t.double_texts for t in turns]),
            Series(key="triple_texts", name="Triple texts", values=[t.triple_texts for t in turns]),
        ],
        drilldowns=[q[p] for p in turn_people],
    )
    longest_turn: LongestTurn | None = None
    tt = ss.turn_table(c)
    if people:
        tt_people = tt.filter(pl.col("sender_id").is_in(people))
        if not tt_people.is_empty():
            lt = tt_people.sort(["messages", "first_message_id"], descending=[True, False]).row(
                0, named=True
            )
            ids = c.filter(pl.col("turn_id") == lt["turn_id"])["message_id"].to_list()
            first_ref = message_refs(ctx, [lt["first_message_id"]])[int(lt["first_message_id"])]
            longest_turn = LongestTurn(
                person=ctx.person_ref(int(lt["sender_id"])),
                messages=int(lt["messages"]),
                duration_ms=int((lt["last_ts"] - lt["first_ts"]).total_seconds() * 1000),
                first_message=first_ref,
                drilldown=MessageQuery(thread_id=chat_id, message_ids=ids[:MAX_DRILLDOWN_IDS]),
            )

    # ---- conversation starters --------------------------------------------------
    starter_src = starts.filter(pl.col("sender_id").is_in(people)) if people else starts.head(0)
    starters = [
        ConversationStarter(
            text=ctx.text(r["text"]),
            count=int(r["count"]),
            drilldown=MessageQuery(
                thread_id=chat_id, message_ids=list(r["message_ids"])[:MAX_DRILLDOWN_IDS]
            ),
        )
        for r in ss.conversation_starters(starter_src, limit=10).iter_rows(named=True)
    ]

    # ---- replies ----------------------------------------------------------------
    lat = {int(r["sender_id"]): r for r in rp.reply_latency_stats(c).iter_rows(named=True)}
    una = {int(r["sender_id"]): r for r in rp.unanswered_turns(c, window_ms).iter_rows(named=True)}
    reply_rows: list[PersonReplyStats] = []
    for p in people:
        lr = lat.get(p)
        ur = una.get(p)
        reply_rows.append(
            PersonReplyStats(
                person=ctx.person_ref(p),
                replies=int(lr["replies"]) if lr else 0,
                median_ms=_f(lr["median_ms"]) if lr else None,
                mean_ms=_f(lr["mean_ms"]) if lr else None,
                p25_ms=_f(lr["p25_ms"]) if lr else None,
                p75_ms=_f(lr["p75_ms"]) if lr else None,
                p90_ms=_f(lr["p90_ms"]) if lr else None,
                fastest_ms=int(lr["fastest_ms"]) if lr else None,
                slowest_ms=int(lr["slowest_ms"]) if lr else None,
                turns=int(ur["turns"]) if ur else 0,
                replied_turns=int(ur["replied"]) if ur else 0,
                reply_rate=_f(ur["reply_rate"]) if ur else None,
                unanswered_turns=int(ur["unanswered"]) if ur else 0,
                drilldown=q[p],
            )
        )
    edges = rp.latency_bin_edges(window_ms)
    hist: dict[int, list[float | None]] = {p: [0.0] * len(edges) for p in people}
    for pid, b, n in rp.latency_histogram(c, edges).iter_rows():
        if pid in hist:
            hist[pid][int(b)] = float(n)
    reply_histogram = CategoryChart(
        categories=[f"≤{fmt_duration(e)}" for e in edges],
        series=person_series(ctx, people, hist),
        unit="count",
    )
    reply_msgs = rp.replies(c).collect()
    median_over_time = TimeSeries(
        bucket="month",
        x=x_labels,
        series=person_series(
            ctx,
            people,
            bucketed_by_person(
                reply_msgs, pl.col("reply_latency_ms").median(), x_months, people, fill=None
            ),
        ),
        unit="ms",
    )
    by_hour: dict[int, list[float | None]] = {p: [None] * 24 for p in people}
    for pid, h, v in (
        reply_msgs.group_by("sender_id", "hour")
        .agg(pl.col("reply_latency_ms").median())
        .iter_rows()
    ):
        if pid in by_hour and v is not None:
            by_hour[pid][int(h)] = float(v)
    reply_by_hour = CategoryChart(
        categories=[str(h) for h in range(24)],
        series=person_series(ctx, people, by_hour),
        unit="ms",
    )
    lw_src = c.with_columns(
        pl.when(pl.col("sender_id").is_in(people))
        .then(pl.col("reply_latency_ms"))
        .otherwise(None)
        .alias("reply_latency_ms")
    )
    lw = rp.longest_answered_wait(lw_src)
    longest_wait: LongestWait | None = None
    if lw is not None:
        wrefs = message_refs(ctx, [lw.reply_message_id, lw.before_message_id])
        longest_wait = LongestWait(
            latency_ms=lw.latency_ms,
            replier=ctx.person_ref(lw.replier_id),
            waited_for=None if lw.waited_for_id is None else ctx.person_ref(lw.waited_for_id),
            before=None if lw.before_message_id is None else wrefs.get(lw.before_message_id),
            reply=wrefs[lw.reply_message_id],
            drilldown=MessageQuery(
                thread_id=chat_id,
                message_ids=[
                    i for i in (lw.before_message_id, lw.reply_message_id) if i is not None
                ],
            ),
        )

    # ---- effort -----------------------------------------------------------------
    effort: list[PersonEffort] = []
    for r in rp.effort_index(c).iter_rows(named=True):
        pid = int(r["sender_id"])
        if pid not in people:
            continue
        effort.append(
            PersonEffort(
                person=ctx.person_ref(pid),
                parts=[
                    EffortPart(
                        key="share",
                        label="Share of messages",
                        value=_f(r["share"]),
                        unit="ratio",
                        z=_f(r["z_share"]),
                    ),
                    EffortPart(
                        key="initiation_rate",
                        label="Initiation rate",
                        value=_f(r["initiation_rate"]),
                        unit="ratio",
                        z=_f(r["z_initiation"]),
                    ),
                    EffortPart(
                        key="reply_speed",
                        label="Reply speed (median reply time)",
                        value=_f(r["median_reply_ms"]),
                        unit="ms",
                        z=_f(r["z_reply_speed"]),
                    ),
                    EffortPart(
                        key="avg_length",
                        label="Average words per message",
                        value=_f(r["avg_words"]),
                        unit="count",
                        z=_f(r["z_length"]),
                    ),
                ],
                combined_z=_f(r["combined_z"]),
            )
        )
    effort.sort(key=lambda e: people.index(e.person.person_id))

    def donut(values: list[int]) -> CategoryChart:
        return CategoryChart(
            categories=names,
            series=[Series(key="sessions", name="Sessions", values=[float(v) for v in values])],
            unit="count",
            drilldowns=[q[p] for p in people],
        )

    return DynamicsResponse(
        dataset_version=ctx.dataset_version,
        chat_id=chat_id,
        session_gap_ms=settings.session_gap_ms,
        reply_window_ms=window_ms,
        notes=[
            "Sessions, turns and reply latencies are derived at import with the session gap "
            "and reply window from settings; date filters do not recompute them.",
            "Conversation structure uses everyone in the chat; the people filter only "
            "narrows the per-person rows.",
            "Double/triple texts: turns made of 2 / 3+ bursts, where a new burst starts after "
            "more than 1 minute without a message.",
        ],
        sessions=session_stats,
        session_length_histogram=CategoryChart(
            categories=_length_labels(len_edges),
            series=[Series(key="sessions", name="Sessions", values=[float(v) for v in len_counts])],
            unit="count",
        ),
        session_duration_histogram=CategoryChart(
            categories=_DURATION_LABELS,
            series=[Series(key="sessions", name="Sessions", values=[float(v) for v in dur_counts])],
            unit="count",
        ),
        people=person_sessions,
        initiators=donut([s.sessions_started for s in person_sessions]),
        initiators_over_time=initiators_over_time,
        last_word=donut([s.last_words for s in person_sessions]),
        turns=turns,
        turns_chart=turns_chart,
        longest_turn=longest_turn,
        starters=starters,
        replies=reply_rows,
        reply_histogram=reply_histogram,
        reply_histogram_edges_ms=edges,
        reply_median_over_time=median_over_time,
        reply_median_by_hour=reply_by_hour,
        longest_wait=longest_wait,
        effort=effort,
    )


@router.get("/{chat_id}/dynamics", response_model=DynamicsResponse)
def chat_dynamics(chat_id: int, ctx: Context) -> DynamicsResponse:
    """Sessions, initiations, turns, reply times and effort index of one chat."""
    ctx = ctx.for_chat(chat_id)
    return ctx.cached("chat_dynamics", lambda: compute_dynamics(ctx, chat_id), extra=chat_id)


__all__ = [
    "bucketed_by_person",
    "chat_filters",
    "chat_messages",
    "ensure_chat",
    "message_refs",
    "month_axis",
    "ordered_people",
    "person_series",
]
