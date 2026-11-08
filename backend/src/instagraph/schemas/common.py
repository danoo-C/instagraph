"""Shared response building blocks (spec §4.2, §7).

Area-specific response models live in ``instagraph/schemas/<area>.py`` and are composed
from these. Conventions:

* Every analysis response model subclasses :class:`ApiResponse` (``dataset_version``).
* People are always sent as :class:`PersonRef` (name already anonymized if the
  anonymize setting is on; build them with ``AnalysisContext.person_ref``).
* Times: ``datetime`` fields are UTC-aware; date-like chart labels are ISO strings in
  the request's display time zone (``YYYY-MM-DD`` for day/week/month/year buckets, the
  bucket START date).
* Durations are in **milliseconds** (``unit="ms"``) unless a field name says ``_s``.
* Ratios are fractions in ``[0, 1]`` (``unit="ratio"``); the frontend formats as %.
* Every count that should be clickable carries a :class:`MessageQuery` (``drilldown``)
  that the frontend merges with the global filters and opens in the Message Browser.
* Hours are ``0..23`` local time; weekdays are ISO ``1`` = Monday … ``7`` = Sunday.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from instagraph.store.dataset import CallKind, MessageType, ShareKind, SystemEvent

Bucket = Literal["day", "week", "month", "year"]
Unit = Literal[
    "count",
    "ratio",  # 0..1, shown as %
    "ms",  # duration in milliseconds
    "s",  # duration in seconds
    "date",  # value is an ISO date string
    "datetime",  # value is an ISO datetime string (UTC)
    "text",
    "emoji",
    "per_day",
    "per_message",
]
Scalar = int | float | str | bool | None


class ApiModel(BaseModel):
    """Base for all API models (forbids unknown fields so typos fail loudly)."""

    model_config = ConfigDict(extra="forbid")


class ApiResponse(ApiModel):
    """Base for analysis responses: the frontend invalidates its cache on version change."""

    dataset_version: int


class PersonRef(ApiModel):
    person_id: int
    name: str
    color_index: int = Field(description="Stable color slot; owner = 0")
    is_owner: bool = False


class MessageQuery(ApiModel):
    """Extra Message Browser filters behind a number (combined with global filters).

    All fields are optional and AND-ed. Dates are local dates (inclusive) in the
    display time zone. Unset fields don't filter.
    """

    thread_id: int | None = None
    thread_ids: list[int] | None = None
    sender_id: int | None = None
    sender_ids: list[int] | None = None
    start: date | None = None
    end: date | None = None
    types: list[MessageType] | None = None
    hour: int | None = Field(default=None, ge=0, le=23)
    weekday: int | None = Field(default=None, ge=1, le=7)
    month: int | None = Field(default=None, ge=1, le=12, description="Month of year")
    emoji: str | None = None
    emoticon: str | None = None
    text: str | None = None
    text_mode: Literal["plain", "word", "regex"] | None = None
    case_sensitive: bool | None = None
    accent_insensitive: bool | None = None
    has_reactions: bool | None = None
    reaction_emoji: str | None = None
    reactor_id: int | None = None
    share_kind: ShareKind | None = None
    share_owner: str | None = None
    link_domain: str | None = None
    call_kind: CallKind | None = None
    call_missed: bool | None = None
    system_event: SystemEvent | None = None
    session_id: int | None = None
    message_ids: list[int] | None = Field(
        default=None, description="Exact messages (e.g. 'longest messages' lists)"
    )


class Tile(ApiModel):
    """A headline number."""

    key: str = Field(description="Stable id, e.g. 'total_messages' (used for i18n)")
    label: str
    value: Scalar
    unit: Unit | None = None
    detail: str | None = Field(default=None, description="Secondary line, e.g. a date")
    person: PersonRef | None = None
    drilldown: MessageQuery | None = None


class Series(ApiModel):
    """One named series of values aligned with a chart's x/categories."""

    key: str = Field(description="Stable id: str(person_id), type name, emoji, …")
    name: str
    person_id: int | None = None
    color_index: int | None = None
    values: list[float | None]


class TimeSeries(ApiModel):
    """Values per time bucket. ``x`` = bucket start dates (ISO), contiguous, zero-filled."""

    bucket: Bucket
    x: list[str]
    series: list[Series]
    unit: Unit | None = None


class CategoryChart(ApiModel):
    """Bar / donut / grouped-bar data: one value per category per series."""

    categories: list[str]
    series: list[Series]
    unit: Unit | None = None
    drilldowns: list[MessageQuery | None] | None = Field(
        default=None, description="Optional per-category drilldown (same order)"
    )


class HeatmapData(ApiModel):
    """``values[y][x]``; e.g. y = weekdays (Mon..Sun), x = hours 0..23."""

    x_labels: list[str]
    y_labels: list[str]
    values: list[list[float]]
    unit: Unit | None = None


class CalendarDay(ApiModel):
    date: date
    value: float


class CalendarData(ApiModel):
    """GitHub-style calendar heatmap (only non-zero days are sent)."""

    days: list[CalendarDay]
    max_value: float


class TableColumn(ApiModel):
    key: str
    label: str
    type: Literal[
        "int", "float", "str", "date", "datetime", "ms", "ratio", "person", "emoji", "bool"
    ] = "str"


class TableData(ApiModel):
    """Generic table. For ``person`` columns the cell holds the ``person_id``."""

    columns: list[TableColumn]
    rows: list[dict[str, Scalar]]


class MessageRef(ApiModel):
    """A short reference to one message (lists, quote cards, tooltips)."""

    message_id: int
    thread_id: int
    sender: PersonRef
    ts: datetime
    type: MessageType
    text: str | None = Field(default=None, description="Snippet; null when anonymized")


class DateRange(ApiModel):
    start: date | None = None
    end: date | None = None
