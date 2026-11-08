"""Calls (spec §6.8, §7.2 Calls tab).

Input: filtered, chat-scoped messages. A call is a message with ``type == "call"``;
the caller is its ``sender_id``. Durations in the dataset are seconds
(``call_duration_s``); everything returned is in **milliseconds**. Duration statistics
use *connected* calls only (not missed and duration > 0); counts use all calls.
"""

from collections.abc import Callable, Sequence
from datetime import date
from typing import Any

import polars as pl

from instagraph.api.filters import bucket_labels, bucket_range, bucket_start
from instagraph.schemas.calls import CallItem, CallsResponse
from instagraph.schemas.common import (
    CategoryChart,
    HeatmapData,
    MessageQuery,
    PersonRef,
    Series,
    Tile,
    TimeSeries,
)

PersonRefFn = Callable[[int], PersonRef]

#: Duration histogram bins: (label, upper bound in seconds, exclusive; None = open).
DURATION_BINS: tuple[tuple[str, int | None], ...] = (
    ("< 1 min", 60),
    ("1–5 min", 300),
    ("5–15 min", 900),
    ("15–30 min", 1800),
    ("30–60 min", 3600),
    ("1–2 h", 7200),
    ("≥ 2 h", None),
)
WEEKDAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def call_frame(msgs: pl.LazyFrame) -> pl.DataFrame:
    """Calls with ``duration_ms`` and ``connected`` columns, chronological."""
    return (
        msgs.filter(pl.col("type") == "call")
        .with_columns(
            (pl.col("call_duration_s").cast(pl.Int64) * 1000).alias("duration_ms"),
            (
                ~pl.col("call_missed").fill_null(False)
                & (pl.col("call_duration_s").fill_null(0) > 0)
            ).alias("connected"),
        )
        .sort("message_id")
        .collect()
    )


def bin_index(seconds: int) -> int:
    for i, (_, upper) in enumerate(DURATION_BINS):
        if upper is None or seconds < upper:
            return i
    return len(DURATION_BINS) - 1


