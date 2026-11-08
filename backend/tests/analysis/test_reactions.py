"""Reactions (§6.7) on a hand-calculated fixture.

Messages (thread 0, A = 0, B = 1): 0 A "hi", 1 B "yes", 2 A "ok", 3 B <like>, 4 A "great".
Reactions: A ❤ on 1, B 😂 on 0, B 👍🏽 on 2, B 👍 on 4, A 😂 on 4 (self-reaction).
"""

from datetime import date
from typing import Any

import polars as pl
import pytest

from instagraph.analysis import reactions as rx
from instagraph.api.filters import FilterParams, filtered_messages, filtered_reactions
from instagraph.store.dataset import Dataset
from instagraph.testing import build_dataset

PEOPLE = [{"display_name": "A", "is_owner": True}, {"display_name": "B"}]
THREADS = [{"title": "B", "participants": [0, 1]}]
MESSAGES: list[dict[str, Any]] = [
    {"thread_id": 0, "sender_id": 0, "ts": "2024-05-01T10:00:00Z", "text": "hi"},
    {"thread_id": 0, "sender_id": 1, "ts": "2024-05-01T10:01:00Z", "text": "yes"},
    {"thread_id": 0, "sender_id": 0, "ts": "2024-05-01T10:02:00Z", "text": "ok"},
    {"thread_id": 0, "sender_id": 1, "ts": "2024-05-01T10:03:00Z", "type": "like"},
    {"thread_id": 0, "sender_id": 0, "ts": "2024-06-01T10:04:00Z", "text": "great"},
]
REACTIONS: list[dict[str, Any]] = [
    {"message_id": 1, "actor_id": 0, "emoji": "❤"},
    {"message_id": 0, "actor_id": 1, "emoji": "😂"},
    {"message_id": 2, "actor_id": 1, "emoji": "👍🏽"},
    {"message_id": 4, "actor_id": 1, "emoji": "👍"},
    {"message_id": 4, "actor_id": 0, "emoji": "😂"},
]


def _ds() -> Dataset:
    return build_dataset(PEOPLE, THREADS, MESSAGES, REACTIONS)


def _reactions(merge: bool = True) -> pl.LazyFrame:
    return rx.normalize_reactions(filtered_reactions(_ds(), FilterParams()), merge_skin_tones=merge)


def test_given_received() -> None:
    assert rx.reactions_given_received(_reactions()).rows() == [(0, 2, 4, 1), (1, 3, 1, 0)]


def test_top_emojis_honor_merge() -> None:
    merged = rx.top_reaction_emojis(_reactions(), "actor_id")
    assert merged.filter(pl.col("person_id") == 1).rows() == [(1, "👍", 2), (1, "😂", 1)]
    split = rx.top_reaction_emojis(_reactions(merge=False), "actor_id")
    assert split.filter(pl.col("person_id") == 1)["count"].to_list() == [1, 1, 1]
    received = rx.top_reaction_emojis(_reactions(), "target_id", limit=1)
    assert received.rows() == [(0, "👍", 2), (1, "❤", 1)]
    overall = rx.top_reaction_emojis(_reactions(), None)
    assert overall.select("emoji", "count").rows() == [("👍", 2), ("😂", 2), ("❤", 1)]


def test_matrix_reciprocity_most_reacted() -> None:
    assert rx.reaction_matrix(_reactions()).rows() == [(0, 0, 1), (0, 1, 1), (1, 0, 3)]
    rec = rx.reciprocity(_reactions())
    assert rec.rows() == [(0, 1, 1, 3, pytest.approx(1 / 3))]
    assert rx.most_reacted(_reactions(), 2).rows() == [(4, 2), (0, 1)]


def test_hearts_and_likes() -> None:
    msgs = filtered_messages(_ds(), FilterParams())
    lk = rx.likes(msgs)
    assert lk.select("liker_id", "liked_id").rows() == [(1, 0)]
    h = rx.hearts(_reactions(), msgs)
    assert h.rows() == [(0, 1, 0, 1, 0, 1, 1), (1, 0, 1, 1, 1, 0, 1)]


def test_filters_apply() -> None:
    # date filter (message date) drops the June message and its 2 reactions
    may = filtered_reactions(_ds(), FilterParams(end=date(2024, 5, 31)))
    assert rx.reactions_given_received(may).rows() == [(0, 1, 2, 0), (1, 2, 1, 0)]
