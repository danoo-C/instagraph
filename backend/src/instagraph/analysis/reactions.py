"""Reactions (spec §6.7).

Inputs are reactions frames as returned by
:func:`instagraph.api.filters.filtered_reactions` (columns ``message_id, actor_id,
emoji, ts, thread_id, target_id, message_ts, local_*``). Outputs are eager DataFrames.
Apply the emoji merge settings first with :func:`normalize_reactions`.

Reusable API
------------
* ``normalize_reactions(reactions, merge_skin_tones=True, merge_gendered=False)
  -> LazyFrame`` — ``emoji`` normalized with ``textproc.normalize_emoji_expr``.
* ``reactions_given_received(reactions) -> DataFrame[person_id, given, received,
  self_reactions]`` (a self-reaction counts as given AND received).
* ``top_reaction_emojis(reactions, by="actor_id"|"target_id"|None, limit=10)
  -> DataFrame[person_id, emoji, count]`` (``person_id`` null when ``by`` is None).
* ``reaction_matrix(reactions) -> DataFrame[actor_id, target_id, count]``.
* ``reciprocity(reactions) -> DataFrame[a_id, b_id, a_to_b, b_to_a, reciprocity]`` —
  one row per unordered pair with any reaction (``a_id < b_id``), self excluded;
  ``reciprocity = min / max``.
* ``most_reacted(reactions, limit=20) -> DataFrame[message_id, count]``.
* ``hearts(reactions, msgs) -> DataFrame[person_id, heart_reactions_given,
  likes_given, hearts_given, heart_reactions_received, likes_received,
  hearts_received]`` — "hearts" = ❤ reactions + ``like`` messages. A like's
  receiver is the sender of the previous non-like message in the thread.
"""

from typing import Literal

import polars as pl

from instagraph.textproc import normalize_emoji_expr

Frame = pl.LazyFrame | pl.DataFrame

HEART = "❤"


def normalize_reactions(
    reactions: Frame, *, merge_skin_tones: bool = True, merge_gendered: bool = False
) -> pl.LazyFrame:
    return reactions.lazy().with_columns(
        normalize_emoji_expr(
            pl.col("emoji"), merge_skin_tones=merge_skin_tones, merge_gendered=merge_gendered
        ).alias("emoji")
    )


def reactions_given_received(reactions: Frame) -> pl.DataFrame:
    """``[person_id, given, received, self_reactions]`` sorted by person_id."""
    lf = reactions.lazy()
    given = lf.group_by(pl.col("actor_id").alias("person_id")).agg(
        pl.len().cast(pl.Int64).alias("given"),
        (pl.col("actor_id") == pl.col("target_id")).sum().cast(pl.Int64).alias("self_reactions"),
    )
    received = lf.group_by(pl.col("target_id").alias("person_id")).agg(
        pl.len().cast(pl.Int64).alias("received")
    )
    return (
        given.join(received, on="person_id", how="full", coalesce=True)
        .with_columns(pl.col("given", "received", "self_reactions").fill_null(0))
        .select("person_id", "given", "received", "self_reactions")
        .sort("person_id")
        .collect()
    )


def top_reaction_emojis(
    reactions: Frame,
    by: Literal["actor_id", "target_id"] | None = None,
    limit: int = 10,
) -> pl.DataFrame:
    """Top ``limit`` emojis per person (or overall when ``by`` is None)."""
    lf = reactions.lazy()
    keys = [by, "emoji"] if by else ["emoji"]
    counts = lf.group_by(keys).agg(pl.len().cast(pl.Int64).alias("count"))
    if by:
        counts = counts.rename({by: "person_id"})
    else:
        counts = counts.with_columns(pl.lit(None, pl.UInt32).alias("person_id"))
    return (
        counts.sort(["count", "emoji"], descending=[True, False])
        .with_columns(pl.int_range(pl.len()).over("person_id").alias("_rank"))
        .filter(pl.col("_rank") < limit)
        .select("person_id", "emoji", "count")
        .sort(["person_id", "count", "emoji"], descending=[False, True, False], nulls_last=True)
        .collect()
    )


