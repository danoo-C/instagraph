"""``GET /api/overview/reply-times`` — my median reply time over time (spec §7.1 #15).

Series ``me`` = median latency of the owner's replies; ``others`` = median latency of
other people's replies to the owner (the previous counted message in the thread was the
owner's). Latencies are the import-time ``reply_latency_ms`` values. The people filter
narrows whose replies are shown (``me`` needs the owner in the list); the conversation
context (who replied to whom) always uses everyone.
"""

from datetime import date
from typing import Annotated

import polars as pl
from fastapi import APIRouter, Query

from instagraph.analysis import replies as rp
from instagraph.api.deps import AnalysisContext, Context
from instagraph.api.filters import bucket_labels, bucket_range, bucket_start, filtered_messages
from instagraph.errors import ERROR_RESPONSES
from instagraph.schemas.common import Bucket, MessageQuery, Series, Tile, TimeSeries
from instagraph.schemas.dynamics import ReplyTimesOverviewResponse

router = APIRouter(prefix="/api/overview", tags=["overview"], responses=ERROR_RESPONSES)


def _median(df: pl.DataFrame) -> float | None:
    if df.is_empty():
        return None
    v = df["reply_latency_ms"].median()
    return None if v is None else float(v)  # type: ignore[arg-type]


def compute_reply_times(ctx: AnalysisContext, bucket: Bucket) -> ReplyTimesOverviewResponse:
    msgs = rp.with_prev_sender(
        filtered_messages(ctx.dataset, ctx.filters.model_copy(update={"people": None}))
    ).collect()
    owner = ctx.owner_id
    wanted = ctx.filters.people
    replies = msgs.filter(pl.col("reply_latency_ms").is_not_null())
    if wanted is not None:
        replies = replies.filter(pl.col("sender_id").is_in(list(wanted)))
    if owner is None:
        mine = others = replies.head(0)
    else:
        mine = replies.filter(pl.col("sender_id") == owner)
        others = replies.filter(
            (pl.col("sender_id") != owner) & (pl.col("prev_sender_id") == owner)
        )

    x: list[date] = []
    dates = msgs["local_date"].drop_nulls()
    if not dates.is_empty():
        lo, hi = dates.min(), dates.max()
        assert isinstance(lo, date) and isinstance(hi, date)
        x = bucket_range(lo, hi, bucket)
    pos = {d: i for i, d in enumerate(x)}

    def series(df: pl.DataFrame, key: str, name: str, color: int | None) -> Series:
        values: list[float | None] = [None] * len(x)
        if not df.is_empty():
            grouped = (
                df.with_columns(bucket_start(pl.col("local_date"), bucket).alias("_b"))
                .group_by("_b")
                .agg(pl.col("reply_latency_ms").median().alias("_v"))
            )
            for b, v in grouped.iter_rows():
                if b in pos and v is not None:
                    values[pos[b]] = float(v)
        return Series(key=key, name=name, person_id=None, color_index=color, values=values)

    owner_ref = None if owner is None else ctx.person_ref(owner)
    my_median = _median(mine)
    their_median = _median(others)
    return ReplyTimesOverviewResponse(
        dataset_version=ctx.dataset_version,
        owner=owner_ref,
        tiles=[
            Tile(
                key="my_median_reply",
                label="My median reply time",
                value=my_median,
                unit="ms",
                detail=f"{mine.height} replies",
                person=owner_ref,
                drilldown=None if owner is None else MessageQuery(sender_id=owner),
            ),
            Tile(
                key="others_median_reply",
                label="Others' median reply time to me",
                value=their_median,
                unit="ms",
                detail=f"{others.height} replies",
            ),
        ],
        over_time=TimeSeries(
            bucket=bucket,
            x=bucket_labels(x),
            series=[
                series(mine, "me", owner_ref.name if owner_ref else "Me", 0),
                series(others, "others", "Others", None),
            ],
            unit="ms",
        ),
    )


@router.get("/reply-times", response_model=ReplyTimesOverviewResponse)
def overview_reply_times(
    ctx: Context,
    bucket: Annotated[Bucket, Query(description="Time bucket")] = "month",
) -> ReplyTimesOverviewResponse:
    """My median reply time (and others' reply time to me) per time bucket."""
    return ctx.cached(
        "overview_reply_times", lambda: compute_reply_times(ctx, bucket), extra=bucket
    )
