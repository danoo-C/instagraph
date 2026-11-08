"""Overview (all chats) response models (spec §5.3, §7.1).

"Contacts" in overview charts are **chats** (threads): a 1:1 chat stands for the other
person (``person`` is set), a group chat for itself. This keeps every stacked chart
summing to the overview total.
"""

from datetime import date
from typing import Literal

from pydantic import Field

from instagraph.schemas.common import (
    ApiModel,
    ApiResponse,
    Bucket,
    CalendarData,
    CategoryChart,
    DateRange,
    HeatmapData,
    MessageQuery,
    MessageRef,
    PersonRef,
    Tile,
    TimeSeries,
)

ChatKind = Literal["one_to_one", "group", "request"]
ContactMetric = Literal["messages", "words", "emojis", "calls", "reactions"]


class StreakInfo(ApiModel):
    """Consecutive active days (``length_days`` = 0 → no streak)."""

    length_days: int
    start: date | None = None
    end: date | None = None
    drilldown: MessageQuery | None = None


class ChatRef(ApiModel):
    """A chat as a chart entity."""

    thread_id: int
    title: str
    kind: ChatKind
    person: PersonRef | None = Field(default=None, description="The other person (1:1 only)")


class FirstMessage(ApiModel):
    message: MessageRef
    chat: ChatRef
    days_since: int = Field(description="Days from the first message to today")


class BusiestDay(ApiModel):
    date: date
    count: int
    top_chat: ChatRef | None = None
    top_chat_count: int = 0
    drilldown: MessageQuery


class ExcludedRequests(ApiModel):
    """Message requests hidden by ``include_requests=false``."""

    threads: int
    messages: int
    note: str = Field(description="e.g. '+12 message requests not included'")


class OverviewResponse(ApiResponse):
    tiles: list[Tile]
    span: DateRange
    first_message: FirstMessage | None = None
    busiest_day: BusiestDay | None = None
    longest_streak: StreakInfo
    current_streak: StreakInfo
    excluded_requests: ExcludedRequests | None = None


class OverviewTimelineResponse(ApiResponse):
    """Messages per bucket stacked by the top 10 chats + ``others`` (series key
    ``str(thread_id)`` / ``"others"``)."""

    requested_bucket: Bucket
    timeline: TimeSeries
    chats: list[ChatRef]
    drilldowns: dict[str, MessageQuery] = Field(description="Per series key")


class SentReceivedResponse(ApiResponse):
    """Series ``sent`` (by me) and ``received`` (everyone else), both positive."""

    requested_bucket: Bucket
    timeline: TimeSeries
    drilldowns: dict[str, MessageQuery]


class ContactItem(ApiModel):
    chat: ChatRef
    value: float
    share: float = Field(description="value / total of all chats (0..1)")
    drilldown: MessageQuery


class ContactsResponse(ApiResponse):
    metric: ContactMetric
    total: float
    items: list[ContactItem]


class TopOverTimeResponse(ApiResponse):
    """Monthly ranks (bump chart; 1 = most messages that month among ALL chats, null =
    no messages) and monthly counts (stream graph) for the overall top chats."""

    chats: list[ChatRef]
    ranks: TimeSeries
    stream: TimeSeries


class OverviewCalendarResponse(ApiResponse):
    calendar: CalendarData
    years: list[int]
    active_days: int


class OverviewHeatmapResponse(ApiResponse):
    """Rows = weekdays Mon..Sun, columns = hours 0..23."""

    heatmap: HeatmapData


class HoursResponse(ApiResponse):
    """Categories ``"0".."23"``; series ``me`` and ``others`` (counts)."""

    chart: CategoryChart


class WeekdaysResponse(ApiResponse):
    """Categories Mon..Sun; series ``total``, ``me``, ``others``."""

    chart: CategoryChart


class CumulativeResponse(ApiResponse):
    timeline: TimeSeries


class NewConversationsResponse(ApiResponse):
    """People met per month: month of the first message in any chat with them."""

    timeline: TimeSeries
    total: int


class ActiveChatsResponse(ApiResponse):
    """Chats with ≥ 1 message per month."""

    timeline: TimeSeries


class TypesResponse(ApiResponse):
    """Monthly counts per message type (frontend stacks to 100%) plus totals."""

    timeline: TimeSeries
    totals: CategoryChart


class SizeBinOut(ApiModel):
    lo: int
    hi: int
    label: str
    chats: int
    drilldown: MessageQuery


class SizeDistributionResponse(ApiResponse):
    """Histogram of chats by messages per chat (log 1-2-5 bins)."""

    bins: list[SizeBinOut]
    chart: CategoryChart


class YearRow(ApiModel):
    year: int
    messages: int
    sent: int
    received: int
    people: int = Field(description="Distinct other people who wrote that year")
    chats: int
    active_days: int
    top_chat: ChatRef | None = None
    top_chat_messages: int = 0
    top_emoji: str | None = None
    top_emoji_count: int = 0
    drilldown: MessageQuery


class YearsResponse(ApiResponse):
    years: list[YearRow]
