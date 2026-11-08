"""Reply latency, reply rate, left-on-read and the effort index (spec §6.4, §6.9).

Inputs are messages frames (``LazyFrame``/``DataFrame``, usually
:func:`instagraph.api.filters.filtered_messages` output). Outputs are eager DataFrames.

Semantics
---------
* A *reply* is the first message of a turn whose ``reply_latency_ms`` is set (derived at
  import: previous turn in the same thread, by someone else, ended ≤ reply window
  earlier). The latency is attributed to the replier (``sender_id``). Values come from
  the derived column and are never recomputed, so a date filter keeps latencies of
  replies whose previous turn lies outside the range.
* A turn *got a reply* when the next visible turn in the same thread starts within
  ``reply_window_ms`` of its last message. Reply detection needs the other side of the
  conversation, so callers should pass messages WITHOUT the people filter and narrow
  the per-person result afterwards.

Reusable API
------------
* ``reply_latency_stats(msgs) -> DataFrame[sender_id, replies, median_ms, mean_ms,
  p25_ms, p75_ms, p90_ms, fastest_ms, slowest_ms]`` (percentiles linear-interpolated).
* ``latency_bin_edges(reply_window_ms) -> list[int]`` — log-spaced upper edges
  1 s … reply window (last edge == window).
* ``latency_histogram(msgs, edges) -> DataFrame[sender_id, bin, count]`` (``bin`` =
  index into edges; bin i holds ``edges[i-1] < v <= edges[i]``).
* ``turn_replies(msgs, reply_window_ms) -> DataFrame[turn_id, thread_id, sender_id,
  first_message_id, last_message_id, last_ts, replied, wait_ms]``.
* ``unanswered_turns(msgs, reply_window_ms) -> DataFrame[sender_id, turns, replied,
  unanswered, reply_rate]``.
* ``longest_answered_wait(msgs) -> LongestWait | None`` (reply + preceding message ids).
* ``with_prev_sender(msgs) -> LazyFrame`` adds ``prev_sender_id`` (sender of the previous
  counted message in the thread) — e.g. to get "replies to me".
* ``effort_index(msgs) -> DataFrame[sender_id, share, initiation_rate, median_reply_ms,
  avg_words, z_share, z_initiation, z_reply_speed, z_length, combined_z]`` — z-scores
  across the people present (population std; 0 when everyone is equal). Reply speed is
  the negated median latency (faster = more effort). ``combined_z`` is the mean of the
  available parts; always show it with the parts.
"""

from dataclasses import dataclass

import polars as pl

from instagraph.analysis.sessions import counted, initiation_rates, turn_table
from instagraph.api.filters import is_text_message

Frame = pl.LazyFrame | pl.DataFrame

_NICE_EDGES_MS: list[int] = [
    1_000,
    2_000,
    5_000,
    10_000,
    30_000,
    60_000,
    2 * 60_000,
    5 * 60_000,
    10 * 60_000,
    30 * 60_000,
    3_600_000,
    2 * 3_600_000,
    4 * 3_600_000,
    8 * 3_600_000,
    12 * 3_600_000,
    24 * 3_600_000,
    48 * 3_600_000,
    7 * 24 * 3_600_000,
    14 * 24 * 3_600_000,
]


def replies(msgs: Frame) -> pl.LazyFrame:
    """Counted messages that are replies (``reply_latency_ms`` not null)."""
    return counted(msgs).filter(pl.col("reply_latency_ms").is_not_null())


def _latency_aggs() -> list[pl.Expr]:
    lat = pl.col("reply_latency_ms")
    return [
        pl.len().cast(pl.Int64).alias("replies"),
        lat.median().alias("median_ms"),
        lat.mean().alias("mean_ms"),
        lat.quantile(0.25, interpolation="linear").alias("p25_ms"),
        lat.quantile(0.75, interpolation="linear").alias("p75_ms"),
        lat.quantile(0.90, interpolation="linear").alias("p90_ms"),
        lat.min().alias("fastest_ms"),
        lat.max().alias("slowest_ms"),
    ]


def reply_latency_stats(msgs: Frame) -> pl.DataFrame:
    """Per replier latency statistics (see module docstring)."""
    return (
        replies(msgs)
        .group_by("sender_id")
        .agg(_latency_aggs())
        .sort(["replies", "sender_id"], descending=[True, False])
        .collect()
    )


def latency_bin_edges(reply_window_ms: int) -> list[int]:
    """Log-spaced inclusive upper edges from 1 s up to (and ending at) the window."""
    edges = [e for e in _NICE_EDGES_MS if e < reply_window_ms]
    edges.append(reply_window_ms)
    return edges


def latency_histogram(msgs: Frame, edges: list[int]) -> pl.DataFrame:
    """``[sender_id, bin, count]`` (only non-empty bins). Values above the last edge
    (cannot happen with derived data) go to the last bin."""
    df = replies(msgs).select("sender_id", "reply_latency_ms").collect()
    if df.is_empty():
        return pl.DataFrame(schema={"sender_id": pl.UInt32, "bin": pl.Int64, "count": pl.Int64})
    idx = pl.Series(edges, dtype=pl.Int64).search_sorted(df["reply_latency_ms"], side="left")
    return (
        df.with_columns(idx.cast(pl.Int64).clip(0, len(edges) - 1).alias("bin"))
        .group_by("sender_id", "bin")
        .agg(pl.len().cast(pl.Int64).alias("count"))
        .sort("sender_id", "bin")
    )


