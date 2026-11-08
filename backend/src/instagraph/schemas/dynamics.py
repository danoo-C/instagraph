"""Dynamics tab (§7.2) and overview reply times (§7.1 #15) response models.

Durations are in milliseconds, rates in ``[0, 1]``. Sessions, turns and reply latencies
come from fields derived at import with the settings' session gap / reply window; date
filters don't recompute them (see ``notes``).
"""

from datetime import datetime

from instagraph.schemas.common import (
    ApiModel,
    ApiResponse,
    CategoryChart,
    MessageQuery,
    MessageRef,
    PersonRef,
    Tile,
    TimeSeries,
    Unit,
)


class SessionRef(ApiModel):
    session_id: int
    start: datetime
    end: datetime
    duration_ms: int
    messages: int
    initiator: PersonRef | None = None
    first_message: MessageRef | None = None
    drilldown: MessageQuery


class SessionStats(ApiModel):
    count: int
    avg_messages: float | None = None
    median_messages: float | None = None
    avg_duration_ms: float | None = None
    median_duration_ms: float | None = None
    longest: SessionRef | None = None
    """Longest by duration."""
    most_messages: SessionRef | None = None
    drilldown: MessageQuery


class PersonSessionStats(ApiModel):
    person: PersonRef
    sessions_started: int
    initiation_rate: float
    last_words: int
    last_word_rate: float
    drilldown: MessageQuery


class PersonTurnStats(ApiModel):
    person: PersonRef
    turns: int
    messages: int
    avg_messages_per_turn: float
    double_texts: int
    triple_texts: int
    drilldown: MessageQuery


class LongestTurn(ApiModel):
    person: PersonRef
    messages: int
    duration_ms: int
    first_message: MessageRef
    drilldown: MessageQuery


class ConversationStarter(ApiModel):
    text: str | None = None
    """``None`` when anonymized."""
    count: int
    drilldown: MessageQuery


class PersonReplyStats(ApiModel):
    person: PersonRef
    replies: int
    median_ms: float | None = None
    mean_ms: float | None = None
    p25_ms: float | None = None
    p75_ms: float | None = None
    p90_ms: float | None = None
    fastest_ms: int | None = None
    slowest_ms: int | None = None
    turns: int
    """Turns by this person (whether or not they got a reply)."""
    replied_turns: int
    reply_rate: float | None = None
    """Share of this person's turns that got a reply within the reply window."""
    unanswered_turns: int
    """Left on read: turns with no reply within the reply window."""
    drilldown: MessageQuery


class LongestWait(ApiModel):
    latency_ms: int
    replier: PersonRef
    waited_for: PersonRef | None = None
    before: MessageRef | None = None
    reply: MessageRef
    drilldown: MessageQuery


class EffortPart(ApiModel):
    key: str
    label: str
    value: float | None = None
    unit: Unit | None = None
    z: float | None = None


class PersonEffort(ApiModel):
    person: PersonRef
    parts: list[EffortPart]
    combined_z: float | None = None


class DynamicsResponse(ApiResponse):
    chat_id: int
    session_gap_ms: int
    reply_window_ms: int
    notes: list[str]
    sessions: SessionStats
    session_length_histogram: CategoryChart
    """Sessions per length bin (messages, log-2 bins)."""
    session_duration_histogram: CategoryChart
    people: list[PersonSessionStats]
    initiators: CategoryChart
    """Donut: sessions started per person."""
    initiators_over_time: TimeSeries
    last_word: CategoryChart
    turns: list[PersonTurnStats]
    turns_chart: CategoryChart
    """Categories = people; series: avg messages per turn, double texts, triple texts."""
    longest_turn: LongestTurn | None = None
    starters: list[ConversationStarter]
    replies: list[PersonReplyStats]
    reply_histogram: CategoryChart
    """Categories = latency bins (log, 1 s … reply window); one series per person."""
    reply_histogram_edges_ms: list[int]
    """Inclusive upper edge of each histogram bin."""
    reply_median_over_time: TimeSeries
    reply_median_by_hour: CategoryChart
    """Categories ``"0".."23"`` (hour of the reply); median ms per person."""
    longest_wait: LongestWait | None = None
    effort: list[PersonEffort]


class ReplyTimesOverviewResponse(ApiResponse):
    owner: PersonRef | None = None
    tiles: list[Tile]
    over_time: TimeSeries
    """Series ``me`` (my median reply time) and ``others`` (others' median reply to me)."""
