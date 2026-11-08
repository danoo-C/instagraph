"""Vectorized derivation vs. the reference implementation in ``instagraph.testing``."""

from typing import Any

import polars as pl
from hypothesis import given, settings
from hypothesis import strategies as st

from instagraph import textproc
from instagraph.importer.derive import (
    derive_conversation_columns,
    derive_text_columns,
    text_metrics,
)
from instagraph.store.dataset import NON_COUNTED_TYPES
from instagraph.testing import derive_conversation_fields

TYPES = ["text", "photo", "like", "system", "call", "share", "unknown"]


@st.composite
def message_sets(draw: st.DrawFn) -> list[dict[str, Any]]:
    n = draw(st.integers(min_value=0, max_value=60))
    n_threads = draw(st.integers(min_value=1, max_value=4))
    ts = 1_700_000_000_000
    rows: list[dict[str, Any]] = []
    for i in range(n):
        ts += draw(st.sampled_from([0, 1, 30_000, 59 * 60_000, 60 * 60_000, 61 * 60_000]))
        ts += draw(st.integers(min_value=0, max_value=14 * 3_600_000)) if draw(st.booleans()) else 0
        rows.append(
            {
                "message_id": i,
                "thread_id": draw(st.integers(min_value=0, max_value=n_threads - 1)),
                "sender_id": draw(st.integers(min_value=0, max_value=2)),
                "ts_ms": ts,
                "type": draw(st.sampled_from(TYPES)),
            }
        )
    return rows


def _vectorized(
    rows: list[dict[str, Any]], gap_min: float, window_h: float
) -> dict[int, tuple[Any, ...]]:
    df = pl.DataFrame(
        rows,
        schema={
            "message_id": pl.UInt32,
            "thread_id": pl.UInt32,
            "sender_id": pl.UInt32,
            "ts_ms": pl.Int64,
            "type": pl.String,
        },
    )
    out = derive_conversation_columns(
        df, session_gap_ms=int(gap_min * 60_000), reply_window_ms=int(window_h * 3_600_000)
    )
    assert out["message_id"].to_list() == df["message_id"].to_list()  # order preserved
    return {
        r["message_id"]: (
            r["session_id"],
            r["is_session_start"],
            r["turn_id"],
            r["reply_latency_ms"],
        )
        for r in out.iter_rows(named=True)
    }


@settings(max_examples=300, deadline=None)
@given(
    rows=message_sets(),
    gap_min=st.sampled_from([1, 60, 90]),
    window_h=st.sampled_from([0.5, 12]),
)
def test_matches_reference(rows: list[dict[str, Any]], gap_min: float, window_h: float) -> None:
    ref = [dict(r) for r in rows]
    derive_conversation_fields(ref, session_gap_min=gap_min, reply_window_h=window_h)
    expected = {
        r["message_id"]: (
            r["session_id"],
            r["is_session_start"],
            r["turn_id"],
            r["reply_latency_ms"],
        )
        for r in ref
    }
    got = _vectorized(rows, gap_min, window_h) if rows else {}
    assert got == expected


@settings(max_examples=200, deadline=None)
@given(rows=message_sets(), window_h=st.sampled_from([0.5, 12]))
def test_properties(rows: list[dict[str, Any]], window_h: float) -> None:
    if not rows:
        return
    got = _vectorized(rows, 60, window_h)
    window_ms = int(window_h * 3_600_000)
    by_id = {r["message_id"]: r for r in rows}
    sessions: dict[int, list[int]] = {}
    for mid, (sid, start, tid, lat) in got.items():
        counted = by_id[mid]["type"] not in NON_COUNTED_TYPES
        if not counted:
            assert (sid, start, tid, lat) == (None, False, None, None)
            continue
        assert sid is not None and tid is not None
        sessions.setdefault(sid, []).append(mid)
        if lat is not None:
            assert 0 <= lat <= window_ms
    # every session has exactly one start (the initiator) and lives in one thread
    for mids in sessions.values():
        assert sum(1 for m in mids if got[m][1]) == 1
        assert got[min(mids)][1]
        assert len({by_id[m]["thread_id"] for m in mids}) == 1
    # per-person message counts sum to the total
    counted_rows = [r for r in rows if r["type"] not in NON_COUNTED_TYPES]
    per_person: dict[int, int] = {}
    for r in counted_rows:
        per_person[r["sender_id"]] = per_person.get(r["sender_id"], 0) + 1
    assert sum(per_person.values()) == len(counted_rows)
    # a turn has a single sender
    turns: dict[int, set[int]] = {}
    for mid, (_, _, tid, _) in got.items():
        if tid is not None:
            turns.setdefault(tid, set()).add(by_id[mid]["sender_id"])
    assert all(len(s) == 1 for s in turns.values())


