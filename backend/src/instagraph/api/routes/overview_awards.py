"""``GET /api/overview/awards`` — global awards across all chats (contacts + owner)."""

from fastapi import APIRouter

from instagraph.api.deps import Context
from instagraph.api.routes._insights_common import award_models, scoped_awards
from instagraph.errors import ERROR_RESPONSES
from instagraph.schemas.awards import AwardsResponse

router = APIRouter(tags=["awards"], responses=ERROR_RESPONSES)


@router.get("/api/overview/awards", response_model=AwardsResponse)
def overview_awards(ctx: Context) -> AwardsResponse:
    """Awards computed over every chat passing the filters."""

    def compute() -> AwardsResponse:
        return AwardsResponse(
            dataset_version=ctx.dataset_version,
            scope="global",
            awards=award_models(ctx, scoped_awards(ctx)),
        )

    return ctx.cached("overview_awards", compute)
