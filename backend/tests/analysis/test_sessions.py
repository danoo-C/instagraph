"""Sessions / initiations / turns (§6.4) on a hand-calculated fixture + property tests.

Fixture (thread 0, A = 0 owner, B = 1; session gap 60 min, reply window 12 h)::

    id  time            who  text            session  turn  bursts
    0   d1 10:00:00     A    hi              S1*      T1
    1   d1 10:00:30     A    you there?      S1       T1    (30 s gap: same burst)
    2   d1 10:05:00     B    yes             S1       T2    reply 270 000 ms
    3   d1 10:10:00     A    ok              S1       T3    reply 300 000 ms
    4   d1 10:11:00     B    <like>          -        -
    5   d1 10:12:00     A    great           S1       T3    (2 min: burst 2)
    6   d1 10:20:00     A    !!              S1       T3    (8 min: burst 3) → triple
    7   d1 13:00:00     B    Hi              S2*      T4    reply 9 600 000 ms
    8   d1 13:02:00     B    hi again        S2       T4    (2 min: burst 2) → double
    9   d2 09:00:00     A    hi              S3*      T5    (no reply: > 12 h)
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import polars as pl
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from instagraph.analysis import sessions as ss
from instagraph.store.dataset import Dataset
from instagraph.testing import build_dataset

PEOPLE = [{"display_name": "A", "is_owner": True}, {"display_name": "B"}]
THREADS = [{"title": "B", "participants": [0, 1]}]
MESSAGES: list[dict[str, Any]] = [
    {"thread_id": 0, "sender_id": 0, "ts": "2024-05-01T10:00:00Z", "text": "hi"},
    {"thread_id": 0, "sender_id": 0, "ts": "2024-05-01T10:00:30Z", "text": "you there?"},
    {"thread_id": 0, "sender_id": 1, "ts": "2024-05-01T10:05:00Z", "text": "yes"},
    {"thread_id": 0, "sender_id": 0, "ts": "2024-05-01T10:10:00Z", "text": "ok"},
    {"thread_id": 0, "sender_id": 1, "ts": "2024-05-01T10:11:00Z", "type": "like"},
    {"thread_id": 0, "sender_id": 0, "ts": "2024-05-01T10:12:00Z", "text": "great"},
    {"thread_id": 0, "sender_id": 0, "ts": "2024-05-01T10:20:00Z", "text": "!!"},
    {"thread_id": 0, "sender_id": 1, "ts": "2024-05-01T13:00:00Z", "text": "Hi"},
    {"thread_id": 0, "sender_id": 1, "ts": "2024-05-01T13:02:00Z", "text": "hi again"},
    {"thread_id": 0, "sender_id": 0, "ts": "2024-05-02T09:00:00Z", "text": "hi"},
]


def _ds() -> Dataset:
    return build_dataset(PEOPLE, THREADS, MESSAGES)


def test_session_table_and_summary() -> None:
    t = ss.session_table(_ds().messages)
    assert t["messages"].to_list() == [6, 2, 1]
    assert t["duration_ms"].to_list() == [1_200_000, 120_000, 0]
    assert t["initiator_id"].to_list() == [0, 1, 0]
    assert t["last_sender_id"].to_list() == [0, 1, 0]
    s = ss.session_summary(t)
    assert s.count == 3
    assert s.avg_messages == 3.0 and s.median_messages == 2.0
    assert s.avg_duration_ms == 440_000.0 and s.median_duration_ms == 120_000.0


def test_empty_summary() -> None:
    t = ss.session_table(_ds().messages.filter(pl.lit(False)))
    assert ss.session_summary(t).count == 0


def test_initiation_and_last_word() -> None:
    init = ss.initiation_rates(_ds().messages)
    assert init.rows() == [(0, 2, 2 / 3), (1, 1, 1 / 3)]
    last = ss.last_word_counts(_ds().messages)
    assert last.rows() == [(0, 2, 2 / 3), (1, 1, 1 / 3)]


def test_date_filter_keeps_derived_fields() -> None:
    # Dropping message 0 hides S1's start: S1 stays visible but has no initiator.
    msgs = _ds().messages.filter(pl.col("message_id") != 0)
    t = ss.session_table(msgs)
    assert t["initiator_id"].to_list() == [None, 1, 0]
    init = ss.initiation_rates(msgs)
    assert init.rows() == [(0, 1, 1 / 3), (1, 1, 1 / 3)]


def test_turns() -> None:
    tt = ss.turn_table(_ds().messages)
    assert tt["messages"].to_list() == [2, 1, 3, 2, 1]
    assert tt["bursts"].to_list() == [1, 1, 3, 2, 1]
    stats = ss.turn_stats(_ds().messages)
    assert stats.rows() == [(0, 3, 6, 2.0, 0, 1), (1, 2, 3, 1.5, 1, 0)]


def test_conversation_starters() -> None:
    cs = ss.conversation_starters(_ds().messages)
    assert cs.height == 1
    row = cs.row(0, named=True)
    assert row["key"] == "hi" and row["text"] == "hi" and row["count"] == 3
    assert row["message_ids"] == [0, 7, 9]


def test_bins() -> None:
    assert ss.session_length_edges(1) == [1]
    assert ss.session_length_edges(5) == [1, 2, 4, 8]
    assert ss.bin_counts([1, 2, 3, 4, 5, 9], [1, 2, 4, 8]) == [1, 1, 2, 1, 1]
    assert ss.bin_counts([], [1]) == [0, 0]


# --------------------------------------------------------------------------------------
# Property tests
# --------------------------------------------------------------------------------------

_events = st.lists(
    st.tuples(
        st.integers(0, 1),  # thread
        st.integers(0, 2),  # sender
        st.sampled_from([0, 5, 30, 61, 300, 3_000, 3_700, 20_000, 50_000, 200_000]),
        st.booleans(),  # is like
    ),
    min_size=1,
    max_size=40,
)


def build_random(
    events: list[tuple[int, int, int, bool]], gap_min: int, window_h: float
) -> Dataset:
    t = datetime(2024, 1, 1, tzinfo=UTC)
    msgs: list[dict[str, Any]] = []
    for thread, sender, gap_s, is_like in events:
        t += timedelta(seconds=gap_s)
        row: dict[str, Any] = {"thread_id": thread, "sender_id": sender, "ts": t}
        if is_like:
            row["type"] = "like"
        else:
            row["text"] = "x"
        msgs.append(row)
    return build_dataset(
        [{"display_name": n} for n in "ABC"],
        [{"participants": [0, 1, 2]}, {"participants": [0, 1, 2]}],
        msgs,
        session_gap_min=gap_min,
        reply_window_h=window_h,
    )


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_events, st.sampled_from([1, 60, 600]), st.sampled_from([0.5, 12.0]))
def test_properties(events: list[tuple[int, int, int, bool]], gap: int, window: float) -> None:
    ds = build_random(events, gap, window)
    msgs = ds.messages
    total = len([e for e in events if not e[3]])
    stats = ss.turn_stats(msgs)
    # sum of per-person messages = total
    assert int(stats["messages"].sum() or 0) == total
    t = ss.session_table(msgs)
    # every session has exactly one initiator
    assert t["initiator_id"].null_count() == 0
    starts = ss.counted(msgs).group_by("session_id").agg(pl.col("is_session_start").sum())
    assert set(starts.collect()["is_session_start"].to_list()) <= {1}
    init = ss.initiation_rates(msgs)
    assert int(init["sessions_started"].sum() or 0) == t.height
    if t.height:
        assert abs(float(init["rate"].sum()) - 1.0) < 1e-9
    assert int(t["messages"].sum() or 0) == total
    # durations are non-negative
    assert all(d >= 0 for d in t["duration_ms"].to_list())
    tt = ss.turn_table(msgs)
    assert int(tt["messages"].sum() or 0) == total
    assert all(1 <= b <= m for b, m in zip(tt["bursts"], tt["messages"], strict=True))
