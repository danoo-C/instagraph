from datetime import date

import polars as pl
import pytest

from instagraph.analysis import search
from instagraph.api.filters import FilterParams, filtered_messages
from instagraph.errors import ValidationApiError
from instagraph.schemas.common import MessageQuery
from instagraph.store.dataset import Dataset
from instagraph.testing import build_dataset


def ids(ds: Dataset, f: FilterParams | None = None, **q: object) -> list[int]:
    query = MessageQuery.model_validate(q)
    search.validate_query(query)
    df = search.match_frame(
        filtered_messages(ds, f or FilterParams()), query, reactions=ds.reactions
    )
    return [int(i) for i in df["message_id"].to_list()]


def test_no_query_returns_all_filtered(small_dataset: Dataset) -> None:
    # default filters: no requests (13), no system (8)
    got = ids(small_dataset)
    assert 13 not in got and 8 not in got and len(got) == 18
    assert got == sorted(got)


def test_plain_case_insensitive_and_sensitive(small_dataset: Dataset) -> None:
    assert ids(small_dataset, text="AHOJ") == [0]
    assert ids(small_dataset, text="AHOJ", case_sensitive=True) == []
    assert ids(small_dataset, text="Ahoj", case_sensitive=True) == [0]
    # substring
    assert ids(small_dataset, text="obr") == [1, 18]


def test_accent_insensitive_slovak(small_dataset: Dataset) -> None:
    assert ids(small_dataset, text="dakujem") == []
    assert ids(small_dataset, text="dakujem", accent_insensitive=True) == [14]
    assert ids(small_dataset, text="velmi", accent_insensitive=True) == [15]
    assert ids(small_dataset, text="cau vsetci", accent_insensitive=True) == [7]
    # accented needle matches unaccented-equivalent text, too
    assert ids(small_dataset, text="ÁNO", accent_insensitive=True) == [11, 18]  # Áno, ráno
    assert ids(small_dataset, text="ano", text_mode="word", accent_insensitive=True) == [11]
    assert ids(small_dataset, text="nemas", accent_insensitive=True) == [16]
    assert ids(small_dataset, text="dobre", accent_insensitive=True) == [1, 18]


def test_whole_word(small_dataset: Dataset) -> None:
    assert ids(small_dataset, text="dobr") == [1, 18]
    assert ids(small_dataset, text="dobr", text_mode="word") == []
    assert ids(small_dataset, text="dobre", text_mode="word") == [1]
    assert ids(small_dataset, text="dobre", text_mode="word", accent_insensitive=True) == [1, 18]
    # non-word edges (emoji / punctuation) still match
    assert ids(small_dataset, text="😂", text_mode="word") == [0, 6]
    assert ids(small_dataset, text="#vikend", text_mode="word") == [10]


def test_regex(small_dataset: Dataset) -> None:
    assert ids(small_dataset, text=r"^h(a)+", text_mode="regex") == [6]
    assert ids(small_dataset, text=r"^D", text_mode="regex", case_sensitive=True) == [1]
    assert ids(small_dataset, text=r"\bmá", text_mode="regex") == [0]
    # catastrophic-backtracking classic is harmless on the linear-time engine
    assert ids(small_dataset, text=r"^(h+a+)+ ", text_mode="regex") == [6]
    assert ids(small_dataset, text=r"(a|aa)+$", text_mode="regex") == []


@pytest.mark.parametrize("pattern", ["(a", "[z-a]", "a{99999}{99999}"])
def test_invalid_regex(pattern: str) -> None:
    with pytest.raises(ValidationApiError):
        search.validate_query(MessageQuery(text=pattern, text_mode="regex"))


def test_regex_too_long() -> None:
    with pytest.raises(ValidationApiError):
        search.validate_query(MessageQuery(text="a" * 501, text_mode="regex"))


def test_structured_filters(small_dataset: Dataset) -> None:
    ds = small_dataset
    assert ids(ds, thread_id=1) == [7, 9, 10, 11, 19]
    assert ids(ds, thread_ids=[1], sender_id=0) == [11]
    assert ids(ds, types=["photo", "call"]) == [5, 12]
    assert ids(ds, has_reactions=True) == [0, 1, 7, 19]
    assert 0 not in ids(ds, has_reactions=False)
    assert ids(ds, reactor_id=3) == [7]
    assert ids(ds, reaction_emoji="❤️") == [1, 7]
    assert ids(ds, emoji="😂") == [0, 6]
    assert ids(ds, emoji="👍") == [4]  # skin tone merged by default
    assert ids(ds, link_domain="example.com") == [2]
    assert ids(ds, share_kind="reel") == [19]
    assert ids(ds, call_kind="audio") == [12]
    assert ids(ds, message_ids=[1, 2, 99]) == [1, 2]
    assert ids(ds, hour=18, start=date(2024, 4, 5), end=date(2024, 4, 5)) == [14, 15, 16, 17]
    assert ids(ds, weekday=6, month=3) == [0, 1, 2, 3, 4]  # Sat 2024-03-30
    # system messages are reachable once system messages aren't excluded
    assert ids(ds, FilterParams(exclude_system=False), system_event="rename") == [8]


