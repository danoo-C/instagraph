"""Time bucketing and time-of-day/week patterns (spec §6.2).

Pure functions over (already filtered) message LazyFrames produced by
:func:`instagraph.api.filters.filtered_messages`. They rely on the ``local_date``,
``local_ts``, ``hour`` and ``weekday`` columns, so every bucket is in the display time
zone. Callers decide which rows count (usually ``.filter(is_counted_message())``).

Grouped results are returned as ``dict[key, list[float]]`` where ``key`` is the value of
the ``by`` column (e.g. a ``sender_id``) or :data:`TOTAL` when ``by`` is ``None``.
"""

from collections.abc import Hashable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

import polars as pl

from instagraph.api.filters import bucket_range, bucket_start
from instagraph.schemas.common import Bucket

#: Never send more points than this per series (§9); buckets get coarser instead.
MAX_POINTS = 20_000
BUCKETS: tuple[Bucket, ...] = ("day", "week", "month", "year")
#: Key used for the single series when ``by`` is ``None``.
TOTAL = "total"
#: Night owl = 00:00–04:59, early bird = 05:00–07:59 (§7.2 When tab).
NIGHT_HOURS = range(0, 5)
EARLY_HOURS = range(5, 8)

_APPROX_DAYS: dict[Bucket, int] = {"day": 1, "week": 7, "month": 30, "year": 365}


@dataclass
class Bucketed:
    """Zero-filled values per bucket: ``x`` = bucket starts, ``series[key]`` aligned."""

    bucket: Bucket
    x: list[date] = field(default_factory=list)
    series: dict[Hashable, list[float]] = field(default_factory=dict)

    def labels(self) -> list[str]:
        return [d.isoformat() for d in self.x]


def choose_bucket(
    start: date | None, end: date | None, requested: Bucket, max_points: int = MAX_POINTS
) -> Bucket:
    """``requested`` or the first coarser bucket whose point count fits ``max_points``."""
    if start is None or end is None:
        return requested
    days = (end - start).days + 1
    for bucket in BUCKETS[BUCKETS.index(requested) :]:
        if days / _APPROX_DAYS[bucket] + 1 <= max_points:
            return bucket
    return "year"


def date_span(lf: pl.LazyFrame) -> tuple[date | None, date | None]:
    """First and last ``local_date`` in ``lf`` (``None`` when empty)."""
    row = lf.select(
        pl.col("local_date").min().alias("a"), pl.col("local_date").max().alias("b")
    ).collect()
    if row.height == 0:
        return None, None
    a, b = row.row(0)
    return a, b


def bucket_values(
    lf: pl.LazyFrame,
    bucket: Bucket,
    *,
    by: str | None = None,
    value: pl.Expr | None = None,
    start: date | None = None,
    end: date | None = None,
) -> Bucketed:
    """Sum of ``value`` (default: row count) per bucket (and per ``by`` key), zero-filled
    over ``start..end`` (default: the data's span)."""
    agg = (value.sum() if value is not None else pl.len()).cast(pl.Float64).alias("v")
    keys = ["_b"] if by is None else ["_b", by]
    df = (
        lf.with_columns(bucket_start(pl.col("local_date"), bucket).alias("_b"))
        .group_by(keys)
        .agg(agg)
        .collect()
    )
    if start is None or end is None:
        if df.height == 0:
            return Bucketed(bucket=bucket)
        start = start or df["_b"].min()  # type: ignore[assignment]
        end = end or df["_b"].max()  # type: ignore[assignment]
    assert start is not None and end is not None
    x = bucket_range(start, end, bucket) if start <= end else []
    index = {d: i for i, d in enumerate(x)}
    series: dict[Hashable, list[float]] = {}
    if by is None:
        series[TOTAL] = [0.0] * len(x)
    for row in df.iter_rows(named=True):
        i = index.get(row["_b"])
        if i is None:
            continue
        key = TOTAL if by is None else row[by]
        series.setdefault(key, [0.0] * len(x))[i] += float(row["v"] or 0.0)
    return Bucketed(bucket=bucket, x=x, series=series)


def _keyed_counts(
    lf: pl.LazyFrame, col: str, size: int, offset: int, by: str | None
) -> dict[Hashable, list[float]]:
    keys = [col] if by is None else [col, by]
    df = lf.group_by(keys).agg(pl.len().alias("n")).collect()
    out: dict[Hashable, list[float]] = {}
    if by is None:
        out[TOTAL] = [0.0] * size
    for row in df.iter_rows(named=True):
        key = TOTAL if by is None else row[by]
        out.setdefault(key, [0.0] * size)[int(row[col]) - offset] += float(row["n"])
    return out


def hour_counts(lf: pl.LazyFrame, by: str | None = None) -> dict[Hashable, list[float]]:
    """Messages per local hour 0..23."""
    return _keyed_counts(lf, "hour", 24, 0, by)


def weekday_counts(lf: pl.LazyFrame, by: str | None = None) -> dict[Hashable, list[float]]:
    """Messages per ISO weekday (index 0 = Monday … 6 = Sunday)."""
    return _keyed_counts(lf, "weekday", 7, 1, by)


def month_of_year_counts(lf: pl.LazyFrame, by: str | None = None) -> dict[Hashable, list[float]]:
    """Messages per calendar month (index 0 = January)."""
    return _keyed_counts(
        lf.with_columns(pl.col("local_date").dt.month().alias("_m")), "_m", 12, 1, by
    )


