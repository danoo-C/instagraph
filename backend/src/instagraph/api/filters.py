"""Global filter parameters (spec §5.1, §8.2) and the canonical filtered views.

Every analysis endpoint gets data ONLY through :func:`filtered_messages` /
:func:`filtered_reactions` so that all numbers respect the same filters.

Filter semantics
----------------
* ``start``/``end``: inclusive local dates in ``tz``.
* ``tz``: IANA zone; DST-correct conversion per timestamp.
* ``include_groups`` / ``include_requests`` / ``include_archived``: thread filters.
* ``exclude_system``: drops ``type == "system"`` rows (likes stay; use
  :func:`is_counted_message` to drop likes too).
* ``people``: keeps messages whose ``sender_id`` is in the list. For reactions it keeps
  rows where the actor OR the message author is in the list.
"""

from datetime import date, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from instagraph.errors import ValidationApiError
from instagraph.store.dataset import NON_COUNTED_TYPES, Dataset

#: Columns added by :func:`filtered_messages`.
LOCAL_COLUMNS = ("local_ts", "local_date", "hour", "weekday")
#: Types excluded from text stats (§6.1).
NON_TEXT_TYPES: tuple[str, ...] = (*NON_COUNTED_TYPES, "unsent", "call")


class FilterParams(BaseModel):
    """The global filters. Build via the ``get_filters`` dependency in routes."""

    model_config = ConfigDict(frozen=True)

    start: date | None = None
    end: date | None = None
    tz: str = "UTC"
    include_groups: bool = True
    include_requests: bool = False
    include_archived: bool = True
    exclude_system: bool = True
    people: tuple[int, ...] | None = Field(default=None)

    def cache_key(self) -> str:
        return self.model_dump_json()


def validate_tz(tz: str) -> str:
    """Return ``tz`` if it is a known IANA zone, else raise a 422 ApiError."""
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationApiError(f"Unknown time zone: {tz!r}") from exc
    return tz


def parse_people(value: str | None) -> tuple[int, ...] | None:
    """Parse ``"1,2,3"`` → ``(1, 2, 3)``; empty/None → None; garbage → 422."""
    if value is None or not value.strip():
        return None
    try:
        return tuple(int(p) for p in value.split(",") if p.strip())
    except ValueError as exc:
        raise ValidationApiError("people must be a comma-separated list of ids") from exc


# --------------------------------------------------------------------------------------
# Semantic expressions (§6.1)
# --------------------------------------------------------------------------------------


def is_counted_message() -> pl.Expr:
    """ "Messages" per §6.1: ``type`` not in {system, like}."""
    return ~pl.col("type").cast(pl.String).is_in(NON_COUNTED_TYPES)


def is_text_message() -> pl.Expr:
    """Messages that count for text stats (words, length): counted, not unsent/call,
    with non-empty text."""
    return (
        ~pl.col("type").cast(pl.String).is_in(NON_TEXT_TYPES)
        & pl.col("text").is_not_null()
        & (pl.col("char_count") > 0)
    )


# --------------------------------------------------------------------------------------
# Filtered views
# --------------------------------------------------------------------------------------


def allowed_thread_ids(ds: Dataset, f: FilterParams) -> list[int]:
    """Thread ids passing the group/request/archived filters."""
    threads = ds.threads_df()
    cond = pl.lit(True)
    if not f.include_groups:
        cond &= ~pl.col("is_group")
    if not f.include_requests:
        cond &= pl.col("category") != "request"
    if not f.include_archived:
        cond &= pl.col("category") != "archived"
    return threads.filter(cond)["thread_id"].to_list()


def add_local_time(lf: pl.LazyFrame, tz: str, ts_col: str = "ts") -> pl.LazyFrame:
    """Add ``local_ts`` (Datetime in ``tz``), ``local_date`` (Date), ``hour`` (0..23),
    ``weekday`` (ISO 1=Mon..7=Sun) computed from ``ts_col``."""
    local = pl.col(ts_col).dt.convert_time_zone(tz)
    return lf.with_columns(local.alias("local_ts")).with_columns(
        pl.col("local_ts").dt.date().alias("local_date"),
        pl.col("local_ts").dt.hour().alias("hour"),
        pl.col("local_ts").dt.weekday().alias("weekday"),
    )


