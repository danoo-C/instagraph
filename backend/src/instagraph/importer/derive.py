"""Derived message columns (spec §4.1 "derived at import", §6.1, §6.4).

Two families:

* **Text metrics** — ``word_count``, ``char_count`` (grapheme clusters of the stripped
  text) and ``emojis`` (storage form: U+FE0F stripped, skin tones kept). They depend
  only on ``text`` and ``type`` and are computed once per message, in the parse
  workers (:func:`derive_text_columns` on each thread chunk), so they parallelize with
  parsing. ``system``/``like`` messages get ``0``/``0``/``[]``. Values are identical to
  ``textproc.word_count`` / ``grapheme_count`` / ``extract_emojis`` (ASCII-only texts take
  an equivalent fast path).
* **Conversation fields** — ``session_id``, ``is_session_start``, ``turn_id``,
  ``reply_latency_ms`` (§6.4), vectorized with Polars over the whole messages table
  (:func:`derive_conversation_columns`). Semantics are exactly those of
  ``instagraph.testing.derive_conversation_fields``: only counted messages (type not
  ``system``/``like``) take part, ids are global and assigned thread-major (threads by
  ``thread_id``, messages by ``message_id``).
"""

from collections.abc import Sequence

import polars as pl

from instagraph import textproc
from instagraph.store.dataset import NON_COUNTED_TYPES

CONVERSATION_COLUMNS: tuple[str, ...] = (
    "session_id",
    "is_session_start",
    "turn_id",
    "reply_latency_ms",
)
TEXT_COLUMNS: tuple[str, ...] = ("word_count", "char_count", "emojis")


# --------------------------------------------------------------------------------------
# Text metrics
# --------------------------------------------------------------------------------------


def _char_count(text: str) -> int:
    if text.isascii():
        # Every ASCII char is its own grapheme cluster except CR LF (one cluster).
        stripped = text.strip()
        return len(stripped) - stripped.count("\r\n")
    return textproc.grapheme_count(text)


def _emojis(text: str) -> list[str]:
    if text.isascii():
        # Every emoji (incl. keycaps, which need U+20E3) contains a non-ASCII code point.
        return []
    return textproc.extract_emojis(text)


def text_metrics(
    texts: Sequence[str | None],
) -> tuple[list[int], list[int], list[list[str]]]:
    """``(word_counts, char_counts, emojis)`` for each text (``None``/empty → 0, 0, [])."""
    words: list[int] = []
    chars: list[int] = []
    emojis: list[list[str]] = []
    for text in texts:
        if not text:
            words.append(0)
            chars.append(0)
            emojis.append([])
            continue
        words.append(textproc.word_count(text))
        chars.append(_char_count(text))
        emojis.append(_emojis(text))
    return words, chars, emojis


def derive_text_columns(df: pl.DataFrame) -> pl.DataFrame:
    """Add ``word_count``, ``char_count``, ``emojis`` from ``text`` and ``type``.

    ``type`` may be a String or Enum column. Messages of type ``system``/``like`` get
    zero counts and no emojis.
    """
    if df.height == 0:
        return df.with_columns(
            pl.lit(None, pl.UInt32).alias("word_count"),
            pl.lit(None, pl.UInt32).alias("char_count"),
            pl.lit(None, pl.List(pl.String)).alias("emojis"),
        )
    counted = df.select(
        pl.when(pl.col("type").cast(pl.String).is_in(NON_COUNTED_TYPES))
        .then(pl.lit(None, pl.String))
        .otherwise(pl.col("text"))
        .alias("t")
    )["t"].to_list()
    words, chars, emojis = text_metrics(counted)
    return df.with_columns(
        pl.Series("word_count", words, dtype=pl.UInt32),
        pl.Series("char_count", chars, dtype=pl.UInt32),
        pl.Series("emojis", emojis, dtype=pl.List(pl.String)),
    )


# --------------------------------------------------------------------------------------
# Conversation fields
# --------------------------------------------------------------------------------------


def _ts_ms_expr(df: pl.DataFrame) -> pl.Expr:
    dtype = df.schema["ts"] if "ts" in df.columns else None
    if "ts_ms" in df.columns:
        return pl.col("ts_ms").cast(pl.Int64)
    if isinstance(dtype, pl.Datetime):
        return pl.col("ts").dt.epoch("ms")
    return pl.col("ts").cast(pl.Int64)


def derive_conversation_columns(
    messages: pl.DataFrame,
    *,
    session_gap_ms: int,
    reply_window_ms: int,
) -> pl.DataFrame:
    """Return ``messages`` with the four §6.4 columns (re)computed.

    Requires ``message_id``, ``thread_id``, ``sender_id``, ``type`` and ``ts`` (Datetime
    or epoch ms) or ``ts_ms``. ``message_id`` must ascend with time within each thread.
    Row order of the input is preserved; existing derived columns are replaced.
    """
    base = messages.drop([c for c in CONVERSATION_COLUMNS if c in messages.columns])
    if base.height == 0:
        return base.with_columns(
            pl.lit(None, pl.UInt32).alias("session_id"),
            pl.lit(False).alias("is_session_start"),
            pl.lit(None, pl.UInt32).alias("turn_id"),
            pl.lit(None, pl.Int64).alias("reply_latency_ms"),
        )
    counted = (
        base.select(
            pl.col("message_id"),
            pl.col("thread_id"),
            pl.col("sender_id"),
            _ts_ms_expr(base).alias("_ts"),
            pl.col("type").cast(pl.String).alias("_type"),
        )
        .filter(~pl.col("_type").is_in(NON_COUNTED_TYPES))
        .sort(["thread_id", "message_id"])
    )
    same_thread = pl.col("thread_id") == pl.col("thread_id").shift(1)
    prev_ts = pl.when(same_thread).then(pl.col("_ts").shift(1))
    prev_sender = pl.when(same_thread).then(pl.col("sender_id").shift(1))
    derived = (
        counted.with_columns(
            (pl.col("_ts") - prev_ts).alias("_gap"),
            prev_sender.alias("_prev_sender"),
        )
        .with_columns(
            (pl.col("_gap").is_null() | (pl.col("_gap") > session_gap_ms)).alias(
                "is_session_start"
            ),
            (
                pl.col("_prev_sender").is_null() | (pl.col("_prev_sender") != pl.col("sender_id"))
            ).alias("_new_turn"),
        )
        .with_columns(
            (pl.col("is_session_start").cast(pl.UInt32).cum_sum() - 1)
            .cast(pl.UInt32)
            .alias("session_id"),
            (pl.col("_new_turn").cast(pl.UInt32).cum_sum() - 1).cast(pl.UInt32).alias("turn_id"),
            pl.when(
                pl.col("_new_turn")
                & pl.col("_gap").is_not_null()
                & (pl.col("_gap") <= reply_window_ms)
            )
            .then(pl.col("_gap"))
            .otherwise(None)
            .cast(pl.Int64)
            .alias("reply_latency_ms"),
        )
        .select("message_id", *CONVERSATION_COLUMNS)
    )
    # Join on the key column only, then attach (avoids copying the wide frame).
    aligned = base.select("message_id").join(
        derived, on="message_id", how="left", maintain_order="left"
    )
    return base.with_columns(
        aligned["session_id"],
        aligned["is_session_start"].fill_null(False),
        aligned["turn_id"],
        aligned["reply_latency_ms"],
    )
