"""``GET /api/overview/emojis`` — top emojis overall and mine (spec §7.1 chart 13)."""

import polars as pl
from fastapi import APIRouter

from instagraph.analysis import emoji as E
from instagraph.api.deps import AnalysisContext, Context
from instagraph.api.filters import is_counted_message
from instagraph.errors import ERROR_RESPONSES
from instagraph.resources import emoji_category
from instagraph.schemas.common import MessageQuery
from instagraph.schemas.emojis import EmojiCount, OverviewEmojisResponse

router = APIRouter(prefix="/api/overview", tags=["overview"], responses=ERROR_RESPONSES)

TOP_N = 20


def _counts(long: pl.DataFrame, q: MessageQuery) -> list[EmojiCount]:
    top = E.top_from_counts(E.emoji_counts(long, None), TOP_N, None)[None]
    return [
        EmojiCount(
            emoji=e,
            count=c,
            category=emoji_category(e),
            drilldown=q.model_copy(update={"emoji": e}),
        )
        for e, c in top
    ]


def _build(ctx: AnalysisContext) -> OverviewEmojisResponse:
    s = ctx.settings
    long = E.emoji_long(
        ctx.messages().filter(is_counted_message()),
        merge_skin_tones=s.emoji_merge_skin_tones,
        merge_gendered=s.emoji_merge_gendered,
    ).collect()
    owner = ctx.owner_id
    mine = long.filter(pl.col("sender_id") == owner) if owner is not None else long.clear()
    others = long.filter(pl.col("sender_id") != owner) if owner is not None else long
    return OverviewEmojisResponse(
        dataset_version=ctx.dataset_version,
        total=long.height,
        unique=long["emoji"].n_unique(),
        top=_counts(long, MessageQuery()),
        mine=_counts(mine, MessageQuery(sender_id=owner)),
        received=_counts(others, MessageQuery()),
    )


@router.get("/emojis", response_model=OverviewEmojisResponse)
def overview_emojis(ctx: Context) -> OverviewEmojisResponse:
    """Top emojis overall, sent by me, and sent by others."""
    return ctx.cached("overview_emojis", lambda: _build(ctx))
