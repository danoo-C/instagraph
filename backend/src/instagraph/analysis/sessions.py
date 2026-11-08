"""Sessions, initiations, last word and turns (spec §6.4).

All functions are pure: they take a messages frame (``LazyFrame`` or ``DataFrame``,
typically :func:`instagraph.api.filters.filtered_messages` output, optionally narrowed
to one thread) and return eager ``DataFrame`` s. Only *counted* messages take part
(``type`` not in {system, like}); other rows are ignored automatically.

Derived columns, not recomputed gaps
------------------------------------
``session_id``, ``is_session_start`` and ``turn_id`` are computed once per thread at
import with the settings' ``session_gap``. When the request filters by date (or people),
the derived columns stay as they are: a session that crosses the date boundary is
counted with the messages that remain visible, and its initiator is only known when its
first message is visible (``is_session_start``). Gaps are never recomputed here.

Reusable API (imported by awards / wrapped / compare)
----------------------------------------------------
* ``session_table(msgs) -> DataFrame[session_id, thread_id, start_ts, end_ts,
  duration_ms, messages, initiator_id (null if the start is not visible),
  last_sender_id, first_message_id, last_message_id]``
* ``session_summary(sessions) -> SessionSummary`` (count, avg/median messages & ms)
* ``initiation_rates(msgs) -> DataFrame[sender_id, sessions_started, rate]`` —
  ``rate = sessions_started / visible sessions`` (sums to 1 without filters).
* ``last_word_counts(msgs) -> DataFrame[sender_id, last_words, rate]``
* ``turn_table(msgs, double_text_gap_ms=60_000) -> DataFrame[turn_id, thread_id,
  sender_id, messages, bursts, first_message_id, last_message_id, first_ts, last_ts]``
  (``bursts`` = 1 + gaps inside the turn longer than ``double_text_gap_ms``).
* ``turn_stats(msgs) -> DataFrame[sender_id, turns, messages, avg_messages_per_turn,
  double_texts, triple_texts]`` — a *double text* is a turn with exactly 2 bursts, a
  *triple text* a turn with ≥ 3 bursts (messages typed within 1 min of each other are
  one burst, so multi-line typing is not a double text).
* ``conversation_starters(msgs, limit=10) -> DataFrame[key, text, count, message_ids]``
* ``bin_counts(values, upper_edges) -> list[int]`` and the edge helpers
  ``session_length_edges`` / ``SESSION_DURATION_EDGES_MS``.
"""

import math
from dataclasses import dataclass

import polars as pl

from instagraph.api.filters import is_counted_message

Frame = pl.LazyFrame | pl.DataFrame

DOUBLE_TEXT_GAP_MS = 60_000

#: Upper edges (inclusive) of session duration bins; the last bin is open-ended.
SESSION_DURATION_EDGES_MS: list[int] = [
    0,
    60_000,
    5 * 60_000,
    15 * 60_000,
    30 * 60_000,
    3_600_000,
    2 * 3_600_000,
    4 * 3_600_000,
    8 * 3_600_000,
]


def counted(msgs: Frame) -> pl.LazyFrame:
    """Counted messages that carry derived conversation fields."""
    return msgs.lazy().filter(is_counted_message() & pl.col("session_id").is_not_null())


# --------------------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------------------


def session_table(msgs: Frame) -> pl.DataFrame:
    """One row per visible session (see module docstring), ordered by start."""
    lf = counted(msgs).sort("message_id")
    return (
        lf.group_by("session_id")
        .agg(
            pl.col("thread_id").first(),
            pl.col("ts").min().alias("start_ts"),
            pl.col("ts").max().alias("end_ts"),
            pl.len().cast(pl.Int64).alias("messages"),
            pl.col("sender_id").filter(pl.col("is_session_start")).first().alias("initiator_id"),
            pl.col("sender_id").last().alias("last_sender_id"),
            pl.col("message_id").first().alias("first_message_id"),
            pl.col("message_id").last().alias("last_message_id"),
        )
        .with_columns(
            (pl.col("end_ts") - pl.col("start_ts")).dt.total_milliseconds().alias("duration_ms")
        )
        .sort("first_message_id")
        .collect()
    )


@dataclass(frozen=True)
class SessionSummary:
    count: int
    avg_messages: float | None
    median_messages: float | None
    avg_duration_ms: float | None
    median_duration_ms: float | None


def _opt_float(value: object) -> float | None:
    if value is None:
        return None
    f = float(value)  # type: ignore[arg-type]
    return None if math.isnan(f) else f


def session_summary(sessions: pl.DataFrame) -> SessionSummary:
    """Count and avg/median length (messages and ms) of a :func:`session_table`."""
    if sessions.is_empty():
        return SessionSummary(0, None, None, None, None)
    return SessionSummary(
        count=sessions.height,
        avg_messages=_opt_float(sessions["messages"].mean()),
        median_messages=_opt_float(sessions["messages"].median()),
        avg_duration_ms=_opt_float(sessions["duration_ms"].mean()),
        median_duration_ms=_opt_float(sessions["duration_ms"].median()),
    )


def initiation_rates(msgs: Frame) -> pl.DataFrame:
    """``[sender_id, sessions_started, rate]``, sorted by sessions_started desc."""
    lf = counted(msgs)
    total = lf.select(pl.col("session_id").n_unique()).collect().item()
    out = (
        lf.filter(pl.col("is_session_start"))
        .group_by("sender_id")
        .agg(pl.len().cast(pl.Int64).alias("sessions_started"))
        .collect()
    )
    return out.with_columns(
        (pl.col("sessions_started") / total if total else pl.lit(0.0)).alias("rate")
    ).sort(["sessions_started", "sender_id"], descending=[True, False])


