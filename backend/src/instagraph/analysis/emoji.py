"""Emoji analysis (spec §6.6, chart catalogue §7.1 #13 and §7.2 "Emojis tab").

Emojis come from the derived ``messages.emojis`` column (``emoji.emoji_list`` grapheme
clusters: ZWJ sequences, flags and keycaps are ONE emoji; stored with U+FE0F removed,
skin tones kept). Skin-tone / gender merging is applied at query time
(:func:`emoji_long`, honoring ``AppSettings.emoji_merge_skin_tones`` /
``emoji_merge_gendered``).

Denominators: "per message" and "% messages with emoji" use **text messages**
(:func:`~instagraph.api.filters.is_text_message`), since emojis only occur in text.

Reusable API (for awards / wrapped / compare)
----------------------------------------------
* :func:`emoji_per_message(msgs, ...) -> dict[int, float]`
* :func:`top_emojis(msgs, n=20, ...) -> list[tuple[str, int]]`
* :func:`emoji_totals(msgs, ...) -> dict[int, EmojiTotals]`
* :func:`emoji_long` (one row per emoji occurrence), :func:`signature_emojis`,
  :func:`longest_chains`, :func:`emoticon_counts`, :func:`category_counts`.
"""

from collections.abc import Iterable
from dataclasses import dataclass

import emoji as emoji_lib
import polars as pl

from instagraph.analysis.text import Frame, distinctive_terms, text_messages
from instagraph.resources import emoji_category, heart_emojis
from instagraph.textproc import extract_emoticons, normalize_emoji, normalize_emoji_expr

#: Rust-regex pre-filter for messages that may contain a text emoticon (superset of
#: ``textproc.EMOTICON_RE``); exact extraction runs in Python on candidates only.
EMOTICON_PREFILTER = r"[:;]-?[)(DPpOo*/3]|:'\(|</?3|\^_?\^|-_-|[xX]D"


def emoji_long(
    msgs: Frame,
    *,
    merge_skin_tones: bool = True,
    merge_gendered: bool = False,
    extra_cols: Iterable[str] = (),
) -> pl.LazyFrame:
    """One row per emoji occurrence in text messages: ``message_id, sender_id,
    emoji`` (+ ``extra_cols``), emoji normalized per the merge settings."""
    return (
        text_messages(msgs)
        .select("message_id", "sender_id", *extra_cols, "emojis")
        .explode("emojis")
        .drop_nulls("emojis")
        .select(
            "message_id",
            "sender_id",
            *extra_cols,
            normalize_emoji_expr(
                pl.col("emojis"), merge_skin_tones=merge_skin_tones, merge_gendered=merge_gendered
            ).alias("emoji"),
        )
    )


@dataclass
class EmojiTotals:
    """Per-person emoji totals (text-message denominators)."""

    text_messages: int
    total: int
    messages_with_emoji: int
    unique: int
    emoji_only_messages: int

    @property
    def per_message(self) -> float:
        return self.total / self.text_messages if self.text_messages else 0.0

    @property
    def with_emoji_rate(self) -> float:
        return self.messages_with_emoji / self.text_messages if self.text_messages else 0.0


def emoji_only_expr() -> pl.Expr:
    """True for text messages consisting only of emojis (and whitespace).

    Vectorized: every emoji is one grapheme, so the message is emoji-only when its
    grapheme count minus inner whitespace equals the number of emojis.
    """
    n = pl.col("emojis").list.len()
    ws = pl.col("text").str.strip_chars().str.count_matches(r"\s")
    return (n > 0) & ((pl.col("char_count").cast(pl.Int64) - ws.cast(pl.Int64)) == n.cast(pl.Int64))


def emoji_totals(
    msgs: Frame, *, merge_skin_tones: bool = True, merge_gendered: bool = False
) -> dict[int, EmojiTotals]:
    """:class:`EmojiTotals` per sender."""
    lf = text_messages(msgs)
    base = (
        lf.group_by("sender_id")
        .agg(
            pl.len().alias("tm"),
            pl.col("emojis").list.len().sum().alias("total"),
            (pl.col("emojis").list.len() > 0).sum().alias("with"),
            emoji_only_expr().sum().alias("only"),
        )
        .collect()
    )
    uniq = dict(
        emoji_long(lf, merge_skin_tones=merge_skin_tones, merge_gendered=merge_gendered)
        .group_by("sender_id")
        .agg(pl.col("emoji").n_unique())
        .collect()
        .iter_rows()
    )
    return {
        int(pid): EmojiTotals(
            text_messages=int(tm),
            total=int(total),
            messages_with_emoji=int(w),
            unique=int(uniq.get(pid, 0)),
            emoji_only_messages=int(only),
        )
        for pid, tm, total, w, only in base.iter_rows()
    }


def emoji_per_message(msgs: Frame) -> dict[int, float]:
    """Emojis per text message, per sender."""
    return {pid: t.per_message for pid, t in emoji_totals(msgs).items()}


def emoji_counts(long: pl.DataFrame, group_col: str | None = "sender_id") -> pl.DataFrame:
    """``[group], key (emoji), n`` counts from an :func:`emoji_long` frame."""
    by = [group_col, "emoji"] if group_col else ["emoji"]
    return long.group_by(by).agg(pl.len().alias("n").cast(pl.Int64)).rename({"emoji": "key"})


