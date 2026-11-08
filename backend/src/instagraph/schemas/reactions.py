"""Reactions tab (§6.7, §7.2) response models."""

from instagraph.schemas.common import (
    ApiModel,
    ApiResponse,
    CategoryChart,
    HeatmapData,
    MessageQuery,
    MessageRef,
    PersonRef,
    Tile,
    TimeSeries,
)


class EmojiCount(ApiModel):
    emoji: str
    count: int
    drilldown: MessageQuery


class PersonReactionStats(ApiModel):
    person: PersonRef
    given: int
    received: int
    messages: int
    """Counted messages sent (denominator of ``received_per_100_messages``)."""
    received_per_100_messages: float | None = None
    self_reactions: int
    heart_reactions_given: int
    likes_given: int
    hearts_given: int
    """❤ reactions given + likes given."""
    heart_reactions_received: int
    likes_received: int
    hearts_received: int
    top_given: list[EmojiCount]
    top_received: list[EmojiCount]
    given_drilldown: MessageQuery
    received_drilldown: MessageQuery


class Reactor(ApiModel):
    person: PersonRef
    emoji: str


class ReactedMessage(ApiModel):
    message: MessageRef
    count: int
    reactors: list[Reactor]


class ReciprocityPair(ApiModel):
    a: PersonRef
    b: PersonRef
    a_to_b: int
    b_to_a: int
    reciprocity: float
    """min(a→b, b→a) / max(a→b, b→a)."""


class ReactionsResponse(ApiResponse):
    chat_id: int
    tiles: list[Tile]
    """total_reactions, self_reactions, likes, hearts, reciprocity (1:1 only)."""
    people: list[PersonReactionStats]
    given_received: CategoryChart
    """Categories = people; series ``given`` and ``received``."""
    top_emojis: list[EmojiCount]
    matrix: HeatmapData
    """``values[actor][target]``; y = actors, x = targets, same order as ``matrix_people``."""
    matrix_people: list[PersonRef]
    most_reacted: list[ReactedMessage]
    over_time: TimeSeries
    """Reactions given per person per month (by reaction time, else message time)."""
    reciprocity: list[ReciprocityPair]
