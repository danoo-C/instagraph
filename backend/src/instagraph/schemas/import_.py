"""Schemas for ``/api/import/*`` (spec §5.2, §5.4, §8.2)."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from instagraph.schemas.common import ApiModel

JobStatus = Literal["uploading", "running", "done", "error", "cancelled"]
JobKind = Literal["upload", "path", "sample"]


class ImportJobRef(ApiModel):
    job_id: str


class PathImportRequest(ApiModel):
    path: str = Field(
        min_length=1, description="Absolute path to an export folder, a zip, or a folder of zips"
    )


class SampleImportRequest(ApiModel):
    messages: int = Field(default=20_000, ge=100, le=5_000_000)
    seed: int = 42


class JobError(ApiModel):
    code: str
    message: str


class ImportJobState(ApiModel):
    """Current state of an import job (also the payload of every SSE event)."""

    job_id: str
    kind: JobKind
    status: JobStatus
    stage: str = Field(
        description="queued, sample, discovery, locales, parse, merge, derive, persist, done"
    )
    done: int = 0
    total: int = 0
    message: str = ""
    messages: int = Field(default=0, description="Messages parsed so far")
    error: JobError | None = None
    created_at: datetime
    finished_at: datetime | None = None
    dataset_version: int | None = None
    seq: int = Field(default=0, description="Increases on every state change")


class OwnerInfo(ApiModel):
    name: str | None = None
    person_id: int | None = None
    method: str = Field(
        default="none", description="settings / personal_information / most_threads / none"
    )
    auto_name: str | None = None
    auto_method: str = "none"
    personal_information_name: str | None = None


class LocaleInfo(ApiModel):
    detected: dict[str, int] = Field(default_factory=dict, description="Locale → matches")
    auto_enabled: list[str] = Field(default_factory=list)
    enabled: list[str] = Field(default_factory=list)
    mode: Literal["auto", "settings"] = "auto"
    sample_size: int = 0


class KeyCount(ApiModel):
    key: str
    count: int


class TextCount(ApiModel):
    text: str
    count: int


class FailedThread(ApiModel):
    folder: str
    error: str


class ImportReport(ApiModel):
    """The import report (§5.4)."""

    dataset_version: int
    import_id: str | None = None
    imported_at: datetime | None = None
    kind: JobKind | None = None
    sources: list[str] = Field(default_factory=list)
    files: int = 0
    zip_files: list[str] = Field(default_factory=list)
    layouts: list[str] = Field(default_factory=list)
    thread_sources: int = 0
    threads: int = 0
    messages: int = 0
    people: int = 0
    reactions: int = 0
    media: int = 0
    media_found: int = 0
    kept_uploads: bool = False
    owner: OwnerInfo = Field(default_factory=OwnerInfo)
    locales: LocaleInfo = Field(default_factory=LocaleInfo)
    encoding_fixes: int = 0
    duplicates_removed: int = 0
    threads_merged: int = 0
    unknown_keys: list[KeyCount] = Field(default_factory=list)
    unmatched_system_like: list[TextCount] = Field(
        default_factory=list, description="Top 50 (empty in anonymize mode)"
    )
    empty_messages: int = 0
    message_types: dict[str, int] = Field(default_factory=dict)
    parse_stats: dict[str, int] = Field(default_factory=dict, description="Parser counters")
    failed_threads: list[FailedThread] = Field(default_factory=list)
    earliest: datetime | None = None
    latest: datetime | None = None
    durations_ms: dict[str, float] = Field(default_factory=dict)
    total_ms: float | None = None


class DeleteUploadsResponse(ApiModel):
    deleted_files: int
    freed_bytes: int
    media_marked_missing: int
    dataset_version: int