def test_emoticon_exact() -> None:
    ds = build_dataset(
        people=[{"display_name": "A"}],
        threads=[{"participants": [0]}],
        messages=[
            {"thread_id": 0, "sender_id": 0, "ts": "2024-01-01T00:00:00Z", "text": "ok :)"},
            {"thread_id": 0, "sender_id": 0, "ts": "2024-01-01T00:01:00Z", "text": "x:)y"},
            {"thread_id": 0, "sender_id": 0, "ts": "2024-01-01T00:02:00Z", "text": "fine"},
        ],
    )
    assert ids(ds, emoticon=":)") == [0]


def test_cursor_roundtrip_and_garbage() -> None:
    c = search.encode_cursor(1_700_000_000_000, 42)
    assert search.decode_cursor(c) == (1_700_000_000_000, 42)
    with pytest.raises(ValidationApiError):
        search.decode_cursor("!!!")


@pytest.mark.parametrize("order", ["asc", "desc"])
def test_pagination_no_gaps_or_dupes(order: str) -> None:
    all_ids = pl.Series([i * 3 for i in range(103)], dtype=pl.UInt32)
    expected = all_ids.to_list() if order == "asc" else all_ids.to_list()[::-1]
    pages = []
    page = search.page_slice(all_ids, limit=10, order=order)
    pages.append(page)
    assert not page.has_prev
    while page.has_next:
        page = search.page_slice(all_ids, limit=10, order=order, cursor_id=page.ids[-1])
        pages.append(page)
    seen = [i for p in pages for i in p.ids]
    assert seen == expected and len(pages) == 11 and pages[-1].has_prev
    # walk back from the last page
    back = [pages[-1]]
    page = pages[-1]
    while page.has_prev:
        page = search.page_slice(
            all_ids, limit=10, order=order, cursor_id=page.ids[0], direction="before"
        )
        back.append(page)
    assert [i for p in reversed(back) for i in p.ids] == expected
    assert not back[-1].has_prev and back[-1].has_next


def test_page_cursor_not_in_set() -> None:
    s = pl.Series([1, 5, 9], dtype=pl.UInt32)
    assert search.page_slice(s, limit=5, order="asc", cursor_id=4).ids == [5, 9]
    assert search.page_slice(s, limit=5, order="desc", cursor_id=4).ids == [1]
    assert search.page_slice(s, limit=5, order="desc", cursor_id=4, direction="before").ids == [
        9,
        5,
    ]


def test_timeline_and_per_person(small_dataset: Dataset) -> None:
    df = search.match_frame(
        filtered_messages(small_dataset, FilterParams()),
        MessageQuery(),
        reactions=small_dataset.reactions,
    )
    bucket, xs, values = search.match_timeline(df)
    assert bucket == "day" and xs[0] == "2024-03-30" and xs[-1] == "2024-04-06"
    assert sum(values) == df.height and len(xs) == 8
    counts = dict(search.per_person_counts(df))
    assert counts[1] == 8 and sum(counts.values()) == df.height
    assert search.auto_bucket(date(2020, 1, 1), date(2020, 12, 31)) == "week"
    assert search.auto_bucket(date(2015, 1, 1), date(2024, 1, 1)) == "month"


def test_context(small_dataset: Dataset) -> None:
    sl = search.context_slice(small_dataset.messages, 4, before=2, after=2)
    assert sl is not None and sl.thread_id == 0
    assert sl.ids == [2, 3, 4, 5, 6] and sl.target_index == 2
    assert sl.has_more_before and sl.has_more_after
    sl = search.context_slice(small_dataset.messages, 8, before=5, after=1)
    assert sl is not None and sl.ids == [7, 8, 9] and sl.target_index == 1  # target system kept
    sl = search.context_slice(small_dataset.messages, 9, before=5, after=0)
    assert sl is not None and sl.ids == [7, 9]  # other system messages hidden
    assert search.context_slice(small_dataset.messages, 999, before=1, after=1) is None


def test_random_respects_set() -> None:
    s = pl.Series([3, 7], dtype=pl.UInt32)
    assert {search.random_id(s, seed) for seed in range(20)} == {3, 7}
    assert search.random_id(pl.Series([], dtype=pl.UInt32)) is None


def test_on_this_day_uses_local_tz() -> None:
    ds = build_dataset(
        people=[{"display_name": "A"}],
        threads=[{"participants": [0]}],
        messages=[
            # 2022-06-14 23:30 UTC = 2022-06-15 01:30 in Bratislava
            {"thread_id": 0, "sender_id": 0, "ts": "2022-06-14T23:30:00Z", "text": "a"},
            {"thread_id": 0, "sender_id": 0, "ts": "2023-06-15T12:00:00Z", "text": "b"},
            {"thread_id": 0, "sender_id": 0, "ts": "2024-06-15T12:00:00Z", "text": "c"},
        ],
    )
    day = date(2024, 6, 15)
    utc = search.match_frame(
        filtered_messages(ds, FilterParams()), MessageQuery(), reactions=ds.reactions
    )
    assert search.on_this_day(utc, day) == {2023: [1]}
    local = search.match_frame(
        filtered_messages(ds, FilterParams(tz="Europe/Bratislava")),
        MessageQuery(),
        reactions=ds.reactions,
    )
    assert search.on_this_day(local, day) == {2022: [0], 2023: [1]}
