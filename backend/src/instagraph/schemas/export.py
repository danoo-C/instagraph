"""JSON export models (spec §11): full stats summary for everything or one chat."""

from datetime import date, datetime

from pydantic import Field

from instagraph.api.filters import FilterParams
from instagraph.schemas.common import ApiModel, ApiResponse, PersonRef
from instagraph.store.dataset import Category, MessageType


class ExportTotals(ApiModel):
    messages: int = Field(description="Counted messages (§6.1: no system, no likes)")
    words: int
    chars: int
    likes: int
    unsent: int
    photos: int
    videos: int
    audios: int
    gifs: int
    stickers: int
    shares: int
    links: int
    calls: int
    call_duration_s: int
    reactions: int
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    first_date: date | None = Field(default=None, description="Local date in tz")
    last_date: date | None = None
    active_days: int
    span_days: int


class ExportPerson(ApiModel):
    person: PersonRef
    messages: int
    words: int
    chars: int
    media: int
    reactions_given: int
    reactions_received: int
    first_ts: datetime | None = None
    last_ts: datetime | None = None


class ExportChat(ApiModel):
    thread_id: int
    title: str
    category: Category
    is_group: bool
    participants: int
    messages: int
    first_ts: datetime | None = None
    last_ts: datetime | None = None


class ExportCount(ApiModel):
    key: str
    count: int


class ExportMessage(ApiModel):
    message_id: int
    thread_id: int
    sender_id: int
    sender: str
    ts: datetime = Field(description="With the display time zone's offset")
    type: MessageType
    text: str | None = Field(default=None, description="Null when anonymized")


class ExportResponse(ApiResponse):
    scope: str = Field(description="'all' or 'chat-<id>'")
    generated_at: datetime
    filters: FilterParams
    chat: ExportChat | None = None
    totals: ExportTotals
    people: list[ExportPerson]
    chats: list[ExportChat]
    types: list[ExportCount] = Field(description="All rows by message type")
    years: list[ExportCount]
    hours: list[int] = Field(description="Counted messages per local hour 0..23")
    weekdays: list[int] = Field(description="Counted messages per ISO weekday Mon..Sun")
    top_emojis: list[ExportCount]
    top_reactions: list[ExportCount]
    messages: list[ExportMessage] | None = Field(
        default=None, description="Only with include_messages=true"
    )