def reaction_matrix(reactions: Frame) -> pl.DataFrame:
    return (
        reactions.lazy()
        .group_by("actor_id", "target_id")
        .agg(pl.len().cast(pl.Int64).alias("count"))
        .sort("actor_id", "target_id")
        .collect()
    )


def reciprocity(reactions: Frame) -> pl.DataFrame:
    """Per unordered pair: reactions each way and min/max reciprocity."""
    m = reaction_matrix(reactions).filter(pl.col("actor_id") != pl.col("target_id"))
    pairs = m.select(
        pl.min_horizontal("actor_id", "target_id").alias("a_id"),
        pl.max_horizontal("actor_id", "target_id").alias("b_id"),
        pl.when(pl.col("actor_id") < pl.col("target_id"))
        .then(pl.col("count"))
        .otherwise(0)
        .alias("a_to_b"),
        pl.when(pl.col("actor_id") > pl.col("target_id"))
        .then(pl.col("count"))
        .otherwise(0)
        .alias("b_to_a"),
    )
    return (
        pairs.group_by("a_id", "b_id")
        .agg(pl.col("a_to_b").sum(), pl.col("b_to_a").sum())
        .with_columns(
            (pl.min_horizontal("a_to_b", "b_to_a") / pl.max_horizontal("a_to_b", "b_to_a")).alias(
                "reciprocity"
            )
        )
        .sort("a_id", "b_id")
    )


def most_reacted(reactions: Frame, limit: int = 20) -> pl.DataFrame:
    return (
        reactions.lazy()
        .group_by("message_id")
        .agg(pl.len().cast(pl.Int64).alias("count"))
        .sort(["count", "message_id"], descending=[True, False])
        .head(limit)
        .collect()
    )


def likes(msgs: Frame) -> pl.DataFrame:
    """``like`` messages with ``liker_id`` and ``liked_id`` (previous non-like sender)."""
    return (
        msgs.lazy()
        .filter(pl.col("type") != "system")
        .sort("message_id")
        .with_columns(
            pl.when(pl.col("type") != "like")
            .then(pl.col("sender_id"))
            .otherwise(None)
            .forward_fill()
            .shift(1)
            .over("thread_id")
            .alias("liked_id")
        )
        .filter(pl.col("type") == "like")
        .select("message_id", "thread_id", pl.col("sender_id").alias("liker_id"), "liked_id")
        .collect()
    )


def hearts(reactions: Frame, msgs: Frame) -> pl.DataFrame:
    """Hearts per person (see module docstring). ``reactions`` must be normalized."""
    heart = reactions.lazy().filter(pl.col("emoji") == HEART).collect()
    lk = likes(msgs)
    parts = [
        heart.group_by(pl.col("actor_id").alias("person_id")).agg(
            pl.len().cast(pl.Int64).alias("heart_reactions_given")
        ),
        heart.group_by(pl.col("target_id").alias("person_id")).agg(
            pl.len().cast(pl.Int64).alias("heart_reactions_received")
        ),
        lk.group_by(pl.col("liker_id").alias("person_id")).agg(
            pl.len().cast(pl.Int64).alias("likes_given")
        ),
        lk.filter(pl.col("liked_id").is_not_null())
        .group_by(pl.col("liked_id").alias("person_id"))
        .agg(pl.len().cast(pl.Int64).alias("likes_received")),
    ]
    out = parts[0].with_columns(pl.col("person_id").cast(pl.UInt32))
    for p in parts[1:]:
        out = out.join(
            p.with_columns(pl.col("person_id").cast(pl.UInt32)),
            on="person_id",
            how="full",
            coalesce=True,
        )
    cols = ["heart_reactions_given", "likes_given", "heart_reactions_received", "likes_received"]
    return (
        out.with_columns(pl.col(cols).fill_null(0))
        .with_columns(
            (pl.col("heart_reactions_given") + pl.col("likes_given")).alias("hearts_given"),
            (pl.col("heart_reactions_received") + pl.col("likes_received")).alias(
                "hearts_received"
            ),
        )
        .select(
            "person_id",
            "heart_reactions_given",
            "likes_given",
            "hearts_given",
            "heart_reactions_received",
            "likes_received",
            "hearts_received",
        )
        .sort("person_id")
    )
