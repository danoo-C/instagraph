"""Message Browser search (spec §5.9).

Pure functions over LazyFrames. The flow used by ``GET /api/messages``:

1. :func:`validate_query` — reject bad text/regex input early (422).
2. :func:`match_frame` — apply a :class:`~instagraph.schemas.common.MessageQuery` to the
   globally filtered messages and collect the (small) result set
   ``message_id, sender_id, local_date`` sorted by ``message_id``. The caller caches it,
   so further pages, stats, random and on-this-day reuse it.
3. :func:`page_slice` — keyset pagination over the sorted ids, both directions.
4. :func:`fetch_rows` — load the full rows of one page.

Ordering: ``message_id`` ascends with ``ts`` (ties keep file order, see
``store.dataset``), so ordering by ``message_id`` IS the ``(ts, message_id)`` keyset
order; cursors carry both values but positioning uses the id.

Regex safety: patterns run on Polars' Rust ``regex`` engine, which has no
backtracking (guaranteed linear time in the input), so catastrophic patterns such as
``(a+)+$`` cannot hang the server. Patterns are limited to :data:`MAX_REGEX_LEN`
characters and compiled once up front; compile errors and the engine's compiled-size
limit (huge counted repetitions) are reported as 422.
"""

import base64
import binascii
import random
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import polars as pl
import regex

from instagraph.api.filters import add_local_time, bucket_labels, bucket_range, bucket_start
from instagraph.errors import ValidationApiError
from instagraph.schemas.common import Bucket, MessageQuery
from instagraph.textproc import extract_emoticons, normalize_emoji, normalize_emoji_expr

MAX_TEXT_LEN = 1000
MAX_REGEX_LEN = 500
#: Columns of the collected match frame.
MATCH_COLUMNS = ("message_id", "sender_id", "local_date")

_WORD_CHAR = regex.compile(r"\w")
_DOMAIN_RE = r"^(?:[A-Za-z][A-Za-z0-9+.\-]*://)?(?:www\.)?([^/:?#\s]+)"


@dataclass(frozen=True)
class EmojiOptions:
    merge_skin_tones: bool = True
    merge_gendered: bool = False

    def norm(self, e: str) -> str:
        return normalize_emoji(
            e, merge_skin_tones=self.merge_skin_tones, merge_gendered=self.merge_gendered
        )

    def norm_expr(self, expr: pl.Expr) -> pl.Expr:
        return normalize_emoji_expr(
            expr, merge_skin_tones=self.merge_skin_tones, merge_gendered=self.merge_gendered
        )


# --------------------------------------------------------------------------------------
# Text matching
# --------------------------------------------------------------------------------------


def fold_accents_expr(expr: pl.Expr) -> pl.Expr:
    """Accent-insensitive form in Polars: NFKD + drop non-spacing marks (§6.5)."""
    return expr.str.normalize("NFKD").str.replace_all(r"\p{Mn}", "")


def fold_accents(s: str) -> str:
    """Same folding as :func:`fold_accents_expr` for a Python string."""
    out = pl.select(fold_accents_expr(pl.lit(s, dtype=pl.String))).item()
    return str(out)


def _escape(s: str) -> str:
    # Python's re.escape output is accepted by the Rust regex syntax.
    return re.escape(s)


def text_pattern(q: MessageQuery) -> tuple[str, bool] | None:
    """``(pattern, literal)`` for the text filter, or ``None`` if there is none.

    The pattern is meant for ``str.contains`` on the (optionally accent-folded) text.
    """
    needle = q.text
    if not needle:
        return None
    mode = q.text_mode or "plain"
    if q.accent_insensitive:
        needle = fold_accents(needle)
    case_sensitive = bool(q.case_sensitive)
    if mode == "plain":
        if case_sensitive:
            return needle, True
        return "(?i)" + _escape(needle), False
    if mode == "word":
        pattern = _escape(needle)
        if _WORD_CHAR.match(needle[0]):
            pattern = r"\b" + pattern
        if _WORD_CHAR.match(needle[-1]):
            pattern += r"\b"
    else:
        pattern = needle
    return (pattern if case_sensitive else "(?i)" + pattern), False


