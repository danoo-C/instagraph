from datetime import date

import polars as pl
import pytest

from instagraph.api.filters import (
    FilterParams,
    bucket_range,
    filtered_messages,
    filtered_reactions,
    is_counted_message,
    is_text_message,
)
from instagraph.errors import ValidationApiError
from instagraph.store.dataset import Dataset


def ids(lf: pl.LazyFrame) -> list[int]:
    return lf.select("message_id").collect()["message_id"].to_list()


def test_default_filters(small_dataset: Dataset) -> None:
    got = ids(filtered_messages(small_dataset, FilterParams()))
    assert 13 not in got  # request excluded
    assert 8 not in got  # system excluded
    assert 3 in got  # like kept


def test_thread_filters(small_dataset: Dataset) -> None:
    f = FilterParams(include_groups=False, include_requests=True, exclude_system=False)
    got = ids(filtered_messages(small_dataset, f))
    assert 13 in got and 7 not in got


def test_counted_and_text(small_dataset: Dataset) -> None:
    lf = filtered_messages(small_dataset, FilterParams())
    counted = ids(lf.filter(is_counted_message()))
    assert 3 not in counted and 12 in counted
    text = ids(lf.filter(is_text_message()))
    assert 5 not in text and 12 not in text and 17 not in text and 0 in text


def test_local_time_dst(small_dataset: Dataset) -> None:
    # 2024-03-31 is the EU DST switch: 08:00Z = 10:00 CEST, 2024-03-30 21:00Z = 22:00 CET
    lf = filtered_messages(small_dataset, FilterParams(tz="Europe/Bratislava"))
    df = lf.filter(pl.col("message_id").is_in([0, 5])).collect().sort("message_id")
    assert df["hour"].to_list() == [22, 10]
    assert df["weekday"].to_list() == [6, 7]
    assert df["local_date"].to_list() == [date(2024, 3, 30), date(2024, 3, 31)]


def test_date_range_in_tz(small_dataset: Dataset) -> None:
    # message 0 is 2024-03-30 21:00Z → 2024-03-31 06:00 in Tokyo
    f = FilterParams(start=date(2024, 3, 31), end=date(2024, 3, 31), tz="Asia/Tokyo")
    assert ids(filtered_messages(small_dataset, f)) == [0, 1, 2, 3, 4, 5, 6]
    f_utc = FilterParams(start=date(2024, 3, 31), end=date(2024, 3, 31))
    assert ids(filtered_messages(small_dataset, f_utc)) == [5, 6]


def test_people_filter_and_reactions(small_dataset: Dataset) -> None:
    f = FilterParams(people=(3,))
    assert ids(filtered_messages(small_dataset, f)) == [9]
    r = filtered_reactions(small_dataset, f).collect()
    assert r["message_id"].to_list() == [7]  # Eva reacted to Peter's message
    assert r["target_id"].to_list() == [2]
    all_r = filtered_reactions(small_dataset, FilterParams()).collect()
    assert all_r.height == 5 and {"local_date", "hour", "thread_id"} <= set(all_r.columns)


def test_validate_tz() -> None:
    from instagraph.api.filters import validate_tz

    with pytest.raises(ValidationApiError):
        validate_tz("Mars/Olympus")


def test_bucket_range() -> None:
    assert bucket_range(date(2024, 1, 17), date(2024, 3, 2), "month") == [
        date(2024, 1, 1),
        date(2024, 2, 1),
        date(2024, 3, 1),
    ]
    assert bucket_range(date(2024, 1, 3), date(2024, 1, 10), "week") == [
        date(2024, 1, 1),
        date(2024, 1, 8),
    ]
