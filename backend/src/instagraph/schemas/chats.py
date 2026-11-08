"""Chats list, chat header and the Summary / Timeline / When tabs (spec §5.5, §5.6, §7.2)."""

from datetime import date, datetime
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
from instagraph.schemas.overview import ChatKind, StreakInfo
from instagraph.store.dataset import Category

TrendLabel = Literal["rising", "stable", "fading"]
ChatSortKey = Literal[
    "title",
    "kind",
    "participants",
    "messages",
    "my_share",
    "messages_last_30d",
    "first_ts",
    "last_ts",
    "duration_days",
    "avg_per_day",
    "median_reply_me_ms",
    "median_reply_them_ms",
    "emojis",
    "calls",
    "trend_slope",
]

# --------------------------------------------------------------------------------------
# List
# --------------------------------------------------------------------------------------


class ChatListItem(ApiModel):
    thread_id: int
    title: str
    kind: ChatKind
    category: Category
    is_group: bool
    participants: list[PersonRef]
    n_participants: int
    messages: int
    my_share: float
    messages_last_30d: int = Field(description="Last 30 days up to the reference date")
    first_ts: datetime
    last_ts: datetime
    duration_days: int
    avg_per_day: float = Field(description="Messages per day over first → last day")
    median_reply_me_ms: float | None = None
    median_reply_them_ms: float | None = None
    emojis: int
    calls: int
    sparkline: list[int] = Field(description="Monthly counts aligned with sparkline_months")
    trend: TrendLabel
    trend_slope: float
    drilldown: MessageQuery


class ChatListResponse(ApiResponse):
    total: int = Field(description="Chats matching the search (before pagination)")
    offset: int
    limit: int
    sort: ChatSortKey
    order: Literal["asc", "desc"]
    reference_date: date | None = Field(
        default=None, description="Last day with messages in scope (for 'last 30 days')"
    )
    sparkline_months: list[str]
    items: list[ChatListItem]


# --------------------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------------------


class ChatHeaderResponse(ApiResponse):
    thread_id: int
    title: str
    kind: ChatKind
    category: Category
    is_group: bool
    participants: list[PersonRef]
    span: DateRange
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    span_days: int
    total_messages: int
    my_messages: int
    my_share: float
    drilldown: MessageQuery


# --------------------------------------------------------------------------------------
# Summary tab
# --------------------------------------------------------------------------------------


class ChatPersonRow(ApiModel):
    person: PersonRef
    messages: int
    share: float
    words: int
    avg_words_per_message: float | None = Field(description="Over text messages")
    characters: int
    emojis: int
    emojis_per_message: float | None
    reactions_given: int
    reactions_received: int
    photos: int
    videos: int
    voice_notes: int
    reels: int
    links: int
    calls_started: int
    unsent: int
    median_reply_ms: float | None = None
    initiation_rate: float | None = Field(description="Sessions started / all sessions")
    drilldown: MessageQuery


class SilenceInfo(ApiModel):
    gap_ms: int
    start: datetime
    end: datetime
    before: MessageRef
    after: MessageRef


class ChatSummaryResponse(ApiResponse):
    tiles: list[Tile]
    people: list[ChatPersonRow]
    messages_by_person: CategoryChart
    words_by_person: CategoryChart
    first_message: MessageRef | None = None
    last_message: MessageRef | None = None
    longest_streak: StreakInfo
    current_streak: StreakInfo
    mutual_streak: StreakInfo | None = Field(default=None, description="1:1 chats only")
    longest_silence: SilenceInfo | None = None


# --------------------------------------------------------------------------------------
# Timeline tab
# --------------------------------------------------------------------------------------

TimelineEventKind = Literal[
    "first_message",
    "peak_day",
    "peak_month",
    "silence",
    "rename",
    "join",
    "leave",
    "removed",
    "first_call",
    "custom_term",
]


class TimelineEvent(ApiModel):
    kind: TimelineEventKind
    date: date
    label: str
    person: PersonRef | None = None
    value: float | None = None
    message_id: int | None = None
    drilldown: MessageQuery | None = None


class LifecycleOut(ApiModel):
    first: date | None = None
    last: date | None = None
    span_days: int
    peak_month: date | None = None
    peak_month_count: int
    active_months: int
    longest_break_days: int
    break_start: date | None = None
    comeback: date | None = None


class ChatTimelineResponse(ApiResponse):
    requested_bucket: Bucket
    messages: TimeSeries = Field(description="Per person, stacked")
    share: TimeSeries = Field(description="Monthly share per person (0..1)")
    balance: TimeSeries | None = Field(default=None, description="1:1 only, monthly")
    balance_overall: float | None = None
    cumulative: TimeSeries = Field(description="Per person, running total")
    rolling: TimeSeries = Field(description="Series daily, avg_7, avg_30 (day bucket)")
    words_per_message: TimeSeries = Field(description="Monthly avg words per text message")
    calendar: CalendarData
    events: list[TimelineEvent]
    trend: TrendLabel
    trend_slope: float
    lifecycle: LifecycleOut


# --------------------------------------------------------------------------------------
# When tab
# --------------------------------------------------------------------------------------


class PersonHeatmap(ApiModel):
    person: PersonRef
    heatmap: HeatmapData


class PersonWhenStats(ApiModel):
    person: PersonRef
    messages: int
    night_owl_ratio: float = Field(description="Share of messages 00:00–04:59")
    early_bird_ratio: float = Field(description="Share of messages 05:00–07:59")
    weekend_ratio: float


class DayCount(ApiModel):
    date: date
    count: int
    drilldown: MessageQuery


class HourCount(ApiModel):
    date: date
    hour: int
    count: int
    drilldown: MessageQuery


class ChatWhenResponse(ApiResponse):
    heatmap: HeatmapData = Field(description="Everyone; rows Mon..Sun, columns 0..23")
    heatmaps: list[PersonHeatmap]
    hours: CategoryChart
    weekdays: CategoryChart
    months: CategoryChart = Field(description="Month of year (seasonality)")
    weekday_weekend: CategoryChart = Field(description="Ratio per person")
    person_stats: list[PersonWhenStats]
    hours_by_year: HeatmapData = Field(description="Rows = years, values = share of year")
    busiest_days: list[DayCount]
    busiest_hour: HourCount | None = None
