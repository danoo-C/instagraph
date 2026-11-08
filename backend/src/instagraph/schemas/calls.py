"""Calls tab (spec §6.8, §7.2)."""

from datetime import datetime

from pydantic import Field

from instagraph.schemas.common import (
    ApiModel,
    ApiResponse,
    CategoryChart,
    HeatmapData,
    MessageQuery,
    PersonRef,
    Tile,
    TimeSeries,
)
from instagraph.store.dataset import CallKind


class CallItem(ApiModel):
    message_id: int
    ts: datetime
    caller: PersonRef
    kind: CallKind | None
    duration_ms: int | None
    missed: bool


class CallsResponse(ApiResponse):
    """Durations in ms. Duration stats (total/avg/median/longest, histogram) use
    connected calls only: not missed and duration > 0."""

    chat_id: int
    tiles: list[Tile]
    total_calls: int
    audio_calls: int
    video_calls: int
    connected_calls: int
    missed_calls: int
    total_ms: int
    avg_ms: float | None = None
    median_ms: float | None = None
    longest: CallItem | None = None
    per_month: TimeSeries = Field(description="Series 'calls' (count) and 'minutes'")
    starters: CategoryChart = Field(description="Calls started per person (caller = sender)")
    missed_per_person: CategoryChart = Field(description="Missed calls per caller")
    duration_histogram: CategoryChart
    heatmap: HeatmapData = Field(description="y = weekdays Mon..Sun, x = hours 0..23")
    longest_calls: list[CallItem]
    drilldown: MessageQuery
