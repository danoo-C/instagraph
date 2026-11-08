from datetime import date

import pytest

from instagraph.analysis import trends as TR


def test_normalized_slope() -> None:
    # y = 1..6: slope 1, mean 3.5
    assert TR.normalized_slope([1, 2, 3, 4, 5, 6]) == pytest.approx(1 / 3.5)
    assert TR.normalized_slope([5]) == 0.0
    assert TR.normalized_slope([0, 0, 0]) == 0.0


def test_trend_labels_use_last_six_months() -> None:
    assert TR.trend([100, 1, 2, 3, 4, 5, 6])[0] == "rising"  # old spike ignored
    label, slope = TR.trend([60, 50, 40, 30, 20, 10])
    assert label == "fading" and slope == pytest.approx(-10 / 35)
    assert TR.trend([10, 10, 11, 10, 9, 10])[0] == "stable"
    assert TR.trend([5, 0, 0, 0, 0, 0, 0]) == ("fading", 0.0)
    assert TR.trend([]) == ("stable", 0.0)
    assert TR.trend([0, 0]) == ("stable", 0.0)


def test_balance() -> None:
    assert TR.balance_index(0.5) == 1.0
    assert TR.balance_index(1.0) == 0.0
    assert TR.balance_index(0.25) == pytest.approx(0.5)
    assert TR.balance_series([1, 0, 3], [2, 0, 4]) == [1.0, None, pytest.approx(0.5)]


def test_peak_and_lifecycle() -> None:
    months = [date(2024, 1, 1), date(2024, 2, 1), date(2024, 3, 1)]
    assert TR.peak(months, [3, 5, 5]) == (date(2024, 2, 1), 5)
    days = [date(2024, 1, 5), date(2024, 1, 6), date(2024, 3, 1), date(2024, 3, 2)]
    life = TR.lifecycle(days, months, [2, 0, 2])
    assert life.first == date(2024, 1, 5) and life.last == date(2024, 3, 2)
    assert life.span_days == 58
    assert life.active_months == 2
    assert life.peak_month == date(2024, 1, 1) and life.peak_month_count == 2
    assert life.longest_break_days == 54  # Jan 7 .. Feb 29
    assert life.break_start == date(2024, 1, 6) and life.comeback == date(2024, 3, 1)
    empty = TR.lifecycle([], [], [])
    assert empty.first is None and empty.span_days == 0
