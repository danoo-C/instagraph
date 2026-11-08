"""Schemas for ``GET /api/compare?chats=…`` / ``?people=…`` (spec §5.8)."""

from typing import Literal

from pydantic import Field

from instagraph.schemas.common import (
    ApiModel,
    ApiResponse,
    CategoryChart,
    MessageQuery,
    PersonRef,
    Scalar,
    TimeSeries,
    Unit,
)


class CompareItem(ApiModel):
    key: str = Field(description="Series key: 'chat:<id>' or 'person:<id>'")
    kind: Literal["chat", "person"]
    id: int
    name: str
    color_index: int
    person: PersonRef | None = Field(default=None, description="Set in people mode")
    is_group: bool | None = Field(default=None, description="Set in chats mode")
    drilldown: MessageQuery


class CompareMetric(ApiModel):
    key: str
    label: str
    unit: Unit | None = None
    lower_is_better: bool = False
    values: list[Scalar] = Field(description="One value per item (same order as `items`)")


class CompareResponse(ApiResponse):
    mode: Literal["chats", "people"]
    items: list[CompareItem]
    metrics: list[CompareMetric] = Field(description="Side-by-side table rows")
    messages_per_day: TimeSeries = Field(description="Monthly avg messages per day")
    hours: CategoryChart = Field(description="Share of messages per hour 0..23 (ratio)")
    weekdays: CategoryChart = Field(description="Share of messages per weekday Mon..Sun")
    emoji_rate: TimeSeries = Field(description="Emojis per message, monthly")
    reply_median: TimeSeries = Field(description="Median reply time (ms), monthly")