def _median(values: list[int]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return float(s[mid]) if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _item(row: dict[str, Any], ref: PersonRefFn) -> CallItem:
    dur = row["duration_ms"]
    return CallItem(
        message_id=int(row["message_id"]),
        ts=row["ts"],
        caller=ref(int(row["sender_id"])),
        kind=row["call_kind"],
        duration_ms=int(dur) if dur is not None else None,
        missed=bool(row["call_missed"]),
    )


def _per_person_chart(
    counts: dict[int, int], ref: PersonRefFn, people: Sequence[int], name: str
) -> CategoryChart:
    order = list(people) + sorted(p for p in counts if p not in people)
    refs = [ref(p) for p in order]
    return CategoryChart(
        categories=[r.name for r in refs],
        series=[Series(key=name, name=name, values=[float(counts.get(p, 0)) for p in order])],
        unit="count",
    )


def analyze_calls(
    msgs: pl.LazyFrame,
    ref: PersonRefFn,
    *,
    chat_id: int,
    dataset_version: int,
    people: Sequence[int] = (),
) -> CallsResponse:
    df = call_frame(msgs)
    rows = list(df.iter_rows(named=True))
    connected = [r for r in rows if r["connected"]]
    durations = [int(r["duration_ms"]) for r in connected]
    total_ms = sum(durations)
    kinds = [r["call_kind"] for r in rows]
    n_missed = sum(1 for r in rows if r["call_missed"])
    longest_rows = sorted(connected, key=lambda r: (-int(r["duration_ms"]), r["message_id"]))
    longest = _item(longest_rows[0], ref) if longest_rows else None
    base = MessageQuery(thread_id=chat_id, types=["call"])

    # per month
    months: list[date] = []
    if rows:
        lo, hi = df["local_date"].min(), df["local_date"].max()
        assert isinstance(lo, date) and isinstance(hi, date)
        months = bucket_range(lo, hi, "month")
    bucketed = (
        df.with_columns(bucket_start(pl.col("local_date"), "month").alias("b"))
        .group_by("b")
        .agg(
            pl.len().alias("n"),
            pl.when(pl.col("connected")).then(pl.col("duration_ms")).otherwise(0).sum().alias("ms"),
        )
        if rows
        else pl.DataFrame({"b": [], "n": [], "ms": []})
    )
    by_month = {b: (int(n), int(ms)) for b, n, ms in bucketed.iter_rows()}
    per_month = TimeSeries(
        bucket="month",
        x=bucket_labels(months),
        series=[
            Series(
                key="calls",
                name="Calls",
                values=[float(by_month.get(m, (0, 0))[0]) for m in months],
            ),
            Series(
                key="minutes",
                name="Minutes",
                values=[round(by_month.get(m, (0, 0))[1] / 60_000, 2) for m in months],
            ),
        ],
        unit="count",
    )

    starts: dict[int, int] = {}
    missed: dict[int, int] = {}
    heat = [[0.0] * 24 for _ in range(7)]
    hist = [0] * len(DURATION_BINS)
    for r in rows:
        pid = int(r["sender_id"])
        starts[pid] = starts.get(pid, 0) + 1
        if r["call_missed"]:
            missed[pid] = missed.get(pid, 0) + 1
        heat[int(r["weekday"]) - 1][int(r["hour"])] += 1
    for d in durations:
        hist[bin_index(d // 1000)] += 1

    tiles = [
        Tile(key="calls_total", label="Calls", value=len(rows), unit="count", drilldown=base),
        Tile(
            key="calls_audio",
            label="Audio calls",
            value=kinds.count("audio"),
            unit="count",
            drilldown=base.model_copy(update={"call_kind": "audio"}),
        ),
        Tile(
            key="calls_video",
            label="Video calls",
            value=kinds.count("video"),
            unit="count",
            drilldown=base.model_copy(update={"call_kind": "video"}),
        ),
        Tile(key="calls_total_time", label="Total call time", value=total_ms, unit="ms"),
        Tile(
            key="calls_avg",
            label="Average call",
            value=total_ms / len(durations) if durations else None,
            unit="ms",
        ),
        Tile(key="calls_median", label="Median call", value=_median(durations), unit="ms"),
        Tile(
            key="calls_longest",
            label="Longest call",
            value=longest.duration_ms if longest else None,
            unit="ms",
            detail=longest.ts.date().isoformat() if longest else None,
            person=longest.caller if longest else None,
            drilldown=MessageQuery(message_ids=[longest.message_id]) if longest else None,
        ),
        Tile(
            key="calls_missed",
            label="Missed calls",
            value=n_missed,
            unit="count",
            drilldown=base.model_copy(update={"call_missed": True}),
        ),
    ]
    return CallsResponse(
        dataset_version=dataset_version,
        chat_id=chat_id,
        tiles=tiles,
        total_calls=len(rows),
        audio_calls=kinds.count("audio"),
        video_calls=kinds.count("video"),
        connected_calls=len(connected),
        missed_calls=n_missed,
        total_ms=total_ms,
        avg_ms=total_ms / len(durations) if durations else None,
        median_ms=_median(durations),
        longest=longest,
        per_month=per_month,
        starters=_per_person_chart(starts, ref, [p for p in people if p in starts], "calls"),
        missed_per_person=_per_person_chart(
            missed, ref, [p for p in people if p in missed], "missed"
        ),
        duration_histogram=CategoryChart(
            categories=[label for label, _ in DURATION_BINS],
            series=[Series(key="calls", name="Calls", values=[float(h) for h in hist])],
            unit="count",
        ),
        heatmap=HeatmapData(
            x_labels=[str(h) for h in range(24)],
            y_labels=list(WEEKDAY_LABELS),
            values=heat,
            unit="count",
        ),
        longest_calls=[_item(r, ref) for r in longest_rows[:10]],
        drilldown=base,
    )