def validate_query(q: MessageQuery) -> None:
    """Raise :class:`ValidationApiError` (422) for unusable text/regex input."""
    if q.text is None:
        return
    if len(q.text) > MAX_TEXT_LEN:
        raise ValidationApiError(f"text must be at most {MAX_TEXT_LEN} characters")
    if (q.text_mode or "plain") == "regex" and len(q.text) > MAX_REGEX_LEN:
        raise ValidationApiError(f"regex must be at most {MAX_REGEX_LEN} characters")
    spec = text_pattern(q)
    if spec is None or spec[1]:
        return
    try:
        pl.Series("t", [""], dtype=pl.String).str.contains(spec[0])
    except pl.exceptions.PolarsError as exc:
        first = str(exc).strip().splitlines()
        detail = " ".join(line.strip() for line in first[:4] if line.strip())
        raise ValidationApiError(f"Invalid regular expression: {detail}") from exc


def text_filter_expr(q: MessageQuery) -> pl.Expr | None:
    spec = text_pattern(q)
    if spec is None:
        return None
    pattern, literal = spec
    col = pl.col("text")
    if q.accent_insensitive:
        col = fold_accents_expr(col)
    return col.str.contains(pattern, literal=literal).fill_null(False)


def domain_expr(expr: pl.Expr) -> pl.Expr:
    """Lower-case host without ``www.`` (mirrors ``textproc.url_domain``)."""
    return expr.str.extract(_DOMAIN_RE, 1).str.to_lowercase()


# --------------------------------------------------------------------------------------
# Query application
# --------------------------------------------------------------------------------------


def _ids(single: int | None, many: Sequence[int] | None) -> list[int] | None:
    vals: list[int] = []
    if single is not None:
        vals.append(single)
    if many:
        vals.extend(many)
    return vals if (single is not None or many is not None) else None


def apply_query(
    messages: pl.LazyFrame,
    q: MessageQuery,
    *,
    reactions: pl.LazyFrame,
    emoji: EmojiOptions | None = None,
) -> pl.LazyFrame:
    """Apply every vectorized :class:`MessageQuery` condition.

    ``messages`` must be a ``filtered_messages`` frame (has ``local_*`` columns);
    ``reactions`` is the raw reactions table. ``emoticon`` is pre-filtered here by
    literal substring only; :func:`match_frame` refines it exactly.
    """
    emoji = emoji or EmojiOptions()
    conds: list[pl.Expr] = []
    threads = _ids(q.thread_id, q.thread_ids)
    if threads is not None:
        conds.append(pl.col("thread_id").is_in(threads))
    senders = _ids(q.sender_id, q.sender_ids)
    if senders is not None:
        conds.append(pl.col("sender_id").is_in(senders))
    if q.start is not None:
        conds.append(pl.col("local_date") >= q.start)
    if q.end is not None:
        conds.append(pl.col("local_date") <= q.end)
    if q.types is not None:
        conds.append(pl.col("type").cast(pl.String).is_in(list(q.types)))
    if q.hour is not None:
        conds.append(pl.col("hour") == q.hour)
    if q.weekday is not None:
        conds.append(pl.col("weekday") == q.weekday)
    if q.month is not None:
        conds.append(pl.col("local_ts").dt.month() == q.month)
    if q.message_ids is not None:
        conds.append(pl.col("message_id").is_in(list(q.message_ids)))
    if q.session_id is not None:
        conds.append(pl.col("session_id") == q.session_id)
    if q.share_kind is not None:
        conds.append(pl.col("share_kind") == q.share_kind)
    if q.share_owner is not None:
        conds.append(pl.col("share_owner") == q.share_owner)
    if q.call_kind is not None:
        conds.append(pl.col("call_kind") == q.call_kind)
    if q.call_missed is not None:
        conds.append((pl.col("type") == "call") & (pl.col("call_missed") == q.call_missed))
    if q.system_event is not None:
        conds.append(pl.col("system_event") == q.system_event)
    if q.link_domain:
        dom = q.link_domain.strip().lower().removeprefix("www.")
        conds.append(pl.col("links").list.eval(domain_expr(pl.element())).list.contains(dom))
    if q.emoji:
        target = emoji.norm(q.emoji)
        if emoji.merge_skin_tones or emoji.merge_gendered:
            lst = pl.col("emojis").list.eval(emoji.norm_expr(pl.element()))
        else:
            lst = pl.col("emojis")
        conds.append(lst.list.contains(target))
    if q.emoticon:
        conds.append(pl.col("text").str.contains(q.emoticon, literal=True).fill_null(False))
    text_cond = text_filter_expr(q)
    if text_cond is not None:
        conds.append(text_cond)

    lf = messages
    for c in conds:
        lf = lf.filter(c)

    reaction_filtered = q.reaction_emoji is not None or q.reactor_id is not None
    if reaction_filtered or q.has_reactions is not None:
        r = reactions
        if q.reactor_id is not None:
            r = r.filter(pl.col("actor_id") == q.reactor_id)
        if q.reaction_emoji is not None:
            r = r.filter(emoji.norm_expr(pl.col("emoji")) == emoji.norm(q.reaction_emoji))
        keys = r.select("message_id").unique()
        if reaction_filtered or q.has_reactions:
            lf = lf.join(keys, on="message_id", how="semi")
        if q.has_reactions is False:
            lf = lf.join(reactions.select("message_id").unique(), on="message_id", how="anti")
    return lf


