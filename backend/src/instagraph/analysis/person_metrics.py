"""Per-person metric table (spec §6, §7.2 per-person table / awards).

:func:`person_metrics` returns ONE row per sender with the basic counts and every
award-relevant metric, computed directly from the derived columns of the messages
table (``word_count``, ``emojis``, ``session_id``/``is_session_start``, ``turn_id``,
``reply_latency_ms``) and the filtered reactions frame. It is reused by awards,
wrapped, compare and people.

Input frames
------------
* ``messages``: a :func:`~instagraph.api.filters.filtered_messages` frame (needs the
  ``hour`` column). Pass it **without** the ``people`` filter so that turn/reply/laughter
  context from other participants is visible; restrict the output rows with
  ``people=...`` instead.
* ``reactions``: a :func:`~instagraph.api.filters.filtered_reactions` frame (needs
  ``actor_id``, ``target_id``, ``emoji``, ``message_id``).

Definitions (all ratios 0..1, durations ms)
-------------------------------------------
* ``messages`` = counted messages (§6.1); ``text_messages`` = :func:`is_text_message`.
* ``avg_words`` = words / text_messages.
* ``emojis_per_message`` = emojis in counted messages / messages.
* ``night_share`` = share of messages with local hour 0–4; ``early_share`` hour 5–7.
* ``initiation_rate`` = sessions started / sessions the person took part in
  (sent ≥1 counted message in the session).
* ``msgs_per_turn`` = messages / distinct turns.
* ``reply_median_ms`` / ``reply_mean_ms`` over ``reply_latency_ms`` of their messages.
* ``unanswered_turns`` = turns whose next turn in the same thread is missing or does not
  carry a ``reply_latency_ms`` (i.e. no reply within the reply window, §6.4).
* ``laugh_messages`` = text messages matching any laughter pattern (case-insensitive).
* ``laughs_caused`` = laughter messages by someone else within 2 min after the person's
  last message of the preceding turn (credited to that person, §6.5).
* ``reels_posts`` = messages with ``share_kind`` in {reel, post}; ``voice_notes`` = Σ n_audios.
* ``hearts_given`` = heart-emoji reactions given + likes sent (§6.7 "hearts" total).
* ``reactions_received`` excludes self-reactions; ``reactions_per_message`` = received /
  messages.
* ``mattr`` = moving-average type-token ratio (window 500) over lower-cased tokens of
  text messages; ``tokens`` = number of tokens.
* ``question_rate`` = text messages containing ``?`` / text messages.
* ``calls_started`` = call records sent by the person; ``unsent`` = unsent messages.
"""

import re
from collections import Counter
from collections.abc import Iterable, Sequence

import polars as pl

from instagraph.api.filters import is_counted_message, is_text_message
from instagraph.store.app_settings import DEFAULT_LAUGHTER_PATTERNS
from instagraph.textproc import KEYCAP_PATTERN, WORD_PATTERN

MATTR_WINDOW = 500
LAUGH_CREDIT_MS = 2 * 60_000

#: Heart emojis (storage form: U+FE0F stripped).
HEART_EMOJIS: tuple[str, ...] = (
    "❤",
    "🧡",
    "💛",
    "💚",
    "💙",
    "💜",
    "🖤",
    "🤍",
    "🤎",
    "🩷",
    "🩵",
    "🩶",
    "💕",
    "💖",
    "💗",
    "💓",
    "💞",
    "💘",
    "💝",
    "❣",
    "❤‍🔥",
    "❤‍🩹",
    "😍",
    "🥰",
    "😘",
)

# Rust-regex (Polars) versions of the textproc URL / mention / word patterns.
_PL_URL = r"(?i)\b(?:https?://|www\.)[^\s<>\x22']+"
_PL_MENTION = r"(?:^|[^\w@])@\w(?:[\w.]*\w)?"
_PL_WORD = WORD_PATTERN

METRIC_COLUMNS: tuple[str, ...] = (
    "sender_id",
    "messages",
    "text_messages",
    "words",
    "chars",
    "avg_words",
    "emojis",
    "emojis_per_message",
    "night_share",
    "early_share",
    "sessions",
    "sessions_started",
    "initiation_rate",
    "turns",
    "msgs_per_turn",
    "replies",
    "reply_median_ms",
    "reply_mean_ms",
    "unanswered_turns",
    "reply_rate",
    "laugh_messages",
    "laugh_rate",
    "laughs_caused",
    "reels_posts",
    "voice_notes",
    "photos",
    "videos",
    "likes",
    "hearts_given",
    "reactions_given",
    "reactions_received",
    "reactions_per_message",
    "tokens",
    "mattr",
    "questions",
    "question_rate",
    "calls_started",
    "call_ms",
    "unsent",
    "active_days",
    "first_ts",
    "last_ts",
)


# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------


def mattr(tokens: Sequence[str], window: int = MATTR_WINDOW) -> float | None:
    """Moving-average type-token ratio (§6.5). Fewer tokens than ``window`` → plain TTR.

    ``None`` for an empty sequence.
    """
    n = len(tokens)
    if n == 0:
        return None
    if n <= window:
        return len(set(tokens)) / n
    counts: Counter[str] = Counter(tokens[:window])
    distinct = len(counts)
    total = distinct
    for i in range(window, n):
        new, old = tokens[i], tokens[i - window]
        counts[new] += 1
        if counts[new] == 1:
            distinct += 1
        counts[old] -= 1
        if counts[old] == 0:
            del counts[old]
            distinct -= 1
        total += distinct
    return total / (n - window + 1) / window


def tokens_expr(text: pl.Expr) -> pl.Expr:
    """Lower-cased word tokens (URLs and @mentions removed) as a List[String] expression.

    Mirrors ``textproc.tokenize_words(lowercase=True, remove_mentions=True)``.
    """
    cleaned = (
        text.fill_null("")
        .str.replace_all(_PL_URL, " ")
        .str.replace_all(KEYCAP_PATTERN, " ")
        .str.replace_all(_PL_MENTION, " ")
        .str.to_lowercase()
    )
    return cleaned.str.extract_all(_PL_WORD)


def laughter_expr(patterns: Iterable[str] | None = None) -> pl.Expr:
    """Boolean expression: ``text`` matches any laughter pattern (case-insensitive).

    Patterns are Python-style regexes (or literal emoji). If they cannot be compiled by
    the Polars regex engine we fall back to Python's ``re`` (slower).
    """
    pats = [p for p in (patterns if patterns is not None else DEFAULT_LAUGHTER_PATTERNS) if p]
    if not pats:
        return pl.lit(False)
    combined = "(?i)(?:" + "|".join(f"(?:{p})" for p in pats) + ")"
    try:
        pl.Series(["x"]).str.contains(combined)
    except Exception:
        py_re = re.compile("|".join(f"(?:{p})" for p in pats), re.IGNORECASE)
        return (
            pl.col("text")
            .map_elements(lambda s: bool(py_re.search(s)), return_dtype=pl.Boolean)
            .fill_null(False)
        )
    return pl.col("text").str.contains(combined).fill_null(False)


def _safe_div(num: str, den: str) -> pl.Expr:
    return pl.when(pl.col(den) > 0).then(pl.col(num) / pl.col(den)).otherwise(None)


# --------------------------------------------------------------------------------------
# Main table
# --------------------------------------------------------------------------------------


def _empty() -> pl.DataFrame:
    schema: dict[str, pl.DataType] = {c: pl.Float64() for c in METRIC_COLUMNS}
    schema["sender_id"] = pl.UInt32()
    schema["first_ts"] = pl.Datetime("ms", "UTC")
    schema["last_ts"] = pl.Datetime("ms", "UTC")
    return pl.DataFrame(schema=schema)


