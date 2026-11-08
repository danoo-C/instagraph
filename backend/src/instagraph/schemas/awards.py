"""Schemas for ``GET /api/chats/{chat_id}/awards`` and ``GET /api/overview/awards``."""

from typing import Literal

from pydantic import Field

from instagraph.schemas.common import ApiModel, ApiResponse, MessageQuery, PersonRef

AwardUnit = Literal["count", "ratio", "ms", "per_message"]


class AwardRankEntry(ApiModel):
    person: PersonRef
    value: float


class Award(ApiModel):
    key: str = Field(description="Stable id, e.g. 'novelist'")
    emoji: str
    title: str
    rule: str = Field(description="What the award measures")
    eligibility: str = Field(description="Minimum activity needed to be eligible")
    unit: AwardUnit
    lower_is_better: bool = False
    winners: list[PersonRef] = Field(description="Empty when nobody qualifies; >1 on ties")
    value: float | None = Field(default=None, description="The winning value (in `unit`)")
    ranking: list[AwardRankEntry] = Field(description="Top eligible people, best first")
    drilldown: MessageQuery | None = None


class AwardsResponse(ApiResponse):
    scope: Literal["chat", "global"]
    chat_id: int | None = None
    awards: list[Award]
