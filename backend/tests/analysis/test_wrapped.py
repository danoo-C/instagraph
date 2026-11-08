"""``analysis.wrapped`` helpers and slide data."""

from datetime import date
from typing import Any

import polars as pl

from instagraph.analysis.wrapped import (
    DEFAULT_STOPWORDS,
    compute_wrapped,
    load_stopwords,
    longest_streak,
    top_words,
    years_with_data,
)
from instagraph.api.filters import FilterParams, filtered_messages, filtered_reactions
from instagraph.testing import build_dataset

PEOPLE = [{"display_name": "Me", "is_owner": True}, {"display_name": "Ana"}, {"display_name": "Bo"}]
THREADS = [
    {"title": "Ana", "participants": [0, 1]},
    {"title": "Group", "participants": [0, 1, 2]},
]
MESSAGES: list[dict[str, Any]] = [
    {"thread_id": 0, "sender_id": 0, "ts": "2023-12-31T12:00:00Z", "text": "old year pizza"},
    {"thread_id": 0, "sender_id": 1, "ts": "2024-01-01T02:00:00Z", "text": "pizza tonight 🍕"},
    {"thread_id": 0, "sender_id": 0, "ts": "2024-01-01T02:01:00Z", "text": "yes pizza 🍕"},
    {"thread_id": 0, "sender_id": 1, "ts": "2024-01-02T10:00:00Z", "text": "the pizza was great"},
    {"thread_id": 0, "sender_id": 0, "ts": "2024-01-03T10:00:00Z", "call_duration_s": 600},
    {"thread_id": 1, "sender_id": 2, "ts": "2024-01-03T11:00:00Z", "text": "hello all 😂"},
    {"thread_id": 1, "sender_id": 0, "ts": "2024-01-05T11:00:00Z", "text": "hello"},
    {"thread_id": 0, "sender_id": 0, "ts": "2024-02-01T11:00:00Z", "call_duration_s": 60},
]
REACTIONS = [
    {"message_id": 3, "actor_id": 0, "emoji": "❤"},
    {"message_id": 3, "actor_id": 2, "emoji": "❤"},
    {"message_id": 2, "actor_id": 1, "emoji": "😂"},
]


def test_longest_streak() -> None:
    d = date
    assert longest_streak([]) is None
    days = [d(2024, 1, 1), d(2024, 1, 2), d(2024, 1, 5), d(2024, 1, 6), d(2024, 1, 7)]
    assert longest_streak(days) == (3, d(2024, 1, 5), d(2024, 1, 7))
    assert longest_streak([d(2024, 1, 1)]) == (1, d(2024, 1, 1), d(2024, 1, 1))


def test_top_words_and_stopwords() -> None:
    df = pl.DataFrame(
        {
            "type": ["text", "text", "text"],
            "text": ["The pizza https://x.com/pizza", "pizza and beer", "beer @pizza 42"],
            "char_count": [5, 5, 5],
        },
        schema_overrides={"type": pl.Enum(["text", "like", "system"])},
    )
    assert top_words(df, DEFAULT_STOPWORDS) == [("beer", 2), ("pizza", 2)]
    sw = load_stopwords({"en": ["beer"]}, {"en": ["the"]}, ["x"])
    assert "beer" in sw and "the" not in sw and "x" in sw


def _wrapped(year: int, chat: int | None = None) -> Any:
    ds = build_dataset(PEOPLE, THREADS, MESSAGES, REACTIONS)
    f = FilterParams(start=date(year, 1, 1), end=date(year, 12, 31))
    msgs = filtered_messages(ds, f)
    if chat is not None:
        msgs = msgs.filter(pl.col("thread_id") == chat)
    return compute_wrapped(
        msgs,
        filtered_reactions(ds, f, messages=msgs),
        year=year,
        owner_id=0,
        chat_scope=chat is not None,
        one_to_one={0: 1},
    )


def test_compute_wrapped_all_chats() -> None:
    w = _wrapped(2024)
    assert w.total_messages == 7
    assert w.sent_by_me == 4 and w.received == 3
    assert w.months[0] == (date(2024, 1, 1), 6)
    assert w.months[1] == (date(2024, 2, 1), 1)
    # Ana: 2 own + 3 of mine in our 1:1 ; Bo: 1
    assert w.top_people == [(1, 5), (2, 1)]
    assert w.top_emojis[0] == ("🍕", 2)
    assert w.busiest_day == (date(2024, 1, 1), 2)
    assert w.longest_streak == (3, date(2024, 1, 1), date(2024, 1, 3))
    assert w.night_owl_share == 2 / 7
    assert w.my_night_owl_share == 1 / 4
    assert w.top_words[0] == ("pizza", 3)
    assert w.most_reacted is not None and w.most_reacted[:2] == (3, 2)
    assert w.longest_call == (4, 600_000)
    assert w.fastest_replier is None  # nobody has ≥ 3 replies


def test_compute_wrapped_chat_scope_and_empty_year() -> None:
    w = _wrapped(2024, chat=1)
    assert w.total_messages == 2
    assert w.top_people == [(0, 1), (2, 1)]
    empty = _wrapped(2020)
    assert empty.total_messages == 0
    assert len(empty.months) == 12 and empty.busiest_day is None


def test_years_with_data() -> None:
    ds = build_dataset(PEOPLE, THREADS, MESSAGES, REACTIONS)
    assert years_with_data(filtered_messages(ds, FilterParams())) == [(2023, 1), (2024, 7)]
