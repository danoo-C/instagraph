"""Schemas for ``GET /api/people`` and ``GET /api/people/{person_id}`` (spec §5.7)."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from instagraph.schemas.common import (
    ApiModel,
    ApiResponse,
    CategoryChart,
    MessageQuery,
    PersonRef,
    Scalar,
    Tile,
    TimeSeries,
    Unit,
)
from instagraph.store.dataset import Category

PeopleSort = Literal["exchanged", "messages", "name", "last_contact", "threads"]


class PersonListItem(ApiModel):
    person: PersonRef
    is_deleted_account: bool = False
    messages_sent: int = Field(description="Counted messages they sent (any chat in scope)")
    messages_exchanged: int = Field(
        description="Their messages + my messages in our 1:1 chat (owner: all messages)"
    )
    threads_in_common: int
    one_to_one_threads: int
    group_threads: int
    first_contact: datetime | None = None
    last_contact: datetime | None = None
    drilldown: MessageQuery


class PeopleListResponse(ApiResponse):
    total: int = Field(description="Matching people before pagination")
    offset: int
    limit: int
    items: list[PersonListItem]


class PersonThread(ApiModel):
    thread_id: int
    title: str
    is_group: bool
    category: Category
    messages: int = Field(description="All counted messages in the chat (in scope)")
    their_messages: int
    my_messages: int
    last_ts: datetime | None = None
    drilldown: MessageQuery


class PersonStatRow(ApiModel):
    key: str
    label: str
    unit: Unit | None = None
    lower_is_better: bool = False
    person_value: Scalar = Field(description="This person's value (owner on the Me page)")
    my_value: Scalar = Field(default=None, description="Owner's value; null on the Me page")


class EmojiCount(ApiModel):
    emoji: str
    count: int


class PersonContact(ApiModel):
    person: PersonRef
    messages_exchanged: int
    drilldown: MessageQuery


class PersonDetailResponse(ApiResponse):
    person: PersonRef
    is_me: bool
    is_deleted_account: bool = False
    tiles: list[Tile]
    threads: list[PersonThread] = Field(description="Threads in common (Me: all my chats)")
    activity: TimeSeries = Field(description="Monthly messages: them vs me in shared chats")
    hours: CategoryChart = Field(description="Share of messages per hour (ratio)")
    weekdays: CategoryChart = Field(description="Share of messages per weekday (ratio)")
    stats: list[PersonStatRow] = Field(description="Their stats vs mine (shared chats)")
    top_emojis: list[EmojiCount]
    top_contacts: list[PersonContact] | None = Field(
        default=None, description="Me page only: people I talk to most"
    )