def hour_weekday_matrix(lf: pl.LazyFrame) -> list[list[float]]:
    """``values[weekday-1][hour]`` message counts (7 × 24)."""
    df = lf.group_by("weekday", "hour").agg(pl.len().alias("n")).collect()
    out = [[0.0] * 24 for _ in range(7)]
    for wd, hr, n in df.iter_rows():
        out[int(wd) - 1][int(hr)] += float(n)
    return out


def hour_weekday_matrices(lf: pl.LazyFrame, by: str) -> dict[Hashable, list[list[float]]]:
    """:func:`hour_weekday_matrix` per ``by`` key."""
    df = lf.group_by(by, "weekday", "hour").agg(pl.len().alias("n")).collect()
    out: dict[Hashable, list[list[float]]] = {}
    for key, wd, hr, n in df.iter_rows():
        out.setdefault(key, [[0.0] * 24 for _ in range(7)])[int(wd) - 1][int(hr)] += float(n)
    return out


def hours_by_year(lf: pl.LazyFrame) -> dict[int, list[float]]:
    """Messages per hour 0..23 for each local calendar year."""
    df = (
        lf.group_by(pl.col("local_date").dt.year().alias("y"), "hour")
        .agg(pl.len().alias("n"))
        .collect()
    )
    out: dict[int, list[float]] = {}
    for y, hr, n in df.iter_rows():
        out.setdefault(int(y), [0.0] * 24)[int(hr)] += float(n)
    return dict(sorted(out.items()))


def daily_counts(lf: pl.LazyFrame) -> pl.DataFrame:
    """``local_date, n`` for every active day, sorted by date."""
    return lf.group_by("local_date").agg(pl.len().alias("n")).sort("local_date").collect()


def active_dates(lf: pl.LazyFrame) -> list[date]:
    """Sorted distinct local dates with ≥ 1 row."""
    dates: list[date] = (
        lf.select(pl.col("local_date").unique()).collect()["local_date"].sort().to_list()
    )
    return dates


def per_day_averages(
    n_messages: int, start: date | None, end: date | None, n_active_days: int
) -> tuple[float, float]:
    """Messages per day over the full span (first → last day) and per active day."""
    if start is None or end is None or n_messages == 0:
        return 0.0, 0.0
    span_days = (end - start).days + 1
    return n_messages / span_days, n_messages / max(n_active_days, 1)


def busiest_days(lf: pl.LazyFrame, n: int = 10) -> list[tuple[date, int]]:
    """Top ``n`` days by message count (ties → earlier day first)."""
    df = (
        lf.group_by("local_date")
        .agg(pl.len().alias("n"))
        .sort(["n", "local_date"], descending=[True, False])
        .head(n)
        .collect()
    )
    return [(d, int(c)) for d, c in df.iter_rows()]


def busiest_hour(lf: pl.LazyFrame) -> tuple[date, int, int] | None:
    """The single clock hour (local date + hour) with the most messages."""
    df = (
        lf.group_by("local_date", "hour")
        .agg(pl.len().alias("n"))
        .sort(["n", "local_date", "hour"], descending=[True, False, False])
        .head(1)
        .collect()
    )
    if df.height == 0:
        return None
    d, h, c = df.row(0)
    return d, int(h), int(c)


def rolling_mean(values: Sequence[float], window: int) -> list[float]:
    """Trailing mean over ``window`` points (shorter at the start)."""
    out: list[float] = []
    acc = 0.0
    for i, v in enumerate(values):
        acc += v
        if i >= window:
            acc -= values[i - window]
        out.append(acc / min(i + 1, window))
    return out


def cumulative(values: Sequence[float]) -> list[float]:
    out: list[float] = []
    acc = 0.0
    for v in values:
        acc += v
        out.append(acc)
    return out


def month_start(d: date) -> date:
    return d.replace(day=1)


def last_day_of_bucket(start: date, bucket: Bucket) -> date:
    """Inclusive last day of the bucket starting at ``start`` (for drilldown ranges)."""
    if bucket == "day":
        return start
    if bucket == "week":
        return start + timedelta(days=6)
    if bucket == "month":
        nxt = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
        return nxt - timedelta(days=1)
    return date(start.year, 12, 31)


def bucket_start_expr(bucket: Bucket) -> pl.Expr:
    """Bucket start of ``local_date``."""
    return bucket_start(pl.col("local_date"), bucket)


def rank_per_bucket(
    lf: pl.LazyFrame, bucket: Bucket, *, by: str, x: Sequence[date]
) -> dict[Hashable, list[float | None]]:
    """Rank (1 = most rows, ties share the best rank) of each ``by`` key within every
    bucket of ``x``; ``None`` where the key has no rows in that bucket."""
    df = (
        lf.with_columns(bucket_start_expr(bucket).alias("_b"))
        .group_by("_b", by)
        .agg(pl.len().alias("n"))
        .with_columns(pl.col("n").rank("min", descending=True).over("_b").alias("r"))
        .collect()
    )
    index = {d: i for i, d in enumerate(x)}
    out: dict[Hashable, list[float | None]] = {}
    for row in df.iter_rows(named=True):
        i = index.get(row["_b"])
        if i is None:
            continue
        out.setdefault(row[by], [None] * len(x))[i] = float(row["r"])
    return out
