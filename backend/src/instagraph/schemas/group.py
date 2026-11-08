"""Group tab (spec §7.2) and the Overview social graph (spec §7.1 chart 16)."""

from datetime import datetime

from pydantic import Field

from instagraph.schemas.common import (
    ApiModel,
    ApiResponse,
    CategoryChart,
    HeatmapData,
    MessageQuery,
    PersonRef,
    TimeSeries,
)
from instagraph.store.dataset import SystemEvent


class MemberStats(ApiModel):
    person: PersonRef
    messages: int
    share: float = Field(description="0..1 of the group's messages")
    words: int
    reactions_received: int
    media: int = Field(description="Photos + videos + voice notes + GIFs + stickers")
    drilldown: MessageQuery


class ChordLink(ApiModel):
    source: int = Field(description="person_id of the replier / reactor")
    target: int = Field(description="person_id replied / reacted to")
    value: int


class InteractionMatrix(ApiModel):
    """``matrix.values[i][j]`` = interactions from ``people[i]`` to ``people[j]``."""

    people: list[PersonRef]
    matrix: HeatmapData
    links: list[ChordLink]


class GroupEvent(ApiModel):
    message_id: int
    ts: datetime
    event: SystemEvent
    actor: PersonRef
    member: PersonRef | None = Field(default=None, description="Matched member, if any")
    value: str | None = Field(
        default=None, description="Payload (new name, member name, theme); null if anonymized"
    )


class MembershipInterval(ApiModel):
    start: datetime | None = Field(description="null = since before the first message")
    end: datetime | None = Field(description="null = still a member / until the end")


class MemberTimeline(ApiModel):
    person: PersonRef
    intervals: list[MembershipInterval]


class GroupResponse(ApiResponse):
    chat_id: int
    members: list[MemberStats]
    leaderboard: CategoryChart = Field(
        description="Categories = members; series messages/words/reactions/media"
    )
    lurkers: list[MemberStats] = Field(description="Members with < 1% of messages")
    replies: InteractionMatrix
    reactions: InteractionMatrix
    activity: TimeSeries
    membership_events: list[GroupEvent]
    membership: list[MemberTimeline]
    history: list[GroupEvent] = Field(description="Renames, photo / theme / emoji changes")
    mentions: CategoryChart = Field(description="@mentions per mentioned member")


class GraphNode(ApiModel):
    person: PersonRef
    messages: int = Field(description="Messages sent in scope (node size)")
    group_chats: int


class GraphEdge(ApiModel):
    source: int
    target: int
    weight: int = Field(description="Number of shared group chats")
    thread_ids: list[int]
    drilldown: MessageQuery


class SocialGraphResponse(ApiResponse):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
