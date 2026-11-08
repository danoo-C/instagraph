"""Basic counts (spec §6.1, §6.8 counts part).

All functions take a filtered message LazyFrame (see
:func:`instagraph.api.filters.filtered_messages`). Functions documented as working on
*counted* messages expect the caller to have applied ``is_counted_message()`` already
(so the invariant "overview total == Σ chats == Σ people" holds by construction).
"""

import itertools
from collections.abc import Sequence
from dataclasses import dataclass

import polars as pl

from instagraph.api.filters import is_text_message
from instagraph.textproc import normalize_emoji_expr

#: Message types shown in type-mix charts (counted types only, §6.1).
MIX_TYPES: tuple[str, ...] = (
    "text",
    "link",
    "photo",
    "video",
    "audio",
    "gif",
    "sticker",
    "share",
    "call",
    "unsent",
    "unknown",
)

#: Columns produced by :func:`count_aggs` (all integers).
COUNT_COLUMNS: tuple[str, ...] = (
    "messages",
    "text_messages",
    "words",
    "characters",
    "emojis",
    "photos",
    "videos",
    "voice_notes",
    "gifs",
    "stickers",
    "reels",
    "posts",
    "shares",
    "links",
    "calls",
    "call_duration_s",
    "unsent",
)


def count_aggs() -> list[pl.Expr]:
    """Aggregations for :data:`COUNT_COLUMNS` over counted messages."""
    text = is_text_message()
    return [
        pl.len().cast(pl.Int64).alias("messages"),
        text.sum().cast(pl.Int64).alias("text_messages"),
        pl.col("word_count").filter(text).sum().cast(pl.Int64).alias("words"),
        pl.col("char_count").filter(text).sum().cast(pl.Int64).alias("characters"),
        pl.col("emojis").list.len().sum().cast(pl.Int64).alias("emojis"),
        pl.col("n_photos").sum().cast(pl.Int64).alias("photos"),
        pl.col("n_videos").sum().cast(pl.Int64).alias("videos"),
        pl.col("n_audios").sum().cast(pl.Int64).alias("voice_notes"),
        pl.col("n_gifs").sum().cast(pl.Int64).alias("gifs"),
        pl.col("n_stickers").sum().cast(pl.Int64).alias("stickers"),
        (pl.col("share_kind") == "reel").sum().cast(pl.Int64).alias("reels"),
        (pl.col("share_kind") == "post").sum().cast(pl.Int64).alias("posts"),
        (pl.col("type") == "share").sum().cast(pl.Int64).alias("shares"),
        pl.col("links").list.len().sum().cast(pl.Int64).alias("links"),
        (pl.col("type") == "call").sum().cast(pl.Int64).alias("calls"),
        pl.col("call_duration_s")
        .filter(pl.col("type") == "call")
        .sum()
        .cast(pl.Int64)
        .alias("call_duration_s"),
        (pl.col("type") == "unsent").sum().cast(pl.Int64).alias("unsent"),
    ]


def totals(counted: pl.LazyFrame) -> dict[str, int]:
    """:data:`COUNT_COLUMNS` over all counted messages."""
    df = counted.select(count_aggs()).collect()
    row = df.row(0, named=True) if df.height else {}
    return {k: int(row.get(k) or 0) for k in COUNT_COLUMNS}


def counts_by(counted: pl.LazyFrame, by: str | Sequence[str]) -> pl.DataFrame:
    """:data:`COUNT_COLUMNS` per group (e.g. ``"sender_id"``, ``"thread_id"``)."""
    keys = [by] if isinstance(by, str) else list(by)
    return counted.group_by(keys).agg(count_aggs()).collect()


def share(part: float, total: float) -> float:
    """``part / total`` as a 0..1 ratio (0 when ``total`` is 0)."""
    return part / total if total else 0.0


def safe_div(a: float, b: float) -> float | None:
    return a / b if b else None


def type_counts(counted: pl.LazyFrame) -> dict[str, int]:
    """Counted messages per main ``type`` (every :data:`MIX_TYPES` key present)."""
    df = counted.group_by(pl.col("type").cast(pl.String)).agg(pl.len().alias("n")).collect()
    out = dict.fromkeys(MIX_TYPES, 0)
    for t, n in df.iter_rows():
        out[t] = out.get(t, 0) + int(n)
    return out


def emoji_counts(
    lf: pl.LazyFrame,
    *,
    by: str | None = None,
    merge_skin_tones: bool = True,
    merge_gendered: bool = False,
) -> pl.DataFrame:
    """``[by,] emoji, n`` sorted by ``n`` desc then emoji (merge settings applied)."""
    keys = ["emoji"] if by is None else [by, "emoji"]
    cols = ["emojis"] if by is None else [by, "emojis"]
    return (
        lf.select(cols)
        .explode("emojis")
        .drop_nulls("emojis")
        .with_columns(
            normalize_emoji_expr(
                pl.col("emojis"),
                merge_skin_tones=merge_skin_tones,
                merge_gendered=merge_gendered,
            ).alias("emoji")
        )
        .group_by(keys)
        .agg(pl.len().alias("n"))
        .sort(
            [*([by] if by else []), "n", "emoji"],
            descending=[*([False] if by else []), True, False],
        )
        .collect()
    )


@dataclass(frozen=True)
class SizeBin:
    """Chats with ``lo <= messages <= hi``."""

    lo: int
    hi: int
    count: int
    thread_ids: list[int]


def log_bin_edges(max_value: int) -> list[int]:
    """1-2-5 log-scale lower edges ``[1, 2, 5, 10, 20, 50, …]`` covering ``max_value``."""
    edges: list[int] = []
    exp = 0
    while True:
        for m in (1, 2, 5):
            e = m * 10**exp
            edges.append(e)
            if e > max_value:
                return edges
        exp += 1


def size_distribution(sizes: dict[int, int]) -> list[SizeBin]:
    """Histogram of chats by message count with log (1-2-5) bins.

    ``sizes`` maps ``thread_id -> messages`` (chats with 0 messages are ignored).
    Trailing empty bins are dropped; inner empty bins are kept (contiguous axis).
    """
    positive = {t: n for t, n in sizes.items() if n > 0}
    if not positive:
        return []
    edges = log_bin_edges(max(positive.values()))
    bins: list[SizeBin] = []
    for lo, nxt in itertools.pairwise(edges):
        ids = sorted(t for t, n in positive.items() if lo <= n < nxt)
        bins.append(SizeBin(lo=lo, hi=nxt - 1, count=len(ids), thread_ids=ids))
    while bins and bins[-1].count == 0:
        bins.pop()
    return bins
