from datetime import date
from typing import Any

import polars as pl

from instagraph.analysis import time as T
from instagraph.api.filters import FilterParams, filtered_messages, is_counted_message
from instagraph.testing import build_dataset

PEOPLE = [{"display_name": "Me", "is_owner": True}, {"display_name": "Ann"}]
THREADS = [{"title": "Ann", "participants": [0, 1]}]


def frame(messages: list[dict[str, Any]], tz: str = "UTC") -> pl.LazyFrame:
    ds = build_dataset(PEOPLE, THREADS, messages)
    return filtered_messages(ds, FilterParams(tz=tz)).filter(is_counted_message())


def msg(ts: str, sender: int = 0, **kw: Any) -> dict[str, Any]:
    return {"thread_id": 0, "sender_id": sender, "ts": ts, "text": "hi", **kw}


# 2024-01-01 is a Monday.
MSGS = [
    msg("2024-01-01T10:00:00Z", 0),  # Mon 10h
    msg("2024-01-01T10:30:00Z", 1),  # Mon 10h
    msg("2024-01-03T23:30:00Z", 1),  # Wed 23h UTC; Thu 00:30 in Bratislava (CET)
    msg("2024-01-10T08:00:00Z", 0),  # Wed (next week) 8h
    msg("2024-02-15T12:00:00Z", 1),  # Thu
    {"thread_id": 0, "sender_id": 1, "ts": "2024-02-15T12:01:00Z", "type": "like"},
]


def test_bucket_values_day_week_month_zero_filled() -> None:
    lf = frame(MSGS)
    days = T.bucket_values(lf, "day")
    assert days.x[0] == date(2024, 1, 1) and days.x[-1] == date(2024, 2, 15)
    assert len(days.x) == 46
    assert days.series[T.TOTAL][0] == 2 and days.series[T.TOTAL][1] == 0
    assert sum(days.series[T.TOTAL]) == 5  # like is not counted

    weeks = T.bucket_values(lf, "week")
    assert weeks.labels()[:2] == ["2024-01-01", "2024-01-08"]
    assert weeks.series[T.TOTAL][:2] == [3.0, 1.0]

    months = T.bucket_values(lf, "month", by="sender_id")
    assert months.labels() == ["2024-01-01", "2024-02-01"]
    assert months.series[0] == [2.0, 0.0]
    assert months.series[1] == [2.0, 1.0]


def test_bucket_values_sum_and_explicit_range() -> None:
    lf = frame(MSGS)
    data = T.bucket_values(
        lf, "month", value=pl.col("word_count"), start=date(2023, 12, 5), end=date(2024, 3, 1)
    )
    assert data.labels() == ["2023-12-01", "2024-01-01", "2024-02-01", "2024-03-01"]
    assert data.series[T.TOTAL] == [0.0, 4.0, 1.0, 0.0]


def test_bucket_values_empty() -> None:
    lf = frame(MSGS).filter(pl.lit(False))
    assert T.bucket_values(lf, "day").x == []
    assert T.date_span(lf) == (None, None)


def test_time_zone_moves_message_to_next_day() -> None:
    utc = T.weekday_counts(frame(MSGS))[T.TOTAL]
    bts = T.weekday_counts(frame(MSGS, tz="Europe/Bratislava"))[T.TOTAL]
    # UTC: Mon 2, Wed 2, Thu 1; Bratislava: the 23:30Z message is on Thursday.
    assert utc == [2.0, 0.0, 2.0, 1.0, 0.0, 0.0, 0.0]
    assert bts == [2.0, 0.0, 1.0, 2.0, 0.0, 0.0, 0.0]
    hours = T.hour_counts(frame(MSGS, tz="Europe/Bratislava"))[T.TOTAL]
    assert hours[0] == 1 and hours[11] == 2 and hours[9] == 1 and hours[13] == 1