def _date_filter(lf: pl.LazyFrame, f: FilterParams) -> pl.LazyFrame:
    if f.start is None and f.end is None:
        return lf
    # Cheap UTC pre-filter with a 1-day margin (offsets are < 1 day), then exact local.
    cond = pl.lit(True)
    if f.start is not None:
        cond &= pl.col("ts") >= pl.lit(f.start - timedelta(days=1)).cast(pl.Datetime("ms", "UTC"))
    if f.end is not None:
        cond &= pl.col("ts") < pl.lit(f.end + timedelta(days=2)).cast(pl.Datetime("ms", "UTC"))
    lf = lf.filter(cond)
    if f.start is not None:
        lf = lf.filter(pl.col("local_date") >= f.start)
    if f.end is not None:
        lf = lf.filter(pl.col("local_date") <= f.end)
    return lf


def filtered_messages(
    ds: Dataset, f: FilterParams, *, thread_id: int | None = None
) -> pl.LazyFrame:
    """Messages after all global filters, with ``local_ts``, ``local_date``, ``hour``,
    ``weekday`` added (see :func:`add_local_time`). All ``MESSAGES_SCHEMA`` columns kept.

    Note: system messages are dropped when ``f.exclude_system``; likes are kept — use
    ``.filter(is_counted_message())`` for message counts.

    ``thread_id`` restricts to one chat and IGNORES the thread-category filters
    (chat scope; prefer ``AnalysisContext.chat_messages``).
    """
    if thread_id is not None:
        lf = ds.messages.filter(pl.col("thread_id") == thread_id)
    else:
        lf = ds.messages.filter(pl.col("thread_id").is_in(allowed_thread_ids(ds, f)))
    if f.exclude_system:
        lf = lf.filter(pl.col("type") != "system")
    if f.people is not None:
        lf = lf.filter(pl.col("sender_id").is_in(list(f.people)))
    lf = add_local_time(lf, f.tz)
    return _date_filter(lf, f)


def filtered_reactions(
    ds: Dataset, f: FilterParams, messages: pl.LazyFrame | None = None
) -> pl.LazyFrame:
    """Reactions on messages passing the filters (date range = the MESSAGE's date).

    Columns: ``message_id, actor_id, emoji, ts`` (reaction ts, nullable) plus
    ``thread_id``, ``target_id`` (author of the reacted message), ``message_ts`` and
    ``local_ts/local_date/hour/weekday`` of the reaction (falling back to the message
    time when the reaction has no timestamp).

    ``messages`` may be a pre-computed :func:`filtered_messages` frame WITHOUT the
    people filter; by default it is computed here.
    """
    if messages is None:
        messages = filtered_messages(ds, f.model_copy(update={"people": None}))
    msgs = messages.select(
        "message_id",
        "thread_id",
        pl.col("sender_id").alias("target_id"),
        pl.col("ts").alias("message_ts"),
    )
    lf = ds.reactions.join(msgs, on="message_id", how="inner")
    if f.people is not None:
        ids = list(f.people)
        lf = lf.filter(pl.col("actor_id").is_in(ids) | pl.col("target_id").is_in(ids))
    lf = lf.with_columns(pl.coalesce("ts", "message_ts").alias("_rts"))
    return add_local_time(lf, f.tz, "_rts").drop("_rts")


# --------------------------------------------------------------------------------------
# Time bucketing helpers (§6.2)
# --------------------------------------------------------------------------------------

_TRUNCATE = {"day": "1d", "week": "1w", "month": "1mo", "year": "1y"}


def bucket_start(expr: pl.Expr, bucket: str) -> pl.Expr:
    """Truncate a Date expression to its bucket start (weeks start on Monday)."""
    return expr.dt.truncate(_TRUNCATE[bucket])


def bucket_range(start: date, end: date, bucket: str) -> list[date]:
    """All bucket start dates covering ``start..end`` (inclusive), for zero-filling."""
    s = pl.select(bucket_start(pl.lit(start), bucket)).item()
    return pl.date_range(s, end, interval=_TRUNCATE[bucket], eager=True).to_list()


def bucket_labels(dates: list[date]) -> list[str]:
    return [d.isoformat() for d in dates]
