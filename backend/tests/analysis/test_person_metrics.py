"""Hand-calculated checks for ``analysis.person_metrics``."""

from typing import Any

import polars as pl
import pytest

from instagraph.analysis.person_metrics import (
    hour_shares,
    mattr,
    metrics_by_person,
    monthly_profile,
    person_metrics,
    scope_summary,
    weekday_shares,
)
from instagraph.api.filters import FilterParams, filtered_messages, filtered_reactions
from instagraph.store.dataset import Dataset
from instagraph.testing import build_dataset

PEOPLE = [{"display_name": "Me", "is_owner": True}, {"display_name": "Ana"}]
THREADS = [{"title": "Ana", "participants": [0, 1]}]
MESSAGES: list[dict[str, Any]] = [
    {"thread_id": 0, "sender_id": 1, "ts": "2024-01-01T01:00:00Z", "text": "hello there friend"},
    {"thread_id": 0, "sender_id": 0, "ts": "2024-01-01T01:01:00Z", "text": "hi 😀😀"},
    {"thread_id": 0, "sender_id": 0, "ts": "2024-01-01T01:02:00Z", "text": "how are you?"},
    {"thread_id": 0, "sender_id": 1, "ts": "2024-01-01T01:03:00Z", "text": "haha good"},
    {"thread_id": 0, "sender_id": 1, "ts": "2024-01-01T06:00:00Z", "text": "morning"},
    {"thread_id": 0, "sender_id": 0, "ts": "2024-01-01T06:10:00Z", "n_audios": 1},
    {"thread_id": 0, "sender_id": 0, "ts": "2024-01-01T06:11:00Z", "is_unsent": True},
    {"thread_id": 0, "sender_id": 1, "ts": "2024-01-01T06:12:00Z", "type": "like"},
]
REACTIONS = [
    {"message_id": 1, "actor_id": 1, "emoji": "❤"},
    {"message_id": 2, "actor_id": 1, "emoji": "😂"},
    {"message_id": 0, "actor_id": 0, "emoji": "👍"},
    {"message_id": 1, "actor_id": 0, "emoji": "❤"},  # self-reaction
]


@pytest.fixture
def ds() -> Dataset:
    return build_dataset(PEOPLE, THREADS, MESSAGES, REACTIONS)


def _metrics(ds: Dataset) -> dict[int, dict[str, Any]]:
    f = FilterParams()
    df = person_metrics(filtered_messages(ds, f), filtered_reactions(ds, f))
    return metrics_by_person(df)


def test_ana(ds: Dataset) -> None:
    a = _metrics(ds)[1]
    assert a["messages"] == 3
    assert a["text_messages"] == 3
    assert a["words"] == 6
    assert a["avg_words"] == 2
    assert a["emojis"] == 0
    assert a["night_share"] == pytest.approx(2 / 3)
    assert a["early_share"] == pytest.approx(1 / 3)
    assert a["sessions"] == 2
    assert a["sessions_started"] == 2
    assert a["initiation_rate"] == 1
    assert a["turns"] == 2
    assert a["msgs_per_turn"] == 1.5
    assert a["replies"] == 1
    assert a["reply_median_ms"] == 60_000
    assert a["unanswered_turns"] == 0
    assert a["reply_rate"] == 1
    assert a["laugh_messages"] == 1
    assert a["laughs_caused"] == 0
    assert a["likes"] == 1
    assert a["hearts_given"] == 2  # ❤ reaction + like
    assert a["reactions_given"] == 2
    assert a["reactions_received"] == 1
    assert a["reactions_per_message"] == pytest.approx(1 / 3)
    assert a["question_rate"] == 0
    assert a["tokens"] == 6
    assert a["mattr"] == 1


def test_me(ds: Dataset) -> None:
    m = _metrics(ds)[0]
    assert m["messages"] == 4
    assert m["text_messages"] == 2
    assert m["words"] == 4
    assert m["avg_words"] == 2
    assert m["emojis"] == 2
    assert m["emojis_per_message"] == 0.5
    assert m["night_share"] == 0.5
    assert m["early_share"] == 0.5
    assert m["sessions_started"] == 0
    assert m["initiation_rate"] == 0
    assert m["msgs_per_turn"] == 2
    assert m["replies"] == 2
    assert m["reply_median_ms"] == 330_000
    assert m["reply_mean_ms"] == 330_000
    assert m["unanswered_turns"] == 1
    assert m["reply_rate"] == 0.5
    assert m["laughs_caused"] == 1  # Ana's "haha" 1 min after my last message
    assert m["voice_notes"] == 1
    assert m["unsent"] == 1
    assert m["hearts_given"] == 1  # self ❤ still counts as given
    assert m["reactions_received"] == 2  # self-reaction excluded
    assert m["reactions_per_message"] == 0.5
    assert m["question_rate"] == 0.5
    assert m["calls_started"] == 0


def test_people_restricts_rows_only(ds: Dataset) -> None:
    f = FilterParams()
    df = person_metrics(filtered_messages(ds, f), filtered_reactions(ds, f), people=[0])
    assert df["sender_id"].to_list() == [0]
    assert df["laughs_caused"][0] == 1


def test_empty() -> None:
    ds = build_dataset(PEOPLE, THREADS, [])
    f = FilterParams()
    df = person_metrics(filtered_messages(ds, f), filtered_reactions(ds, f))
    assert df.is_empty()
    assert "mattr" in df.columns


def test_mattr() -> None:
    assert mattr([]) is None
    assert mattr(["a", "b", "a"]) == pytest.approx(2 / 3)
    # windows of 2: [a,b]=2, [b,a]=2, [a,a]=1 → (1 + 1 + 0.5) / 3
    assert mattr(["a", "b", "a", "a"], window=2) == pytest.approx(2.5 / 3)


def test_scope_summary_and_shares(ds: Dataset) -> None:
    f = FilterParams()
    msgs = filtered_messages(ds, f)
    s = scope_summary(msgs, filtered_reactions(ds, f))
    assert s["messages"] == 7
    assert s["words"] == 10
    assert s["reactions_received"] == 3
    assert s["voice_notes"] == 1
    assert s["reply_median_ms"] == 60_000  # latencies 60s, 60s, 600s
    hs = hour_shares(msgs)
    assert hs[1] == pytest.approx(4 / 7) and hs[6] == pytest.approx(3 / 7)
    ws = weekday_shares(msgs)  # 2024-01-01 is a Monday
    assert ws[0] == 1
    prof = monthly_profile(msgs)
    assert prof["messages"].to_list() == [7]
    assert prof["per_day"][0] == pytest.approx(7 / 31)
    assert prof["emoji_rate"][0] == pytest.approx(2 / 7)


def test_custom_laughter_patterns(ds: Dataset) -> None:
    f = FilterParams()
    df = person_metrics(
        filtered_messages(ds, f), filtered_reactions(ds, f), laughter_patterns=["morning"]
    )
    assert metrics_by_person(df)[1]["laugh_messages"] == 1
    # A pattern Polars' regex engine rejects (look-ahead) falls back to Python ``re``.
    df = person_metrics(
        filtered_messages(ds, f), filtered_reactions(ds, f), laughter_patterns=[r"ha(?=ha)"]
    )
    assert metrics_by_person(df)[1]["laugh_messages"] == 1
    assert isinstance(df, pl.DataFrame)
