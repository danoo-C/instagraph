"""``GET /api/chats/{chat_id}/awards`` — fun badges for one chat (spec §7.2 Awards tab)."""

from fastapi import APIRouter

from instagraph.api.deps import Context
from instagraph.api.routes._insights_common import award_models, scoped_awards
from instagraph.errors import ERROR_RESPONSES
from instagraph.schemas.awards import AwardsResponse

router = APIRouter(tags=["awards"], responses=ERROR_RESPONSES)


@router.get("/api/chats/{chat_id}/awards", response_model=AwardsResponse)
def chat_awards(chat_id: int, ctx: Context) -> AwardsResponse:
    """Every award with winner(s), the value that earned it and the runner-ups."""
    ctx = ctx.for_chat(chat_id)

    def compute() -> AwardsResponse:
        results = scoped_awards(ctx, thread_id=chat_id)
        return AwardsResponse(
            dataset_version=ctx.dataset_version,
            scope="chat",
            chat_id=chat_id,
            awards=award_models(ctx, results, thread_id=chat_id),
        )

    return ctx.cached("chat_awards", compute, extra=chat_id)
