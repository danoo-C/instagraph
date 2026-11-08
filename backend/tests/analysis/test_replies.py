"""Reply latency / reply rate / effort index (§6.4, §6.9).

Same fixture as ``test_sessions.py`` (see its docstring): replies are message 2 (B,
270 000 ms), 3 (A, 300 000 ms) and 7 (B, 9 600 000 ms). Turns: T1 A replied, T2 B
replied, T3 A replied (2 h 40 min), T4 B unanswered (next turn ~20 h later), T5 A
unanswered (last).
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import polars as pl
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from instagraph.analysis import replies as rp
from instagraph.store.dataset import Dataset
from instagraph.testing import build_dataset

H12 = 12 * 3_600_000

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


def _msgs() -> pl.LazyFrame:
    return build_dataset(PEOPLE, THREADS, MESSAGES).messages


def test_reply_latency_stats() -> None:
    s = {r["sender_id"]: r for r in rp.reply_latency_stats(_msgs()).iter_rows(named=True)}
    a, b = s[0], s[1]
    assert a["replies"] == 1 and a["median_ms"] == 300_000 and a["fastest_ms"] == 300_000
    assert b["replies"] == 2
    assert b["median_ms"] == 4_935_000 and b["mean_ms"] == 4_935_000
    assert b["p25_ms"] == 270_000 + 0.25 * 9_330_000
    assert b["p90_ms"] == 270_000 + 0.9 * 9_330_000
    assert b["fastest_ms"] == 270_000 and b["slowest_ms"] == 9_600_000


def test_histogram() -> None:
    edges = rp.latency_bin_edges(H12)
    assert edges[0] == 1_000 and edges[-1] == H12 and len(edges) == 15
    assert rp.latency_bin_edges(90_000) == [1_000, 2_000, 5_000, 10_000, 30_000, 60_000, 90_000]
    h = rp.latency_histogram(_msgs(), edges)
    # 270 s and 300 s → "≤5 min" (index 7); 2 h 40 min → "≤4 h" (index 12)
    assert h.rows() == [(0, 7, 1), (1, 7, 1), (1, 12, 1)]


def test_unanswered_turns() -> None:
    u = rp.unanswered_turns(_msgs(), H12)
    assert u.rows() == [(0, 3, 2, 1, 2 / 3), (1, 2, 1, 1, 0.5)]
    # with a 1 h window the 2 h 40 min reply no longer counts
    u1 = {r[0]: r for r in rp.unanswered_turns(_msgs(), 3_600_000).rows()}
    assert u1[0] == (0, 3, 1, 2, 1 / 3)
    tr = rp.turn_replies(_msgs(), H12)
    assert tr["replied"].to_list() == [True, True, True, False, False]
    assert tr["wait_ms"].to_list()[:3] == [270_000, 300_000, 9_600_000]


def test_longest_wait() -> None:
    lw = rp.longest_answered_wait(_msgs())
    assert lw is not None
    assert (lw.reply_message_id, lw.before_message_id, lw.latency_ms) == (7, 6, 9_600_000)
    assert (lw.replier_id, lw.waited_for_id) == (1, 0)
    assert rp.longest_answered_wait(_msgs().filter(pl.lit(False))) is None


def test_prev_sender() -> None:
    df = rp.with_prev_sender(_msgs()).collect()
    assert df["prev_sender_id"].to_list() == [None, 0, 0, 1, 0, 0, 0, 1, 1]


def test_effort_index() -> None:
    e = {r["sender_id"]: r for r in rp.effort_index(_msgs()).iter_rows(named=True)}
    a, b = e[0], e[1]
    assert a["share"] == 6 / 9 and a["initiation_rate"] == 2 / 3
    assert a["median_reply_ms"] == 300_000 and b["median_reply_ms"] == 4_935_000
    assert a["avg_words"] == 1.0 and abs(b["avg_words"] - 4 / 3) < 1e-12
    zs = (a["z_share"], a["z_initiation"], a["z_reply_speed"], a["z_length"])
    assert zs == pytest.approx((1, 1, 1, -1))
    assert a["combined_z"] == pytest.approx(0.5) and b["combined_z"] == pytest.approx(-0.5)
    assert rp.effort_index(_msgs().filter(pl.lit(False))).is_empty()


# --------------------------------------------------------------------------------------
# Property tests
# --------------------------------------------------------------------------------------


def _random(events: list[tuple[int, int, int]], window_h: float) -> Dataset:
    t = datetime(2024, 1, 1, tzinfo=UTC)
    msgs: list[dict[str, Any]] = []
    for thread, sender, gap_s in events:
        t += timedelta(seconds=gap_s)
        msgs.append({"thread_id": thread, "sender_id": sender, "ts": t, "text": "x"})
    return build_dataset(
        [{"display_name": n} for n in "ABC"],
        [{"participants": [0, 1]}, {"participants": [0, 1, 2]}],
        msgs,
        reply_window_h=window_h,
    )


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    st.lists(
        st.tuples(
            st.integers(0, 1),
            st.integers(0, 2),
            st.sampled_from([0, 1, 59, 61, 600, 7_200, 30_000, 50_000, 100_000]),
        ),
        min_size=1,
        max_size=40,
    ),
    st.sampled_from([1.0, 12.0]),
)
def test_reply_properties(events: list[tuple[int, int, int]], window_h: float) -> None:
    window_ms = int(window_h * 3_600_000)
    msgs = _random(events, window_h).messages
    lat = rp.replies(msgs).collect()["reply_latency_ms"].to_list()
    assert all(0 <= v <= window_ms for v in lat)
    stats = rp.reply_latency_stats(msgs)
    assert int(stats["replies"].sum() or 0) == len(lat)
    for r in stats.iter_rows(named=True):
        assert r["fastest_ms"] <= r["p25_ms"] <= r["median_ms"] <= r["p75_ms"] <= r["p90_ms"]
        assert r["p90_ms"] <= r["slowest_ms"]
    hist = rp.latency_histogram(msgs, rp.latency_bin_edges(window_ms))
    assert int(hist["count"].sum() or 0) == len(lat)
    u = rp.unanswered_turns(msgs, window_ms)
    assert (u["replied"] + u["unanswered"] == u["turns"]).all()
    # every reply answers exactly one turn
    assert int(u["replied"].sum() or 0) == len(lat)
