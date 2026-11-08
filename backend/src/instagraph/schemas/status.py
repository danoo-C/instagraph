"""Schemas for ``GET /api/status`` and ``DELETE /api/data``."""

from datetime import datetime

from pydantic import Field

from instagraph.schemas.common import ApiModel, PersonRef


class DatasetCounts(ApiModel):
    people: int
    threads: int
    messages: int
    reactions: int
    media: int


class StatusResponse(ApiModel):
    has_data: bool
    dataset_version: int
    counts: DatasetCounts | None = None
    owner: PersonRef | None = None
    first_ts: datetime | None = Field(default=None, description="Earliest message (UTC)")
    last_ts: datetime | None = Field(default=None, description="Latest message (UTC)")