def top_from_counts(
    counts: pl.DataFrame, n: int, group_col: str | None = "sender_id"
) -> dict[int | None, list[tuple[str, int]]]:
    """Top ``n`` emojis per group (``None`` when ``group_col`` is None); ties by emoji."""
    df = counts.sort(["n", "key"], descending=[True, False])
    if group_col is None:
        return {None: [(str(k), int(c)) for k, c in df.head(n).select("key", "n").iter_rows()]}
    out: dict[int | None, list[tuple[str, int]]] = {}
    for g, k, c in (
        df.group_by(group_col, maintain_order=True)
        .head(n)
        .select(group_col, "key", "n")
        .iter_rows()
    ):
        out.setdefault(int(g), []).append((str(k), int(c)))
    return out


def top_emojis(
    msgs: Frame, n: int = 20, *, merge_skin_tones: bool = True, merge_gendered: bool = False
) -> list[tuple[str, int]]:
    """Top ``n`` emojis over all of ``msgs`` as ``(emoji, count)``."""
    long = emoji_long(
        msgs, merge_skin_tones=merge_skin_tones, merge_gendered=merge_gendered
    ).collect()
    return top_from_counts(emoji_counts(long, None), n, None)[None]


def signature_emojis(counts: pl.DataFrame, min_count: int = 2) -> dict[int, tuple[str, int, float]]:
    """Signature emoji per sender = highest weighted log-odds vs everyone else
    (same estimator as distinctive words). Needs ≥ 2 senders with emojis."""
    if counts.is_empty() or counts["sender_id"].n_unique() < 2:
        return {}
    method, top = distinctive_terms(counts, 1, min_count=min_count)
    if method != "log_odds":
        return {}
    return {g: lst[0] for g, lst in top.items() if lst}


# --------------------------------------------------------------------------------------
# Longest chains
# --------------------------------------------------------------------------------------


def _chain(text: str, *, merge_skin_tones: bool, merge_gendered: bool) -> tuple[int, str]:
    """Longest run of directly adjacent emojis in ``text``: ``(length, emojis)``."""
    best: list[str] = []
    cur: list[str] = []
    prev_end = -1
    for item in emoji_lib.emoji_list(text):
        e = normalize_emoji(
            item["emoji"], merge_skin_tones=merge_skin_tones, merge_gendered=merge_gendered
        )
        if item["match_start"] == prev_end:
            cur.append(e)
        else:
            cur = [e]
        prev_end = item["match_end"]
        if len(cur) > len(best):
            best = list(cur)
    return len(best), "".join(best)


@dataclass
class EmojiChain:
    length: int
    emojis: str
    message_id: int
    sender_id: int


def longest_chains(
    msgs: Frame, *, merge_skin_tones: bool = True, merge_gendered: bool = False
) -> dict[int, EmojiChain]:
    """Longest chain of directly adjacent emojis per sender (≥ 2 emojis).

    Python runs only on the few candidates: messages are visited in descending emoji
    count and the scan stops once a message can't beat the current best.
    """
    cand = (
        text_messages(msgs)
        .select("message_id", "sender_id", "text", pl.col("emojis").list.len().alias("n"))
        .filter(pl.col("n") >= 2)
        .sort(["n", "message_id"], descending=[True, False])
        .collect()
    )
    out: dict[int, EmojiChain] = {}
    for part in cand.partition_by("sender_id", maintain_order=True):
        best: EmojiChain | None = None
        for mid, sid, text, n in part.iter_rows():
            if best is not None and n <= best.length:
                break
            length, chain = _chain(
                text, merge_skin_tones=merge_skin_tones, merge_gendered=merge_gendered
            )
            if length >= 2 and (best is None or length > best.length):
                best = EmojiChain(length, chain, int(mid), int(sid))
        if best is not None:
            out[best.sender_id] = best
    return out


# --------------------------------------------------------------------------------------
# Emoticons, categories, hearts
# --------------------------------------------------------------------------------------


def emoticon_counts(msgs: Frame) -> pl.DataFrame:
    """Text emoticons (``:)``, ``<3``, ``xD``…): ``sender_id, emoticon, n``.

    A Rust-regex pre-filter selects candidate messages; ``textproc.extract_emoticons``
    runs on those only.
    """
    cand = (
        text_messages(msgs)
        .select("sender_id", "text")
        .filter(pl.col("text").str.contains(EMOTICON_PREFILTER))
        .collect()
    )
    if cand.is_empty():
        return pl.DataFrame(schema={"sender_id": pl.UInt32, "emoticon": pl.String, "n": pl.Int64})
    found = cand.with_columns(
        pl.col("text")
        .map_elements(extract_emoticons, return_dtype=pl.List(pl.String))
        .alias("emoticon")
    )
    return (
        found.select("sender_id", "emoticon")
        .explode("emoticon")
        .drop_nulls("emoticon")
        .group_by("sender_id", "emoticon")
        .agg(pl.len().alias("n").cast(pl.Int64))
        .sort(["n", "emoticon"], descending=[True, False])
    )


def with_category(long: pl.DataFrame) -> pl.DataFrame:
    """Add a ``category`` column (see ``resources.EMOJI_CATEGORIES``)."""
    if long.is_empty():
        return long.with_columns(pl.lit(None, pl.String).alias("category"))
    uniq = long["emoji"].unique().to_list()
    mapping = {e: emoji_category(e) for e in uniq}
    return long.with_columns(
        pl.col("emoji")
        .replace_strict(mapping, default="symbols", return_dtype=pl.String)
        .alias("category")
    )


def category_counts(long: pl.DataFrame) -> pl.DataFrame:
    """``sender_id, category, n`` from an :func:`emoji_long` frame."""
    return with_category(long).group_by("sender_id", "category").agg(pl.len().alias("n"))


def is_heart_expr() -> pl.Expr:
    """True for heart emojis (skin tones ignored)."""
    base = normalize_emoji_expr(pl.col("emoji"), merge_skin_tones=True)
    return base.is_in(list(heart_emojis()))