def last_word_counts(msgs: Frame) -> pl.DataFrame:
    """``[sender_id, last_words, rate]``: who sent the last visible message of sessions."""
    sessions = session_table(msgs)
    if sessions.is_empty():
        return pl.DataFrame(
            schema={"sender_id": pl.UInt32, "last_words": pl.Int64, "rate": pl.Float64}
        )
    return (
        sessions.group_by(pl.col("last_sender_id").alias("sender_id"))
        .agg(pl.len().cast(pl.Int64).alias("last_words"))
        .with_columns((pl.col("last_words") / sessions.height).alias("rate"))
        .sort(["last_words", "sender_id"], descending=[True, False])
    )


def session_length_edges(max_messages: int) -> list[int]:
    """Log-2 upper edges for session length in messages: 1, 2, 4, 8, … ≥ max."""
    edges = [1]
    while edges[-1] < max_messages:
        edges.append(edges[-1] * 2)
    return edges


def bin_counts(values: list[int] | list[float], upper_edges: list[int]) -> list[int]:
    """Histogram with inclusive upper edges plus one trailing open bin (``> last``).

    Bin ``i`` counts ``upper_edges[i-1] < v <= upper_edges[i]`` (bin 0: ``v <= e0``).
    Returns ``len(upper_edges) + 1`` counts.
    """
    counts = [0] * (len(upper_edges) + 1)
    if not values:
        return counts
    idx = pl.Series(upper_edges, dtype=pl.Float64).search_sorted(
        pl.Series(values, dtype=pl.Float64), side="left"
    )
    for i in idx.to_list():
        counts[int(i)] += 1
    return counts


# --------------------------------------------------------------------------------------
# Turns
# --------------------------------------------------------------------------------------


def turn_table(msgs: Frame, double_text_gap_ms: int = DOUBLE_TEXT_GAP_MS) -> pl.DataFrame:
    """One row per visible turn (see module docstring), ordered by first message."""
    lf = (
        counted(msgs)
        .filter(pl.col("turn_id").is_not_null())
        .sort("message_id")
        .with_columns(
            pl.col("ts").diff().over("turn_id").dt.total_milliseconds().alias("_gap"),
        )
    )
    return (
        lf.group_by("turn_id")
        .agg(
            pl.col("thread_id").first(),
            pl.col("sender_id").first(),
            pl.len().cast(pl.Int64).alias("messages"),
            ((pl.col("_gap") > double_text_gap_ms).sum().cast(pl.Int64) + 1).alias("bursts"),
            pl.col("message_id").first().alias("first_message_id"),
            pl.col("message_id").last().alias("last_message_id"),
            pl.col("ts").min().alias("first_ts"),
            pl.col("ts").max().alias("last_ts"),
        )
        .sort("first_message_id")
        .collect()
    )


def turn_stats(msgs: Frame, double_text_gap_ms: int = DOUBLE_TEXT_GAP_MS) -> pl.DataFrame:
    """``[sender_id, turns, messages, avg_messages_per_turn, double_texts, triple_texts]``."""
    turns = turn_table(msgs, double_text_gap_ms)
    return (
        turns.group_by("sender_id")
        .agg(
            pl.len().cast(pl.Int64).alias("turns"),
            pl.col("messages").sum().cast(pl.Int64),
            (pl.col("bursts") == 2).sum().cast(pl.Int64).alias("double_texts"),
            (pl.col("bursts") >= 3).sum().cast(pl.Int64).alias("triple_texts"),
        )
        .with_columns((pl.col("messages") / pl.col("turns")).alias("avg_messages_per_turn"))
        .select(
            "sender_id",
            "turns",
            "messages",
            "avg_messages_per_turn",
            "double_texts",
            "triple_texts",
        )
        .sort(["messages", "sender_id"], descending=[True, False])
    )


# --------------------------------------------------------------------------------------
# Conversation starters
# --------------------------------------------------------------------------------------


def conversation_starters(msgs: Frame, limit: int = 10) -> pl.DataFrame:
    """Most common first messages of sessions: ``[key, text, count, message_ids]``.

    Texts are grouped case-insensitively after trimming whitespace and trailing
    punctuation (``"Ahoj!"`` == ``"ahoj"``); ``text`` is the most frequent original form.
    Only visible session starts with text count.
    """
    starts = (
        counted(msgs)
        .filter(pl.col("is_session_start") & pl.col("text").is_not_null())
        .with_columns(
            pl.col("text")
            .str.strip_chars()
            .str.to_lowercase()
            .str.strip_chars_end(" .!?,;:…")
            .alias("key"),
            pl.col("text").str.strip_chars().alias("_orig"),
        )
        .filter(pl.col("key").str.len_chars() > 0)
        .sort("message_id")
        .collect()
    )
    if starts.is_empty():
        return pl.DataFrame(
            schema={
                "key": pl.String,
                "text": pl.String,
                "count": pl.Int64,
                "message_ids": pl.List(pl.UInt32),
            }
        )
    return (
        starts.group_by("key")
        .agg(
            pl.col("_orig").mode().sort().first().alias("text"),
            pl.len().cast(pl.Int64).alias("count"),
            pl.col("message_id").alias("message_ids"),
            pl.col("message_id").min().alias("_first"),
        )
        .sort(["count", "_first"], descending=[True, False])
        .head(limit)
        .drop("_first")
    )
