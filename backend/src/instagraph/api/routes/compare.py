"""``GET /api/compare?chats=1,2`` or ``?people=3,4`` — 2–4 items side by side (§5.8).

* chats mode: each item is every counted message of that chat (global ``people`` filter
  is not available here because ``people`` names the compared items in people mode).
* people mode: each item is that person's messages across all chats in scope.

Series are normalized so items of different size overlay: monthly messages per day,
hour / weekday distribution as shares, emojis per message and median reply time.
"""

from datetime import date, datetime
from typing import Annotated

import polars as pl
from fastapi import APIRouter, Query

from instagraph.analysis.person_metrics import (
    hour_shares,
    metrics_by_person,
    monthly_profile,
    person_metrics,
    scope_summary,
    weekday_shares,
)
from instagraph.api.deps import AnalysisContext, Context
from instagraph.api.filters import bucket_labels, bucket_range
from instagraph.api.routes._insights_common import ensure_person, ensure_thread, scoped_frames
from instagraph.errors import ERROR_RESPONSES, ValidationApiError
from instagraph.schemas.common import (
    CategoryChart,
    MessageQuery,
    Scalar,
    Series,
    TimeSeries,
    Unit,
)
from instagraph.schemas.compare import CompareItem, CompareMetric, CompareResponse

router = APIRouter(tags=["compare"], responses=ERROR_RESPONSES)

MIN_ITEMS = 2
MAX_ITEMS = 4
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

#: (key, label, unit, lower_is_better) of the shared table rows (from ``scope_summary``).
SUMMARY_ROWS: tuple[tuple[str, str, Unit, bool], ...] = (
    ("messages", "Messages", "count", False),
    ("words", "Words", "count", False),
    ("avg_words", "Avg words / message", "per_message", False),
    ("emojis_per_message", "Emojis / message", "per_message", False),
    ("emoji_message_share", "Messages with emoji", "ratio", False),
    ("active_days", "Active days", "count", False),
    ("messages_per_active_day", "Messages / active day", "per_day", False),
    ("reply_median_ms", "Median reply time", "ms", True),
    ("night_share", "Night owl (00–05)", "ratio", False),
    ("early_share", "Early bird (05–08)", "ratio", False),
    ("weekend_share", "Weekend share", "ratio", False),
    ("question_rate", "Question rate", "ratio", False),
    ("laugh_rate", "Laughter rate", "ratio", False),
    ("reactions_per_message", "Reactions received / message", "per_message", False),
    ("media", "Photos & videos", "count", False),
    ("reels_posts", "Reels & posts shared", "count", False),
    ("voice_notes", "Voice notes", "count", False),
    ("calls", "Calls", "count", False),
    ("call_ms", "Call time", "ms", False),
    ("first_ts", "First message", "datetime", False),
    ("last_ts", "Last message", "datetime", False),
)
PERSON_ROWS: tuple[tuple[str, str, Unit, bool], ...] = (
    ("initiation_rate", "Initiation rate", "ratio", False),
    ("msgs_per_turn", "Messages / turn", "per_message", False),
    ("reply_rate", "Reply rate", "ratio", False),
    ("unanswered_turns", "Unanswered turns", "count", True),
    ("mattr", "Lexical diversity (MATTR)", "ratio", False),
)


def _parse_ids(value: str, name: str) -> list[int]:
    try:
        ids = [int(p) for p in value.split(",") if p.strip()]
    except ValueError as exc:
        raise ValidationApiError(f"{name} must be a comma-separated list of ids") from exc
    unique = list(dict.fromkeys(ids))
    if not MIN_ITEMS <= len(unique) <= MAX_ITEMS:
        raise ValidationApiError(f"Pick {MIN_ITEMS}–{MAX_ITEMS} distinct {name} to compare.")
    return unique


def _scalar(v: object) -> Scalar:
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, bool | int | float | str) or v is None:
        return v
    return str(v)


def _time_series(
    profiles: list[pl.DataFrame], items: list[CompareItem], col: str, unit: Unit
) -> TimeSeries:
    months: list[date] = [m for p in profiles for m in p["month"].to_list()]
    x = bucket_range(min(months), max(months), "month") if months else []
    series = []
    for item, prof in zip(items, profiles, strict=True):
        by_month = dict(zip(prof["month"].to_list(), prof[col].to_list(), strict=True))
        values: list[float | None] = []
        for m in x:
            v = by_month.get(m)
            values.append(float(v) if v is not None else (0.0 if col == "per_day" else None))
        series.append(
            Series(
                key=item.key,
                name=item.name,
                person_id=item.person.person_id if item.person else None,
                color_index=item.color_index,
                values=values,
            )
        )
    return TimeSeries(bucket="month", x=bucket_labels(x), series=series, unit=unit)