def turn_replies(msgs: Frame, reply_window_ms: int) -> pl.DataFrame:
    """Per visible turn: did the next turn in the thread come within the window?"""
    turns = turn_table(msgs)
    if turns.is_empty():
        return turns.select(
            "turn_id", "thread_id", "sender_id", "first_message_id", "last_message_id", "last_ts"
        ).with_columns(
            pl.lit(None, pl.Boolean).alias("replied"), pl.lit(None, pl.Int64).alias("wait_ms")
        )
    return (
        turns.sort("thread_id", "first_message_id")
        .with_columns(
            (pl.col("first_ts").shift(-1).over("thread_id") - pl.col("last_ts"))
            .dt.total_milliseconds()
            .alias("wait_ms")
        )
        .with_columns(
            (pl.col("wait_ms").is_not_null() & (pl.col("wait_ms") <= reply_window_ms)).alias(
                "replied"
            )
        )
        .select(
            "turn_id",
            "thread_id",
            "sender_id",
            "first_message_id",
            "last_message_id",
            "last_ts",
            "replied",
            "wait_ms",
        )
        .sort("first_message_id")
    )


def unanswered_turns(msgs: Frame, reply_window_ms: int) -> pl.DataFrame:
    """``[sender_id, turns, replied, unanswered, reply_rate]`` per turn author."""
    t = turn_replies(msgs, reply_window_ms)
    return (
        t.group_by("sender_id")
        .agg(
            pl.len().cast(pl.Int64).alias("turns"),
            pl.col("replied").sum().cast(pl.Int64).alias("replied"),
        )
        .with_columns(
            (pl.col("turns") - pl.col("replied")).alias("unanswered"),
            (pl.col("replied") / pl.col("turns")).alias("reply_rate"),
        )
        .sort(["turns", "sender_id"], descending=[True, False])
    )


def with_prev_sender(msgs: Frame) -> pl.LazyFrame:
    """Counted messages with ``prev_sender_id`` (previous counted message in thread)."""
    return (
        counted(msgs)
        .sort("message_id")
        .with_columns(pl.col("sender_id").shift(1).over("thread_id").alias("prev_sender_id"))
    )


@dataclass(frozen=True)
class LongestWait:
    reply_message_id: int
    before_message_id: int | None
    latency_ms: int
    replier_id: int
    waited_for_id: int | None


def longest_answered_wait(msgs: Frame) -> LongestWait | None:
    """The reply with the largest latency and the visible message right before it."""
    df = (
        counted(msgs)
        .sort("message_id")
        .with_columns(
            pl.col("message_id").shift(1).over("thread_id").alias("_prev_id"),
            pl.col("sender_id").shift(1).over("thread_id").alias("_prev_sender"),
        )
        .filter(pl.col("reply_latency_ms").is_not_null())
        .sort(["reply_latency_ms", "message_id"], descending=[True, False])
        .head(1)
        .collect()
    )
    if df.is_empty():
        return None
    row = df.row(0, named=True)
    return LongestWait(
        reply_message_id=int(row["message_id"]),
        before_message_id=None if row["_prev_id"] is None else int(row["_prev_id"]),
        latency_ms=int(row["reply_latency_ms"]),
        replier_id=int(row["sender_id"]),
        waited_for_id=None if row["_prev_sender"] is None else int(row["_prev_sender"]),
    )


def _z(col: str) -> pl.Expr:
    c = pl.col(col)
    std = c.std(ddof=0)
    return pl.when(c.is_null()).then(None).when(std > 0).then((c - c.mean()) / std).otherwise(0.0)


def effort_index(msgs: Frame) -> pl.DataFrame:
    """Effort index parts and z-scores per sender (see module docstring)."""
    lf = counted(msgs)
    base = (
        lf.group_by("sender_id")
        .agg(
            pl.len().cast(pl.Int64).alias("_n"),
            pl.col("reply_latency_ms").median().alias("median_reply_ms"),
            pl.col("word_count").filter(is_text_message()).mean().alias("avg_words"),
        )
        .collect()
    )
    if base.is_empty():
        return pl.DataFrame(
            schema={
                "sender_id": pl.UInt32,
                **{
                    k: pl.Float64
                    for k in (
                        "share",
                        "initiation_rate",
                        "median_reply_ms",
                        "avg_words",
                        "z_share",
                        "z_initiation",
                        "z_reply_speed",
                        "z_length",
                        "combined_z",
                    )
                },
            }
        )
    total = int(base["_n"].sum())
    init = initiation_rates(lf).select("sender_id", pl.col("rate").alias("initiation_rate"))
    df = (
        base.join(init, on="sender_id", how="left")
        .with_columns(
            (pl.col("_n") / total).alias("share"),
            pl.col("initiation_rate").fill_null(0.0),
            pl.col("median_reply_ms").cast(pl.Float64),
            pl.col("avg_words").cast(pl.Float64),
            (-pl.col("median_reply_ms").cast(pl.Float64)).alias("_speed"),
        )
        .with_columns(
            _z("share").alias("z_share"),
            _z("initiation_rate").alias("z_initiation"),
            _z("_speed").alias("z_reply_speed"),
            _z("avg_words").alias("z_length"),
        )
        .with_columns(
            pl.mean_horizontal("z_share", "z_initiation", "z_reply_speed", "z_length").alias(
                "combined_z"
            )
        )
    )
    return df.select(
        "sender_id",
        "share",
        "initiation_rate",
        "median_reply_ms",
        "avg_words",
        "z_share",
        "z_initiation",
        "z_reply_speed",
        "z_length",
        "combined_z",
    ).sort("sender_id")
