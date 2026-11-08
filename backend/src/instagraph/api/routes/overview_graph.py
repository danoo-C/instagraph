"""Overview social graph (spec §7.1 chart 16): ``GET /api/overview/graph``."""

from typing import Annotated

from fastapi import APIRouter, Query

from instagraph.analysis.group import social_graph
from instagraph.api.deps import Context
from instagraph.errors import ERROR_RESPONSES
from instagraph.schemas.group import SocialGraphResponse

router = APIRouter(prefix="/api/overview", tags=["overview"], responses=ERROR_RESPONSES)


@router.get("/graph", response_model=SocialGraphResponse)
def overview_graph(
    ctx: Context,
    include_owner: bool = False,
    max_nodes: Annotated[int, Query(ge=1, le=1000)] = 150,
) -> SocialGraphResponse:
    """People connected by shared group chats; node size = messages sent in scope."""

    def compute() -> SocialGraphResponse:
        nodes, edges = social_graph(
            ctx.messages(),
            ctx.dataset.threads_df(),
            ctx.person_ref,
            owner_id=ctx.owner_id,
            include_owner=include_owner,
            max_nodes=max_nodes,
        )
        return SocialGraphResponse(dataset_version=ctx.dataset_version, nodes=nodes, edges=edges)

    return ctx.cached("overview_graph", compute, extra=(include_owner, max_nodes))