def _category(
    categories: list[str], items: list[CompareItem], values: list[list[float]]
) -> CategoryChart:
    return CategoryChart(
        categories=categories,
        unit="ratio",
        series=[
            Series(
                key=it.key,
                name=it.name,
                person_id=it.person.person_id if it.person else None,
                color_index=it.color_index,
                values=list(v),
            )
            for it, v in zip(items, values, strict=True)
        ],
    )


def _compare(ctx: AnalysisContext, mode: str, ids: list[int]) -> CompareResponse:
    f = ctx.filters.model_copy(update={"people": None})
    msgs, rx = scoped_frames(ctx, f)
    items: list[CompareItem] = []
    frames: list[pl.DataFrame] = []
    extra_rows: list[CompareMetric] = []
    threads = ctx.dataset.threads_df()
    if mode == "chats":
        for i, tid in enumerate(ids):
            row = threads.filter(pl.col("thread_id") == tid).row(0, named=True)
            items.append(
                CompareItem(
                    key=f"chat:{tid}",
                    kind="chat",
                    id=tid,
                    name=ctx.thread_title(tid),
                    color_index=i,
                    is_group=bool(row["is_group"]),
                    drilldown=MessageQuery(thread_id=tid),
                )
            )
            frames.append(msgs.filter(pl.col("thread_id") == tid).collect())
        owner = ctx.owner_id
        participants: list[Scalar] = []
        my_share: list[Scalar] = []
        for tid, fr in zip(ids, frames, strict=True):
            parts = threads.filter(pl.col("thread_id") == tid)["participants"].to_list()[0]
            participants.append(len(parts or []))
            counted = fr.filter(~pl.col("type").cast(pl.String).is_in(["system", "like"]))
            mine = counted.filter(pl.col("sender_id") == owner).height if owner is not None else 0
            my_share.append(mine / counted.height if counted.height else None)
        extra_rows = [
            CompareMetric(
                key="participants", label="Participants", unit="count", values=participants
            ),
            CompareMetric(key="my_share", label="My share", unit="ratio", values=my_share),
        ]
    else:
        for pid in ids:
            ref = ctx.person_ref(pid)
            items.append(
                CompareItem(
                    key=f"person:{pid}",
                    kind="person",
                    id=pid,
                    name=ref.name,
                    color_index=ref.color_index,
                    person=ref,
                    drilldown=MessageQuery(sender_id=pid),
                )
            )
            frames.append(msgs.filter(pl.col("sender_id") == pid).collect())
        pm = metrics_by_person(
            person_metrics(msgs, rx, laughter_patterns=ctx.settings.laughter_patterns, people=ids)
        )
        extra_rows = [
            CompareMetric(
                key=key,
                label=label,
                unit=unit,
                lower_is_better=low,
                values=[_scalar(pm.get(pid, {}).get(key)) for pid in ids],
            )
            for key, label, unit, low in PERSON_ROWS
        ]

    rx_df = rx.collect()
    summaries = [
        scope_summary(fr, rx_df, laughter_patterns=ctx.settings.laughter_patterns) for fr in frames
    ]
    metrics = [
        CompareMetric(
            key=key,
            label=label,
            unit=unit,
            lower_is_better=low,
            values=[_scalar(s[key]) for s in summaries],
        )
        for key, label, unit, low in SUMMARY_ROWS
    ]
    metrics += extra_rows
    profiles = [monthly_profile(fr) for fr in frames]
    return CompareResponse(
        dataset_version=ctx.dataset_version,
        mode="chats" if mode == "chats" else "people",
        items=items,
        metrics=metrics,
        messages_per_day=_time_series(profiles, items, "per_day", "per_day"),
        hours=_category([str(h) for h in range(24)], items, [hour_shares(fr) for fr in frames]),
        weekdays=_category(WEEKDAYS, items, [weekday_shares(fr) for fr in frames]),
        emoji_rate=_time_series(profiles, items, "emoji_rate", "per_message"),
        reply_median=_time_series(profiles, items, "reply_median_ms", "ms"),
    )


@router.get("/api/compare", response_model=CompareResponse)
def compare(
    ctx: Context,
    chats: Annotated[str | None, Query(description="2–4 comma-separated chat ids")] = None,
    people: Annotated[str | None, Query(description="2–4 comma-separated person ids")] = None,
) -> CompareResponse:
    """Side-by-side metrics and overlaid normalized charts for 2–4 chats or people."""
    if (chats is None) == (people is None):
        raise ValidationApiError("Pass exactly one of `chats` or `people`.")
    if chats is not None:
        mode, ids = "chats", _parse_ids(chats, "chats")
        for tid in ids:
            ensure_thread(ctx, tid)
    else:
        mode, ids = "people", _parse_ids(people or "", "people")
        for pid in ids:
            ensure_person(ctx, pid)
    return ctx.cached("compare", lambda: _compare(ctx, mode, ids), extra=(mode, tuple(ids)))