def match_frame(
    messages: pl.LazyFrame,
    q: MessageQuery,
    *,
    reactions: pl.LazyFrame,
    emoji: EmojiOptions | None = None,
) -> pl.DataFrame:
    """Collect the result set: :data:`MATCH_COLUMNS` sorted by ``message_id``."""
    lf = apply_query(messages, q, reactions=reactions, emoji=emoji)
    if q.emoticon:
        df = lf.select(*MATCH_COLUMNS, "text").collect()
        wanted = q.emoticon
        keep = [wanted in extract_emoticons(t) for t in df["text"].to_list()]
        df = df.filter(pl.Series(keep, dtype=pl.Boolean)).drop("text")
    else:
        df = lf.select(*MATCH_COLUMNS).collect()
    return df.sort("message_id")


# --------------------------------------------------------------------------------------
# Pagination
# --------------------------------------------------------------------------------------


def encode_cursor(ts_ms: int, message_id: int) -> str:
    raw = f"{ts_ms}:{message_id}".encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[int, int]:
    """``(ts_ms, message_id)`` from :func:`encode_cursor`; 422 on garbage."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        ts_s, mid_s = base64.urlsafe_b64decode(padded.encode("ascii")).decode().split(":")
        return int(ts_s), int(mid_s)
    except (ValueError, binascii.Error, UnicodeError) as exc:
        raise ValidationApiError("Invalid cursor") from exc


@dataclass(frozen=True)
class PageSlice:
    ids: list[int]
    """Message ids in display order."""
    has_next: bool
    has_prev: bool


def page_slice(
    ids: pl.Series,
    *,
    limit: int,
    order: str = "desc",
    cursor_id: int | None = None,
    direction: str = "after",
) -> PageSlice:
    """Keyset page over ascending ``ids``.

    ``order`` is the display order (``asc`` oldest first, ``desc`` newest first).
    ``direction="after"`` returns the ``limit`` items following ``cursor_id`` in display
    order, ``"before"`` the ``limit`` items preceding it (still in display order). The
    cursor message itself is never included and need not be in ``ids``.
    """
    n = ids.len()
    desc = order == "desc"
    if cursor_id is None:
        if desc:
            lo, hi = max(0, n - limit), n
        else:
            lo, hi = 0, min(n, limit)
    else:
        forward = (direction == "after") != desc
        if forward:
            lo = ids.search_sorted(cursor_id, "right")
            hi = min(n, lo + limit)
        else:
            hi = ids.search_sorted(cursor_id, "left")
            lo = max(0, hi - limit)
    chunk = [int(v) for v in ids.slice(lo, hi - lo).to_list()]
    more_low, more_high = lo > 0, hi < n
    if desc:
        chunk.reverse()
        return PageSlice(chunk, has_next=more_low, has_prev=more_high)
    return PageSlice(chunk, has_next=more_high, has_prev=more_low)


def fetch_rows(messages: pl.LazyFrame, ids: Sequence[int], tz: str) -> pl.DataFrame:
    """Full message rows for ``ids`` (raw messages table) in the order of ``ids``,
    with ``local_ts`` in ``tz``."""
    return fetch_page(messages, None, None, ids, tz)[0]


def fetch_page(
    messages: pl.LazyFrame,
    reactions: pl.LazyFrame | None,
    media: pl.LazyFrame | None,
    ids: Sequence[int],
    tz: str,
) -> tuple[pl.DataFrame, pl.DataFrame | None, pl.DataFrame | None]:
    """Rows (in the order of ``ids``, with ``local_ts``), their reactions and media.

    The three scans run in parallel (``collect_all``); an ``is_between`` pre-filter on
    the id range lets Parquet row-group statistics skip most of the file.
    """
    id_list = [int(i) for i in ids]
    if id_list:
        lo, hi = min(id_list), max(id_list)
        sel = pl.col("message_id").is_between(lo, hi) & pl.col("message_id").is_in(id_list)
    else:
        sel = pl.lit(False)
    frames = [add_local_time(messages.filter(sel), tz)]
    if reactions is not None:
        frames.append(reactions.filter(sel))
    if media is not None:
        frames.append(media.filter(sel).sort("media_id"))
    out = pl.collect_all(frames)
    order = pl.DataFrame(
        {"message_id": id_list, "_pos": list(range(len(id_list)))},
        schema={"message_id": pl.UInt32(), "_pos": pl.Int64()},
    )
    rows = out[0].join(order, on="message_id").sort("_pos").drop("_pos")
    rest = iter(out[1:])
    r = next(rest) if reactions is not None else None
    m = next(rest) if media is not None else None
    return rows, r, m


# --------------------------------------------------------------------------------------
# Stats over the result set
# --------------------------------------------------------------------------------------


def auto_bucket(start: date, end: date) -> Bucket:
    """day ≤ 92 days ≤ week ≤ 3 years ≤ month ≤ 40 years < year."""
    span = (end - start).days
    if span <= 92:
        return "day"
    if span <= 3 * 366:
        return "week"
    if span <= 40 * 366:
        return "month"
    return "year"


def match_timeline(
    matches: pl.DataFrame, bucket: Bucket | None = None
) -> tuple[Bucket, list[str], list[int]]:
    """Zero-filled counts per bucket (``bucket`` auto when ``None``)."""
    if matches.is_empty():
        return bucket or "day", [], []
    first: date = matches["local_date"].min()  # type: ignore[assignment]
    last: date = matches["local_date"].max()  # type: ignore[assignment]
    b: Bucket = bucket or auto_bucket(first, last)
    counts = (
        matches.group_by(bucket_start(pl.col("local_date"), b).alias("b"))
        .agg(pl.len().alias("n"))
        .to_dict(as_series=False)
    )
    by_bucket = dict(zip(counts["b"], counts["n"], strict=True))
    xs = bucket_range(first, last, b)
    return b, bucket_labels(xs), [int(by_bucket.get(x, 0)) for x in xs]


def per_person_counts(matches: pl.DataFrame) -> list[tuple[int, int]]:
    """``(sender_id, count)`` sorted by count desc, then id."""
    df = (
        matches.group_by("sender_id")
        .agg(pl.len().alias("n"))
        .sort(["n", "sender_id"], descending=[True, False])
    )
    return [
        (int(s), int(n)) for s, n in zip(df["sender_id"].to_list(), df["n"].to_list(), strict=True)
    ]


# --------------------------------------------------------------------------------------
# Context / random / on this day
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextSlice:
    thread_id: int
    ids: list[int]
    target_index: int
    has_more_before: bool
    has_more_after: bool


def context_slice(
    messages: pl.LazyFrame,
    message_id: int,
    *,
    before: int,
    after: int,
    exclude_system: bool = True,
) -> ContextSlice | None:
    """``before``/``after`` messages around ``message_id`` in its thread (raw table).

    The target itself is always included, even if it is a system message.
    """
    target = messages.filter(pl.col("message_id") == message_id).select("thread_id").collect()
    if target.is_empty():
        return None
    tid = int(target.item())
    lf = messages.filter(pl.col("thread_id") == tid)
    if exclude_system:
        lf = lf.filter((pl.col("type") != "system") | (pl.col("message_id") == message_id))
    ids = lf.select("message_id").collect()["message_id"].sort()
    pos = ids.search_sorted(message_id, "left")
    lo = max(0, pos - before)
    hi = min(ids.len(), pos + after + 1)
    chunk = [int(v) for v in ids.slice(lo, hi - lo).to_list()]
    return ContextSlice(tid, chunk, pos - lo, lo > 0, hi < ids.len())


def random_id(ids: pl.Series, seed: int | None = None) -> int | None:
    if ids.is_empty():
        return None
    rng = random.Random(seed)
    return int(ids[rng.randrange(ids.len())])


def on_this_day(matches: pl.DataFrame, day: date) -> dict[int, list[int]]:
    """Ids of matches on ``day``'s month-day in earlier years: ``{year: [ids asc]}``."""
    d = pl.col("local_date")
    df = matches.filter(
        (d.dt.month() == day.month) & (d.dt.day() == day.day) & (d.dt.year() < day.year)
    )
    out: dict[int, list[int]] = {}
    for mid, ld in zip(df["message_id"].to_list(), df["local_date"].to_list(), strict=True):
        out.setdefault(ld.year, []).append(int(mid))
    return out