def person_metrics(
    messages: pl.LazyFrame,
    reactions: pl.LazyFrame | None = None,
    *,
    laughter_patterns: Iterable[str] | None = None,
    people: Iterable[int] | None = None,
    with_mattr: bool = True,
) -> pl.DataFrame:
    """One row per sender (see module docstring for every column).

    ``people`` restricts the OUTPUT rows (context is still computed from all rows).
    Rows are sorted by ``messages`` desc, then ``sender_id``.
    """
    laugh = laughter_expr(laughter_patterns)
    counted = (
        messages.filter(is_counted_message())
        .with_columns(
            is_text_message().alias("_is_text"),
            pl.col("emojis").list.len().alias("_n_emojis"),
        )
        .with_columns(
            (pl.col("_is_text") & laugh).alias("_laugh"),
            (pl.col("_is_text") & pl.col("text").str.contains("?", literal=True)).alias("_q"),
        )
        .collect()
    )
    if counted.is_empty():
        return _empty()

    base = counted.group_by("sender_id").agg(
        pl.len().cast(pl.Float64).alias("messages"),
        pl.col("_is_text").sum().cast(pl.Float64).alias("text_messages"),
        pl.col("word_count").filter(pl.col("_is_text")).sum().cast(pl.Float64).alias("words"),
        pl.col("char_count").filter(pl.col("_is_text")).sum().cast(pl.Float64).alias("chars"),
        pl.col("_n_emojis").sum().cast(pl.Float64).alias("emojis"),
        (pl.col("hour") < 5).sum().cast(pl.Float64).alias("_night"),
        ((pl.col("hour") >= 5) & (pl.col("hour") < 8)).sum().cast(pl.Float64).alias("_early"),
        pl.col("session_id").n_unique().cast(pl.Float64).alias("sessions"),
        pl.col("is_session_start").sum().cast(pl.Float64).alias("sessions_started"),
        pl.col("turn_id").n_unique().cast(pl.Float64).alias("turns"),
        pl.col("reply_latency_ms").count().cast(pl.Float64).alias("replies"),
        pl.col("reply_latency_ms").median().alias("reply_median_ms"),
        pl.col("reply_latency_ms").mean().alias("reply_mean_ms"),
        pl.col("_laugh").sum().cast(pl.Float64).alias("laugh_messages"),
        pl.col("share_kind")
        .cast(pl.String)
        .is_in(["reel", "post"])
        .sum()
        .cast(pl.Float64)
        .alias("reels_posts"),
        pl.col("n_audios").cast(pl.Float64).sum().alias("voice_notes"),
        pl.col("n_photos").cast(pl.Float64).sum().alias("photos"),
        pl.col("n_videos").cast(pl.Float64).sum().alias("videos"),
        pl.col("_q").sum().cast(pl.Float64).alias("questions"),
        (pl.col("type") == "call").sum().cast(pl.Float64).alias("calls_started"),
        (pl.col("call_duration_s").cast(pl.Float64).fill_null(0).sum() * 1000).alias("call_ms"),
        (pl.col("type") == "unsent").sum().cast(pl.Float64).alias("unsent"),
        pl.col("local_date").n_unique().cast(pl.Float64).alias("active_days"),
        pl.col("ts").min().alias("first_ts"),
        pl.col("ts").max().alias("last_ts"),
    )

    # Turns: answered = the next turn in the thread carries a reply latency.
    turns = (
        counted.group_by("turn_id")
        .agg(
            pl.col("thread_id").first(),
            pl.col("sender_id").first(),
            pl.col("message_id").min().alias("_first_id"),
            pl.col("ts").max().alias("_last_ts"),
            pl.col("reply_latency_ms").sort_by("message_id").first().alias("_lat"),
        )
        .sort("_first_id")
        .with_columns(
            pl.col("_lat").shift(-1).over("thread_id").is_not_null().alias("_answered"),
            pl.col("sender_id").shift(1).over("thread_id").alias("_prev_sender"),
            pl.col("_last_ts").shift(1).over("thread_id").alias("_prev_last_ts"),
        )
    )
    unanswered = turns.group_by("sender_id").agg(
        (~pl.col("_answered")).sum().cast(pl.Float64).alias("unanswered_turns")
    )

    # Who makes whom laugh: laughter credited to the previous turn's sender within 2 min.
    caused = (
        counted.filter(pl.col("_laugh"))
        .select("turn_id", "ts")
        .join(turns.select("turn_id", "_prev_sender", "_prev_last_ts"), on="turn_id")
        .filter(
            pl.col("_prev_sender").is_not_null()
            & ((pl.col("ts") - pl.col("_prev_last_ts")).dt.total_milliseconds() <= LAUGH_CREDIT_MS)
        )
        .group_by(pl.col("_prev_sender").alias("sender_id"))
        .agg(pl.len().cast(pl.Float64).alias("laughs_caused"))
    )

    likes = (
        messages.filter(pl.col("type") == "like")
        .group_by("sender_id")
        .agg(pl.len().cast(pl.Float64).alias("likes"))
        .collect()
    )

    frames = [unanswered, caused, likes]
    if reactions is not None:
        rx = reactions.select("message_id", "actor_id", "target_id", "emoji").collect()
        given = rx.group_by(pl.col("actor_id").alias("sender_id")).agg(
            pl.len().cast(pl.Float64).alias("reactions_given"),
            pl.col("emoji").is_in(HEART_EMOJIS).sum().cast(pl.Float64).alias("_heart_rx"),
        )
        received = (
            rx.filter(pl.col("actor_id") != pl.col("target_id"))
            .join(counted.select("message_id"), on="message_id")
            .group_by(pl.col("target_id").alias("sender_id"))
            .agg(pl.len().cast(pl.Float64).alias("reactions_received"))
        )
        frames += [given, received]

    if with_mattr:
        tok = (
            counted.filter(pl.col("_is_text"))
            .sort("message_id")
            .select("sender_id", tokens_expr(pl.col("text")).alias("_tok"))
            .explode("_tok")
            .drop_nulls("_tok")
            .group_by("sender_id", maintain_order=True)
            .agg(pl.col("_tok"))
        )
        rows = [
            (sid, float(len(t)), mattr(t))
            for sid, t in zip(tok["sender_id"].to_list(), tok["_tok"].to_list(), strict=True)
        ]
        frames.append(
            pl.DataFrame(
                rows,
                schema={"sender_id": pl.UInt32(), "tokens": pl.Float64(), "mattr": pl.Float64()},
                orient="row",
            )
        )

    out = base
    for fr in frames:
        out = out.join(
            fr.with_columns(pl.col("sender_id").cast(pl.UInt32)), on="sender_id", how="left"
        )
    for col in METRIC_COLUMNS:
        if col not in out.columns:
            out = out.with_columns(pl.lit(None, dtype=pl.Float64).alias(col))
    zero_fill = [
        "unanswered_turns",
        "laughs_caused",
        "likes",
        "reactions_given",
        "reactions_received",
        "_heart_rx",
        "tokens",
    ]
    out = out.with_columns(
        [pl.col(c).fill_null(0.0) for c in zero_fill if c in out.columns]
    ).with_columns(
        (pl.col("_heart_rx") if "_heart_rx" in out.columns else pl.lit(0.0)).alias("hearts_given")
    )
    out = out.with_columns(
        (pl.col("hearts_given") + pl.col("likes")).alias("hearts_given"),
        _safe_div("words", "text_messages").alias("avg_words"),
        _safe_div("emojis", "messages").alias("emojis_per_message"),
        _safe_div("_night", "messages").alias("night_share"),
        _safe_div("_early", "messages").alias("early_share"),
        _safe_div("sessions_started", "sessions").alias("initiation_rate"),
        _safe_div("messages", "turns").alias("msgs_per_turn"),
        (1 - _safe_div("unanswered_turns", "turns")).alias("reply_rate"),
        _safe_div("laugh_messages", "messages").alias("laugh_rate"),
        _safe_div("reactions_received", "messages").alias("reactions_per_message"),
        _safe_div("questions", "text_messages").alias("question_rate"),
    )
    if people is not None:
        out = out.filter(pl.col("sender_id").is_in(list(people)))
    return out.select(METRIC_COLUMNS).sort(["messages", "sender_id"], descending=[True, False])


