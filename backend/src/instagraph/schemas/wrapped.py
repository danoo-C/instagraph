"""Schemas for ``GET /api/wrapped`` and ``GET /api/wrapped/years`` (spec §5.10)."""

from datetime import date

from pydantic import Field

from instagraph.schemas.awards import Award
from instagraph.schemas.common import ApiModel, ApiResponse, MessageQuery, MessageRef, PersonRef


class WrappedMonth(ApiModel):
    month: date = Field(description="First day of the month")
    messages: int


class WrappedPersonCount(ApiModel):
    person: PersonRef
    messages: int
    drilldown: MessageQuery | None = None


class WrappedEmoji(ApiModel):
    emoji: str
    count: int
    drilldown: MessageQuery | None = None


class WrappedWord(ApiModel):
    word: str
    count: int


class WrappedDay(ApiModel):
    date: date
    messages: int
    drilldown: MessageQuery


class WrappedStreak(ApiModel):
    days: int
    start: date
    end: date


class WrappedReplier(ApiModel):
    person: PersonRef
    median_ms: float
    replies: int


class WrappedReactedMessage(ApiModel):
    message: MessageRef
    thread_title: str
    reactions: int
    emojis: list[str] = Field(description="Most used reaction emojis on it (≤ 3)")
    drilldown: MessageQuery


class WrappedCall(ApiModel):
    message: MessageRef
    thread_title: str
    duration_ms: int
    drilldown: MessageQuery


class WrappedPersonality(ApiModel):
    summary: str
    awards: list[Award] = Field(description="Awards the owner won in this scope")


class WrappedResponse(ApiResponse):
    year: int
    chat_id: int | None = None
    chat_title: str | None = None
    available_years: list[int]
    total_messages: int
    sent_by_me: int
    received: int
    total_drilldown: MessageQuery
    months: list[WrappedMonth]
    top_people: list[WrappedPersonCount]
    top_emoji: WrappedEmoji | None = None
    top_emojis: list[WrappedEmoji]
    busiest_day: WrappedDay | None = None
    night_owl_share: float | None = Field(default=None, description="Share 00:00–05:00, all")
    my_night_owl_share: float | None = Field(default=None, description="Owner's share")
    longest_streak: WrappedStreak | None = None
    top_word: str | None = Field(default=None, description="Null when anonymized")
    top_words: list[WrappedWord] = Field(description="Empty when anonymized")
    fastest_replier: WrappedReplier | None = None
    most_reacted_message: WrappedReactedMessage | None = None
    longest_call: WrappedCall | None = None
    personality: WrappedPersonality


class WrappedYear(ApiModel):
    year: int
    messages: int


class WrappedYearsResponse(ApiResponse):
    years: list[WrappedYear]
