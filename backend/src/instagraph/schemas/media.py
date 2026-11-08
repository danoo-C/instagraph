"""Media & Links tab (spec §6.8, §7.2) and media file models."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from instagraph.schemas.common import (
    ApiModel,
    ApiResponse,
    CategoryChart,
    MessageQuery,
    PersonRef,
    TimeSeries,
)
from instagraph.store.dataset import MediaKind


class MediaPersonCounts(ApiModel):
    """Per-person counts (spec §6.8). Media counts are attachments (``n_*``), shares and
    unsent are messages, ``external_links`` = URLs in text + external shares."""

    person: PersonRef
    photos: int
    videos: int
    voice_notes: int
    gifs: int
    stickers: int
    shared_posts: int
    shared_reels: int
    shared_stories: int
    shared_profiles: int
    shared_other: int
    external_links: int
    unsent: int
    drilldown: MessageQuery


class RankedItem(ApiModel):
    """A label with a count (top shared accounts, top link domains)."""

    key: str
    count: int
    drilldown: MessageQuery


class VoiceNoteStats(ApiModel):
    """Voice notes of one person. Durations (ms) only cover notes whose file was read."""

    person: PersonRef
    count: int
    with_duration: int
    total_ms: int | None = None
    avg_ms: float | None = None
    drilldown: MessageQuery


class VoiceNotesSummary(ApiModel):
    status: Literal["ready", "pending", "unavailable"] = Field(
        description="Durations: ready, still being read in the background, or no files"
    )
    count: int
    with_duration: int
    total_ms: int | None = None
    avg_ms: float | None = None
    per_person: list[VoiceNoteStats]


class MediaItem(ApiModel):
    """One attachment for the gallery. ``url``/``thumb_url`` are null if the file is
    missing from the export."""

    media_id: int
    message_id: int
    kind: MediaKind
    sender: PersonRef
    ts: datetime
    exists: bool
    url: str | None = None
    thumb_url: str | None = None


class MediaGallery(ApiModel):
    """A page of gallery items (chronological). Pass ``next_cursor`` as ``cursor``."""

    items: list[MediaItem]
    total: int
    next_cursor: str | None = None
    years: list[int] = Field(description="Years that have items (for the year filter)")


class ChatMediaResponse(ApiResponse):
    chat_id: int
    per_person: list[MediaPersonCounts]
    counts_chart: CategoryChart = Field(description="Categories = media types, series = people")
    type_mix: TimeSeries = Field(
        description="Share of messages per type group per bucket (ratio, sums to 1)"
    )
    reels_exchange: TimeSeries = Field(description="Reels + posts shared per person per bucket")
    top_shared_accounts: list[RankedItem]
    top_link_domains: list[RankedItem]
    voice_notes: VoiceNotesSummary
    gallery: MediaGallery
