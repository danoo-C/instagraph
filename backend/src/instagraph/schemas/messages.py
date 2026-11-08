"""Message Browser models (spec §5.9): search pages, context, random, on-this-day."""

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from instagraph.schemas.common import (
    ApiModel,
    ApiResponse,
    MessageQuery,
    PersonRef,
    TimeSeries,
)
from instagraph.store.dataset import CallKind, MediaKind, MessageType, ShareKind, SystemEvent

SortOrder = Literal["asc", "desc"]


class ReactionItem(ApiModel):
    emoji: str
    actor: PersonRef


class MediaItem(ApiModel):
    media_id: int
    kind: MediaKind
    exists: bool = Field(description="File available for display (GET /api/media/{id})")


class Attachments(ApiModel):
    photos: int = 0
    videos: int = 0
    audios: int = 0
    gifs: int = 0
    stickers: int = 0
    media: list[MediaItem] = Field(default_factory=list)


class ShareInfo(ApiModel):
    kind: ShareKind | None = None
    link: str | None = Field(default=None, description="Null when anonymized")
    text: str | None = Field(default=None, description="Null when anonymized")
    owner: str | None = Field(default=None, description="Null when anonymized")


class CallInfo(ApiModel):
    kind: CallKind | None = None
    duration_s: int | None = None
    missed: bool = False


class MessageItem(ApiModel):
    """One message as shown in the browser (chat bubble)."""

    message_id: int
    thread_id: int
    thread_title: str
    sender: PersonRef
    ts: datetime = Field(description="Timestamp with the display time zone's offset")
    type: MessageType
    text: str | None = Field(default=None, description="Null when anonymized or absent")
    links: list[str] = Field(default_factory=list)
    reactions: list[ReactionItem] = Field(default_factory=list)
    attachments: Attachments
    share: ShareInfo | None = None
    call: CallInfo | None = None
    system_event: SystemEvent | None = None
    system_payload: str | None = Field(default=None, description="Null when anonymized")


class PersonCount(ApiModel):
    person: PersonRef
    count: int
    drilldown: MessageQuery


class SearchStats(ApiModel):
    """Timeline of matches (auto bucket) and per-person counts for the whole result set."""

    timeline: TimeSeries
    per_person: list[PersonCount]


class MessagePage(ApiResponse):
    """One page of search results in display order (``order``).

    ``next_cursor`` continues in ``order`` after the last item; ``prev_cursor`` goes
    back before the first item (pass it with ``direction=before``).
    """

    items: list[MessageItem]
    total: int = Field(description="Number of messages matching the query")
    order: SortOrder
    next_cursor: str | None = None
    prev_cursor: str | None = None
    has_next: bool
    has_prev: bool
    stats: SearchStats | None = None


class MessageContext(ApiResponse):
    """Messages around one message in its thread (``items[target_index]`` is it)."""

    thread_id: int
    thread_title: str
    target_id: int
    target_index: int
    items: list[MessageItem]
    has_more_before: bool
    has_more_after: bool


class RandomMessage(ApiResponse):
    message: MessageItem | None = Field(description="Null when nothing matches")


class OnThisDayYear(ApiModel):
    year: int
    date: date
    years_ago: int
    count: int
    messages: list[MessageItem]
    drilldown: MessageQuery


class OnThisDay(ApiResponse):
    date: date
    years: list[OnThisDayYear] = Field(description="Most recent year first")