def metrics_by_person(df: pl.DataFrame) -> dict[int, dict[str, object]]:
    """``person_id -> {column: value}`` view of a :func:`person_metrics` table."""
    return {int(r["sender_id"]): r for r in df.iter_rows(named=True)}


# --------------------------------------------------------------------------------------
# Scope summaries (compare / people pages)
# --------------------------------------------------------------------------------------

SUMMARY_KEYS: tuple[str, ...] = (
    "messages",
    "words",
    "avg_words",
    "emojis_per_message",
    "emoji_message_share",
    "active_days",
    "messages_per_active_day",
    "reply_median_ms",
    "night_share",
    "early_share",
    "weekend_share",
    "question_rate",
    "laugh_rate",
    "reactions_received",
    "reactions_per_message",
    "media",
    "reels_posts",
    "voice_notes",
    "calls",
    "call_ms",
    "first_ts",
    "last_ts",
)


def scope_summary(
    messages: pl.LazyFrame | pl.DataFrame,
    reactions: pl.LazyFrame | pl.DataFrame | None = None,
    *,
    laughter_patterns: Iterable[str] | None = None,
) -> dict[str, object]:
    """Summary metrics of ONE message set (a chat, or one person's messages).

    Same definitions as :func:`person_metrics`; ``reply_median_ms`` is over the
    ``reply_latency_ms`` of the messages in the set, ``reactions_received`` counts
    non-self reactions on them. Keys: :data:`SUMMARY_KEYS`.
    """
    counted = (
        messages.lazy()
        .filter(is_counted_message())
        .with_columns(is_text_message().alias("_is_text"))
        .with_columns((pl.col("_is_text") & laughter_expr(laughter_patterns)).alias("_laugh"))
        .collect()
    )
    out: dict[str, object] = dict.fromkeys(SUMMARY_KEYS)
    n = counted.height
    out["messages"] = n
    if n == 0:
        out.update(messages=0, words=0, active_days=0, reactions_received=0, media=0)
        return out
    text = counted.filter(pl.col("_is_text"))
    words = int(text["word_count"].sum())
    n_emojis = int(counted["emojis"].list.len().sum())
    days = int(counted["local_date"].n_unique())
    lat = counted["reply_latency_ms"].drop_nulls()
    rx_n = 0
    if reactions is not None:
        rx_n = (
            reactions.lazy()
            .filter(pl.col("actor_id") != pl.col("target_id"))
            .join(counted.lazy().select("message_id"), on="message_id")
            .select(pl.len())
            .collect()
            .item()
        )
    out.update(
        words=words,
        avg_words=words / text.height if text.height else None,
        emojis_per_message=n_emojis / n,
        emoji_message_share=float((counted["emojis"].list.len() > 0).sum()) / n,
        active_days=days,
        messages_per_active_day=n / days,
        reply_median_ms=float(lat.median()) if lat.len() else None,  # type: ignore[arg-type]
        night_share=float((counted["hour"] < 5).sum()) / n,
        early_share=float(((counted["hour"] >= 5) & (counted["hour"] < 8)).sum()) / n,
        weekend_share=float((counted["weekday"] >= 6).sum()) / n,
        question_rate=(
            float(text["text"].str.contains("?", literal=True).sum()) / text.height
            if text.height
            else None
        ),
        laugh_rate=float(counted["_laugh"].sum()) / n,
        reactions_received=int(rx_n),
        reactions_per_message=int(rx_n) / n,
        media=int(
            counted.select(
                (pl.col("n_photos").cast(pl.Int64) + pl.col("n_videos").cast(pl.Int64)).sum()
            ).item()
        ),
        reels_posts=int(counted["share_kind"].cast(pl.String).is_in(["reel", "post"]).sum()),
        voice_notes=int(counted["n_audios"].cast(pl.Int64).sum()),
        calls=int((counted["type"] == "call").sum()),
        call_ms=int(counted["call_duration_s"].cast(pl.Int64).fill_null(0).sum()) * 1000,
        first_ts=counted["ts"].min(),
        last_ts=counted["ts"].max(),
    )
    return out