def test_hand_example() -> None:
    m = 60_000
    rows = [
        {"message_id": 0, "thread_id": 0, "sender_id": 1, "ts_ms": 0, "type": "text"},
        {"message_id": 1, "thread_id": 0, "sender_id": 0, "ts_ms": 2 * m, "type": "text"},
        {"message_id": 2, "thread_id": 0, "sender_id": 0, "ts_ms": 3 * m, "type": "like"},
        {"message_id": 3, "thread_id": 0, "sender_id": 0, "ts_ms": 4 * m, "type": "text"},
        {"message_id": 4, "thread_id": 1, "sender_id": 2, "ts_ms": 5 * m, "type": "text"},
        {"message_id": 5, "thread_id": 0, "sender_id": 1, "ts_ms": 200 * m, "type": "text"},
    ]
    got = _vectorized(rows, 60, 12)
    assert got[0] == (0, True, 0, None)
    assert got[1] == (0, False, 1, 2 * m)
    assert got[2] == (None, False, None, None)
    assert got[3] == (0, False, 1, None)
    assert got[5] == (1, True, 2, 196 * m)
    assert got[4] == (2, True, 3, None)  # thread 1 numbered after thread 0


def test_datetime_ts_column() -> None:
    df = pl.DataFrame(
        {
            "message_id": [0, 1],
            "thread_id": [0, 0],
            "sender_id": [0, 1],
            "ts": [0, 1000],
            "type": ["text", "text"],
        },
        schema_overrides={"message_id": pl.UInt32, "thread_id": pl.UInt32, "sender_id": pl.UInt32},
    ).with_columns(pl.col("ts").cast(pl.Datetime("ms", "UTC")))
    out = derive_conversation_columns(df, session_gap_ms=60_000, reply_window_ms=10_000)
    assert out["reply_latency_ms"].to_list() == [None, 1000]
    assert out["is_session_start"].to_list() == [True, False]


def test_empty_frame() -> None:
    df = pl.DataFrame(
        schema={
            "message_id": pl.UInt32,
            "thread_id": pl.UInt32,
            "sender_id": pl.UInt32,
            "ts_ms": pl.Int64,
            "type": pl.String,
        }
    )
    out = derive_conversation_columns(df, session_gap_ms=1, reply_window_ms=1)
    assert out.height == 0
    assert "session_id" in out.columns


SAMPLES = [
    "Ahoj, ako sa máš? 😂",
    "čšťžýáíéúäôň",
    "👨‍👩‍👧 family 🇸🇰 flag 👍🏽 tone ❤️ heart",
    "keycap 1️⃣ and #️⃣",
    "  padded\r\n text  ",
    "visit https://example.com/x?y=1 now",
    "don’t it's",
    "",
    "   ",
    "plain ascii 123 # *",
]


@settings(max_examples=300, deadline=None)
@given(st.lists(st.one_of(st.none(), st.text(max_size=30), st.sampled_from(SAMPLES)), max_size=20))
def test_text_metrics_match_textproc(texts: list[str | None]) -> None:
    words, chars, emojis = text_metrics(texts)
    for t, w, c, e in zip(texts, words, chars, emojis, strict=True):
        assert w == textproc.word_count(t)
        assert c == textproc.grapheme_count(t)
        assert e == textproc.extract_emojis(t)


def test_derive_text_columns_skips_system_and_like() -> None:
    df = pl.DataFrame(
        {
            "text": ["ahoj 😂 svet", "Liked a message", "X named the group 😂", None],
            "type": ["text", "like", "system", "photo"],
        }
    )
    out = derive_text_columns(df)
    assert out["word_count"].to_list() == [2, 0, 0, 0]
    assert out["char_count"].to_list() == [11, 0, 0, 0]
    assert out["emojis"].to_list() == [["😂"], [], [], []]
