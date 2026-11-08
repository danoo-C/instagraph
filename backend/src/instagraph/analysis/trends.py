"""Trend, balance and lifecycle metrics (spec §6.9).

* Trend = slope of a least-squares line over the last :data:`TREND_WINDOW` monthly
  counts, divided by their mean. ``> TREND_THRESHOLD`` → rising, ``< -TREND_THRESHOLD``
  → fading, otherwise stable. A window of only zeros after earlier activity is fading.
* Balance index (1:1) = ``1 - |my_share - 0.5| * 2``.
* Lifecycle = first/last message, span, peak month, active months, longest break and
  the comeback after it.
"""

import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

TrendLabel = Literal["rising", "stable", "fading"]

TREND_WINDOW = 6
TREND_THRESHOLD = 0.1


def normalized_slope(values: Sequence[float]) -> float:
    """Least-squares slope of ``values`` over x = 0..n-1, divided by their mean."""
    n = len(values)
    if n < 2:
        return 0.0
    mean_y = sum(values) / n
    if mean_y == 0:
        return 0.0
    mean_x = (n - 1) / 2
    sxx = sum((i - mean_x) ** 2 for i in range(n))
    sxy = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
    return (sxy / sxx) / mean_y


def trend(
    monthly: Sequence[float], window: int = TREND_WINDOW, threshold: float = TREND_THRESHOLD
) -> tuple[TrendLabel, float]:
    """Trend label and normalized slope from monthly counts ending at the reference
    month (zero-filled, chronological)."""
    recent = list(monthly[-window:])
    if not recent:
        return "stable", 0.0
    if sum(recent) == 0:
        return ("fading" if any(monthly) else "stable"), 0.0
    slope = normalized_slope(recent)
    if slope > threshold:
        return "rising", slope
    if slope < -threshold:
        return "fading", slope
    return "stable", slope


def balance_index(my_share: float) -> float:
    """1 = perfectly balanced, 0 = one-sided."""
    return max(0.0, 1.0 - abs(my_share - 0.5) * 2)


def balance_series(mine: Sequence[float], total: Sequence[float]) -> list[float | None]:
    """Balance index per bucket (``None`` where nobody wrote)."""
    return [balance_index(m / t) if t else None for m, t in zip(mine, total, strict=True)]


def peak(x: Sequence[date], values: Sequence[float]) -> tuple[date | None, float]:
    """Bucket with the highest value (earliest on ties)."""
    best: tuple[date | None, float] = (None, 0.0)
    for d, v in zip(x, values, strict=True):
        if v > best[1]:
            best = (d, v)
    return best


@dataclass(frozen=True)
class Lifecycle:
    first: date | None
    last: date | None
    span_days: int
    peak_month: date | None
    peak_month_count: int
    active_months: int
    longest_break_days: int
    break_start: date | None
    comeback: date | None


def lifecycle(
    active_dates: Sequence[date], months: Sequence[date], monthly: Sequence[float]
) -> Lifecycle:
    """Friendship lifecycle from the sorted active days and monthly counts.

    ``longest_break_days`` = most days without any message between two active days
    (``break_start`` = last active day before it, ``comeback`` = first day after).
    """
    days = sorted(active_dates)
    if not days:
        return Lifecycle(None, None, 0, None, 0, 0, 0, None, None)
    best_gap, b_start, b_end = 0, None, None
    for a, b in itertools.pairwise(days):
        silent = (b - a).days - 1
        if silent > best_gap:
            best_gap, b_start, b_end = silent, a, b
    peak_month, peak_count = peak(months, monthly)
    return Lifecycle(
        first=days[0],
        last=days[-1],
        span_days=(days[-1] - days[0]).days + 1,
        peak_month=peak_month,
        peak_month_count=int(peak_count),
        active_months=sum(1 for v in monthly if v > 0),
        longest_break_days=best_gap,
        break_start=b_start,
        comeback=b_end,
    )