def hour_shares(messages: pl.LazyFrame | pl.DataFrame) -> list[float]:
    """Share of counted messages per local hour 0..23 (zeros when empty)."""
    return _shares(messages, "hour", range(24))


def weekday_shares(messages: pl.LazyFrame | pl.DataFrame) -> list[float]:
    """Share of counted messages per ISO weekday Mon..Sun."""
    return _shares(messages, "weekday", range(1, 8))


def _shares(messages: pl.LazyFrame | pl.DataFrame, col: str, keys: range) -> list[float]:
    df = (
        messages.lazy()
        .filter(is_counted_message())
        .group_by(col)
        .agg(pl.len().alias("n"))
        .collect()
    )
    counts = {int(k): int(v) for k, v in df.iter_rows()}
    total = sum(counts.values())
    return [counts.get(k, 0) / total if total else 0.0 for k in keys]


def monthly_profile(messages: pl.LazyFrame | pl.DataFrame) -> pl.DataFrame:
    """Per local month (``month`` = first day): ``messages``, ``per_day`` (messages /
    days in month), ``emoji_rate`` (emojis / messages), ``reply_median_ms``."""
    return (
        messages.lazy()
        .filter(is_counted_message())
        .with_columns(pl.col("local_date").dt.truncate("1mo").alias("month"))
        .group_by("month")
        .agg(
            pl.len().alias("messages"),
            pl.col("emojis").list.len().sum().alias("_emojis"),
            pl.col("reply_latency_ms").median().alias("reply_median_ms"),
        )
        .with_columns(
            (pl.col("messages") / pl.col("month").dt.month_end().dt.day()).alias("per_day"),
            (pl.col("_emojis") / pl.col("messages")).alias("emoji_rate"),
        )
        .drop("_emojis")
        .sort("month")
        .collect()
    )
