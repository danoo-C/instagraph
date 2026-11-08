from datetime import UTC, date, datetime

import polars as pl

from instagraph.analysis import streaks as S
from instagraph.api.filters import FilterParams, filtered_messages, is_counted_message
from instagraph.testing import build_dataset

D = date


def test_longest_streak_picks_earliest_on_tie() -> None:
    dates = [D(2024, 1, 1), D(2024, 1, 2), D(2024, 1, 5), D(2024, 1, 6), D(2024, 1, 7)]
    assert S.longest_streak(dates) == S.Streak(3, D(2024, 1, 5), D(2024, 1, 7))
    assert S.longest_streak([D(2024, 1, 1), D(2024, 1, 3)]) == S.Streak(
        1, D(2024, 1, 1), D(2024, 1, 1)
    )
    assert S.longest_streak([]) == S.Streak()
    # duplicates and unsorted input; month boundary
    assert S.longest_streak([D(2024, 2, 1), D(2024, 1, 31), D(2024, 1, 31)]).length == 2


def test_current_streak() -> None:
    dates = [D(2024, 1, 1), D(2024, 1, 3), D(2024, 1, 4)]
    assert S.current_streak(dates, D(2024, 1, 4)).length == 2
    assert S.current_streak(dates, D(2024, 1, 5)).length == 2
    assert S.current_streak(dates, D(2024, 1, 6)) == S.Streak()


def _frame() -> pl.LazyFrame:
    ds = build_dataset(
        [{"display_name": "Me", "is_owner": True}, {"display_name": "Ann"}],
        [{"participants": [0, 1]}],
        [
            {"thread_id": 0, "sender_id": 0, "ts": "2024-01-01T10:00:00Z", "text": "a"},
            {"thread_id": 0, "sender_id": 1, "ts": "2024-01-01T11:00:00Z", "text": "b"},
            {"thread_id": 0, "sender_id": 0, "ts": "2024-01-02T10:00:00Z", "text": "c"},
            {"thread_id": 0, "sender_id": 0, "ts": "2024-01-03T10:00:00Z", "text": "d"},
            {"thread_id": 0, "sender_id": 1, "ts": "2024-01-03T12:00:00Z", "text": "e"},
            {"thread_id": 0, "sender_id": 1, "ts": "2024-01-10T12:00:00Z", "text": "f"},
        ],
    )
    return filtered_messages(ds, FilterParams()).filter(is_counted_message())


def test_mutual_dates() -> None:
    lf = _frame()
    assert S.mutual_dates(lf, [0, 1]) == [D(2024, 1, 1), D(2024, 1, 3)]
    assert S.mutual_dates(lf, []) == []


def test_longest_gaps() -> None:
    gaps = S.longest_gaps(_frame(), 2)
    assert gaps[0].gap_ms == 7 * 86_400_000  # Jan 3 12:00 → Jan 10 12:00
    assert (gaps[0].before_id, gaps[0].after_id) == (4, 5)
    assert gaps[0].start == datetime(2024, 1, 3, 12, tzinfo=UTC)
    assert gaps[1].gap_ms == 86_400_000 and gaps[1].after_id == 3  # Jan 2 10:00 → Jan 3
