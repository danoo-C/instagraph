"""Streaks and gaps (spec §6.3).

* Daily streak = consecutive local calendar days with ≥ 1 message.
* Mutual streak = consecutive days where every given participant sent ≥ 1 message.
* Longest silence = largest gap between two consecutive messages of a frame (call it
  on one chat; across chats the "gap" would mix conversations).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import polars as pl


@dataclass(frozen=True)
class Streak:
    length: int = 0
    start: date | None = None
    end: date | None = None


@dataclass(frozen=True)
class Gap:
    """Silence between message ``before_id`` (at ``start``) and ``after_id`` (at ``end``)."""

    gap_ms: int
    before_id: int
    after_id: int
    start: datetime
    end: datetime


def _runs(dates: Iterable[date]) -> list[Streak]:
    days = sorted(set(dates))
    runs: list[Streak] = []
    if not days:
        return runs
    start = prev = days[0]
    for d in days[1:]:
        if d - prev != timedelta(days=1):
            runs.append(Streak((prev - start).days + 1, start, prev))
            start = d
        prev = d
    runs.append(Streak((prev - start).days + 1, start, prev))
    return runs


def longest_streak(dates: Iterable[date]) -> Streak:
    """Longest run of consecutive days (ties → the earliest run)."""
    best = Streak()
    for run in _runs(dates):
        if run.length > best.length:
            best = run
    return best


def current_streak(dates: Iterable[date], today: date) -> Streak:
    """Run ending ``today`` or yesterday (still alive); otherwise an empty streak."""
    runs = _runs(dates)
    if runs and runs[-1].end is not None and (today - runs[-1].end).days in (0, 1):
        return runs[-1]
    return Streak()


def mutual_dates(lf: pl.LazyFrame, participants: Sequence[int]) -> list[date]:
    """Days on which every id in ``participants`` sent ≥ 1 row of ``lf``."""
    ids = sorted(set(participants))
    if not ids:
        return []
    df = (
        lf.filter(pl.col("sender_id").is_in(ids))
        .group_by("local_date")
        .agg(pl.col("sender_id").n_unique().alias("k"))
        .filter(pl.col("k") == len(ids))
        .sort("local_date")
        .collect()
    )
    out: list[date] = df["local_date"].to_list()
    return out


def longest_gaps(lf: pl.LazyFrame, n: int = 1) -> list[Gap]:
    """The ``n`` largest gaps between consecutive rows of ``lf`` (ordered by
    ``message_id`` = chronological), largest first."""
    df = (
        lf.select("message_id", "ts")
        .sort("message_id")
        .with_columns(
            pl.col("message_id").shift(1).alias("prev_id"),
            pl.col("ts").shift(1).alias("prev_ts"),
        )
        .drop_nulls("prev_id")
        .with_columns((pl.col("ts") - pl.col("prev_ts")).dt.total_milliseconds().alias("gap"))
        .sort(["gap", "message_id"], descending=[True, False])
        .head(n)
        .collect()
    )
    return [
        Gap(
            gap_ms=int(r["gap"]),
            before_id=int(r["prev_id"]),
            after_id=int(r["message_id"]),
            start=r["prev_ts"],
            end=r["ts"],
        )
        for r in df.iter_rows(named=True)
    ]
