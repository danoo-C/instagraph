from typing import Any

import polars as pl

from instagraph.analysis import counts as C
from instagraph.api.filters import FilterParams, filtered_messages, is_counted_message
from instagraph.testing import build_dataset

PEOPLE = [{"display_name": "Me", "is_owner": True}, {"display_name": "Ann"}]
THREADS = [{"title": "Ann", "participants": [0, 1]}, {"title": "G", "participants": [0, 1]}]

MESSAGES: list[dict[str, Any]] = [
    {"thread_id": 0, "sender_id": 0, "ts": "2024-01-01T10:00:00Z", "text": "ahoj ako sa máš"},
    {"thread_id": 0, "sender_id": 1, "ts": "2024-01-01T10:01:00Z", "text": "dobre 😂😂"},
    {"thread_id": 0, "sender_id": 1, "ts": "2024-01-01T10:02:00Z", "n_photos": 3},
    {"thread_id": 0, "sender_id": 0, "ts": "2024-01-01T10:03:00Z", "type": "like"},
    {
        "thread_id": 0,
        "sender_id": 0,
        "ts": "2024-01-01T10:04:00Z",
        "text": "pozri https://a.com a https://b.com",
    },
    {
        "thread_id": 1,
        "sender_id": 1,
        "ts": "2024-01-02T10:00:00Z",
        "share_link": "https://instagram.com/reel/1",
        "share_kind": "reel",
    },
    {
        "thread_id": 1,
        "sender_id": 0,
        "ts": "2024-01-02T11:00:00Z",
        "call_duration_s": 60,
        "call_kind": "video",
    },
    {"thread_id": 1, "sender_id": 0, "ts": "2024-01-02T12:00:00Z", "is_unsent": True},
    {"thread_id": 1, "sender_id": 1, "ts": "2024-01-02T13:00:00Z", "n_audios": 1},
    {"thread_id": 1, "sender_id": 1, "ts": "2024-01-02T14:00:00Z", "text": "👍🏽 👍🏻 👍"},
]


def counted() -> pl.LazyFrame:
    ds = build_dataset(PEOPLE, THREADS, MESSAGES)
    return filtered_messages(ds, FilterParams()).filter(is_counted_message())


def test_totals_hand_calculated() -> None:
    t = C.totals(counted())
    assert t["messages"] == 9  # 10 rows minus the like
    assert t["text_messages"] == 4  # 3 text/link + emoji-only text
    # words: "ahoj ako sa máš" 4 + "dobre" 1 + "pozri a" 2 (URLs removed) + emoji-only 0
    assert t["words"] == 7
    assert t["emojis"] == 5
    assert t["photos"] == 3 and t["voice_notes"] == 1
    assert t["reels"] == 1 and t["shares"] == 1 and t["posts"] == 0
    assert t["links"] == 2
    assert t["calls"] == 1 and t["call_duration_s"] == 60
    assert t["unsent"] == 1


def test_counts_by_sender_sum_to_total() -> None:
    lf = counted()
    by = C.counts_by(lf, "sender_id").sort("sender_id")
    assert by["messages"].to_list() == [4, 5]
    assert by["words"].to_list() == [6, 1]
    assert int(by["messages"].sum()) == C.totals(lf)["messages"]
    by_thread = C.counts_by(lf, "thread_id").sort("thread_id")
    assert by_thread["messages"].to_list() == [4, 5]


def test_type_counts() -> None:
    t = C.type_counts(counted())
    assert t["text"] == 3 and t["link"] == 1 and t["photo"] == 1 and t["share"] == 1
    assert t["call"] == 1 and t["unsent"] == 1 and t["audio"] == 1
    assert "like" not in t and sum(t.values()) == 9


def test_emoji_counts_merge_skin_tones() -> None:
    merged = C.emoji_counts(counted())
    assert merged.row(0) == ("👍", 3)
    assert merged.row(1) == ("😂", 2)
    raw = C.emoji_counts(counted(), merge_skin_tones=False)
    assert raw.height == 4
    by = C.emoji_counts(counted(), by="sender_id")
    assert by.filter(pl.col("sender_id") == 1)["n"].sum() == 5


def test_share_and_safe_div() -> None:
    assert C.share(1, 4) == 0.25 and C.share(1, 0) == 0.0
    assert C.safe_div(3, 2) == 1.5 and C.safe_div(3, 0) is None


def test_log_bins_and_size_distribution() -> None:
    assert C.log_bin_edges(7) == [1, 2, 5, 10]
    assert C.log_bin_edges(10) == [1, 2, 5, 10, 20]
    bins = C.size_distribution({1: 1, 2: 3, 3: 4, 4: 60, 5: 0})
    assert [(b.lo, b.hi, b.count) for b in bins] == [
        (1, 1, 1),
        (2, 4, 2),
        (5, 9, 0),
        (10, 19, 0),
        (20, 49, 0),
        (50, 99, 1),
    ]
    assert bins[1].thread_ids == [2, 3]
    assert C.size_distribution({}) == []