def test_hour_weekday_matrix_and_by_person() -> None:
    lf = frame(MSGS)
    m = T.hour_weekday_matrix(lf)
    assert len(m) == 7 and all(len(r) == 24 for r in m)
    assert m[0][10] == 2 and m[2][23] == 1 and m[2][8] == 1 and m[3][12] == 1
    per = T.hour_weekday_matrices(lf, "sender_id")
    assert per[0][0][10] == 1 and per[1][0][10] == 1
    by_person = T.hour_counts(lf, by="sender_id")
    assert by_person[0][10] == 1 and by_person[0][8] == 1 and sum(by_person[1]) == 3


def test_month_of_year_and_hours_by_year() -> None:
    lf = frame([*MSGS, msg("2025-01-05T10:00:00Z")])
    moy = T.month_of_year_counts(lf)[T.TOTAL]
    assert moy[0] == 5 and moy[1] == 1 and sum(moy) == 6
    hby = T.hours_by_year(lf)
    assert list(hby) == [2024, 2025]
    assert hby[2025][10] == 1 and sum(hby[2024]) == 5


def test_busiest_days_and_hour() -> None:
    lf = frame(MSGS)
    assert T.busiest_days(lf, 2) == [(date(2024, 1, 1), 2), (date(2024, 1, 3), 1)]
    assert T.busiest_hour(lf) == (date(2024, 1, 1), 10, 2)
    assert T.busiest_hour(lf.filter(pl.lit(False))) is None


def test_active_dates_and_per_day() -> None:
    lf = frame(MSGS)
    dates = T.active_dates(lf)
    assert dates == [date(2024, 1, 1), date(2024, 1, 3), date(2024, 1, 10), date(2024, 2, 15)]
    full, active = T.per_day_averages(5, date(2024, 1, 1), date(2024, 1, 10), 4)
    assert full == 0.5 and active == 1.25
    assert T.per_day_averages(0, None, None, 0) == (0.0, 0.0)


def test_choose_bucket_coarsens() -> None:
    a = date(2000, 1, 1)
    assert T.choose_bucket(a, date(2000, 12, 31), "day") == "day"
    assert T.choose_bucket(a, date(2000, 12, 31), "day", max_points=40) == "month"
    assert T.choose_bucket(a, date(2000, 12, 31), "day", max_points=60) == "week"
    assert T.choose_bucket(None, None, "week") == "week"


def test_rolling_cumulative_and_bucket_end() -> None:
    assert T.rolling_mean([3, 0, 3, 6], 2) == [3.0, 1.5, 1.5, 4.5]
    assert T.cumulative([1, 2, 0, 3]) == [1.0, 3.0, 3.0, 6.0]
    assert T.last_day_of_bucket(date(2024, 2, 1), "month") == date(2024, 2, 29)
    assert T.last_day_of_bucket(date(2024, 12, 1), "month") == date(2024, 12, 31)
    assert T.last_day_of_bucket(date(2024, 1, 1), "week") == date(2024, 1, 7)
    assert T.last_day_of_bucket(date(2024, 1, 1), "year") == date(2024, 12, 31)


def test_rank_per_bucket() -> None:
    ds = build_dataset(
        [{"display_name": "Me", "is_owner": True}, {"display_name": "A"}, {"display_name": "B"}],
        [{"participants": [0, 1]}, {"participants": [0, 2]}],
        [
            {"thread_id": 0, "sender_id": 1, "ts": "2024-01-01T10:00:00Z", "text": "a"},
            {"thread_id": 1, "sender_id": 2, "ts": "2024-01-02T10:00:00Z", "text": "b"},
            {"thread_id": 1, "sender_id": 2, "ts": "2024-01-03T10:00:00Z", "text": "b"},
            {"thread_id": 0, "sender_id": 1, "ts": "2024-02-01T10:00:00Z", "text": "a"},
        ],
    )
    lf = filtered_messages(ds, FilterParams())
    x = [date(2024, 1, 1), date(2024, 2, 1)]
    ranks = T.rank_per_bucket(lf, "month", by="thread_id", x=x)
    assert ranks[0] == [2.0, 1.0]
    assert ranks[1] == [1.0, None]
