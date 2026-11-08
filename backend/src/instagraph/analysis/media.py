"""Media, shares and links (spec §6.8, §7.2 Media & Links tab).

Pure functions over (already filtered, chat-scoped) message LazyFrames from
:func:`instagraph.api.filters.filtered_messages`. People are rendered through the
``ref`` callable (``AnalysisContext.person_ref``) so anonymize mode applies.
"""

from collections.abc import Callable, Sequence
from datetime import date

import polars as pl

from instagraph import textproc
from instagraph.api.filters import bucket_labels, bucket_range, bucket_start, is_counted_message
from instagraph.media.audio import DurationStatus
from instagraph.schemas.common import (
    Bucket,
    CategoryChart,
    MessageQuery,
    PersonRef,
    Series,
    TimeSeries,
)
from instagraph.schemas.media import (
    MediaGallery,
    MediaItem,
    MediaPersonCounts,
    RankedItem,
    VoiceNotesSummary,
    VoiceNoteStats,
)

PersonRefFn = Callable[[int], PersonRef]

#: Type groups for the "message type mix" chart (§6.8), in display order.
TYPE_GROUPS: tuple[str, ...] = ("text", "photo", "video", "audio", "share", "other")
_TYPE_TO_GROUP = {
    "text": "text",
    "link": "text",
    "photo": "photo",
    "video": "video",
    "audio": "audio",
    "share": "share",
}
#: Category order of the per-person media chart.
COUNT_FIELDS: tuple[tuple[str, str], ...] = (
    ("photos", "Photos"),
    ("videos", "Videos"),
    ("voice_notes", "Voice notes"),
    ("gifs", "GIFs"),
    ("stickers", "Stickers"),
    ("shared_posts", "Posts"),
    ("shared_reels", "Reels"),
    ("shared_stories", "Stories"),
    ("shared_profiles", "Profiles"),
    ("external_links", "Links"),
)
GALLERY_KINDS: tuple[str, ...] = ("photo", "video")


def _ordered_people(found: Sequence[int], people: Sequence[int] | None) -> list[int]:
    order = list(people or [])
    order.extend(p for p in found if p not in order)
    return order


def person_counts(msgs: pl.LazyFrame) -> pl.DataFrame:
    """One row per ``sender_id`` with every §6.8 count (see :class:`MediaPersonCounts`)."""
    kind = pl.col("share_kind").cast(pl.String)
    return (
        msgs.group_by("sender_id")
        .agg(
            pl.col("n_photos").cast(pl.Int64).sum().alias("photos"),
            pl.col("n_videos").cast(pl.Int64).sum().alias("videos"),
            pl.col("n_audios").cast(pl.Int64).sum().alias("voice_notes"),
            pl.col("n_gifs").cast(pl.Int64).sum().alias("gifs"),
            pl.col("n_stickers").cast(pl.Int64).sum().alias("stickers"),
            (kind == "post").sum().alias("shared_posts"),
            (kind == "reel").sum().alias("shared_reels"),
            (kind == "story").sum().alias("shared_stories"),
            (kind == "profile").sum().alias("shared_profiles"),
            (kind == "other").sum().alias("shared_other"),
            (
                pl.col("links").list.len().cast(pl.Int64).sum()
                + (kind == "external").sum().cast(pl.Int64)
            ).alias("external_links"),
            (pl.col("type") == "unsent").sum().alias("unsent"),
        )
        .sort("sender_id")
        .collect()
    )


def build_person_counts(
    msgs: pl.LazyFrame, ref: PersonRefFn, chat_id: int, people: Sequence[int] | None = None
) -> list[MediaPersonCounts]:
    """Per-person counts; ``people`` (e.g. chat participants) are listed first, with
    zeros when they sent nothing."""
    df = person_counts(msgs)
    by_id = {int(r["sender_id"]): r for r in df.iter_rows(named=True)}
    rows: list[MediaPersonCounts] = []
    for pid in _ordered_people(list(by_id), people):
        r = by_id.get(pid, {})
        rows.append(
            MediaPersonCounts(
                person=ref(pid),
                drilldown=MessageQuery(thread_id=chat_id, sender_id=pid),
                **{
                    f: int(r.get(f) or 0)
                    for f in (*(k for k, _ in COUNT_FIELDS), "shared_other", "unsent")
                },
            )
        )
    return rows


def counts_chart(rows: Sequence[MediaPersonCounts]) -> CategoryChart:
    return CategoryChart(
        categories=[label for _, label in COUNT_FIELDS],
        series=[
            Series(
                key=str(r.person.person_id),
                name=r.person.name,
                person_id=r.person.person_id,
                color_index=r.person.color_index,
                values=[float(getattr(r, f)) for f, _ in COUNT_FIELDS],
            )
            for r in rows
        ],
        unit="count",
    )


def _date_span(df: pl.DataFrame, col: str = "local_date") -> tuple[date, date] | None:
    if df.is_empty():
        return None
    lo, hi = df[col].min(), df[col].max()
    if not isinstance(lo, date) or not isinstance(hi, date):
        return None
    return lo, hi


def type_mix(msgs: pl.LazyFrame, bucket: Bucket = "month") -> TimeSeries:
    """Share of counted messages per type group per bucket (100% stacked, ratio)."""
    df = (
        msgs.filter(is_counted_message())
        .select(
            bucket_start(pl.col("local_date"), bucket).alias("b"),
            pl.col("type")
            .cast(pl.String)
            .replace_strict(_TYPE_TO_GROUP, default="other", return_dtype=pl.String)
            .alias("g"),
        )
        .group_by("b", "g")
        .agg(pl.len().alias("n"))
        .collect()
    )
    span = _date_span(df, "b")
    if span is None:
        return TimeSeries(bucket=bucket, x=[], series=[], unit="ratio")
    xs = bucket_range(span[0], span[1], bucket)
    counts = {(b, g): int(n) for b, g, n in df.iter_rows()}
    totals = {b: sum(counts.get((b, g), 0) for g in TYPE_GROUPS) for b in xs}
    series = [
        Series(
            key=g,
            name=g.capitalize(),
            values=[counts.get((b, g), 0) / totals[b] if totals[b] else 0.0 for b in xs],
        )
        for g in TYPE_GROUPS
    ]
    return TimeSeries(bucket=bucket, x=bucket_labels(xs), series=series, unit="ratio")


def reels_exchange(
    msgs: pl.LazyFrame,
    ref: PersonRefFn,
    bucket: Bucket = "month",
    people: Sequence[int] | None = None,
) -> TimeSeries:
    """Reels + posts shared per person per bucket (zero-filled)."""
    df = (
        msgs.filter(pl.col("share_kind").cast(pl.String).is_in(["reel", "post"]))
        .select(bucket_start(pl.col("local_date"), bucket).alias("b"), "sender_id")
        .group_by("b", "sender_id")
        .agg(pl.len().alias("n"))
        .collect()
    )
    span = _date_span(df, "b")
    if span is None:
        return TimeSeries(bucket=bucket, x=[], series=[], unit="count")
    xs = bucket_range(span[0], span[1], bucket)
    counts = {(b, int(s)): int(n) for b, s, n in df.iter_rows()}
    senders = sorted({int(s) for s in df["sender_id"].to_list()})
    order = [p for p in _ordered_people(senders, people) if p in senders]
    series = []
    for pid in order:
        p = ref(pid)
        series.append(
            Series(
                key=str(pid),
                name=p.name,
                person_id=pid,
                color_index=p.color_index,
                values=[float(counts.get((b, pid), 0)) for b in xs],
            )
        )
    return TimeSeries(bucket=bucket, x=bucket_labels(xs), series=series, unit="count")


#: Max message ids in an anonymized drill-down.
MAX_DRILL_IDS = 500


def top_shared_accounts(
    msgs: pl.LazyFrame, chat_id: int, limit: int = 10, *, anonymize: bool = False
) -> list[RankedItem]:
    """Most shared accounts (``share_owner``), count desc then name.

    ``anonymize``: account names become ``Account 1``, ``Account 2``… (by rank) and the
    drill-down lists the message ids instead of the account name.
    """
    df = (
        msgs.filter(pl.col("share_owner").is_not_null() & (pl.col("share_owner") != ""))
        .group_by("share_owner")
        .agg(pl.len().alias("n"), pl.col("message_id").sort().head(MAX_DRILL_IDS).alias("ids"))
        .sort(["n", "share_owner"], descending=[True, False])
        .head(limit)
        .collect()
    )
    out: list[RankedItem] = []
    for i, (owner, n, ids) in enumerate(df.iter_rows(), start=1):
        if anonymize:
            key = f"Account {i}"
            drill = MessageQuery(thread_id=chat_id, message_ids=[int(m) for m in ids])
        else:
            key = owner
            drill = MessageQuery(thread_id=chat_id, share_owner=owner)
        out.append(RankedItem(key=key, count=int(n), drilldown=drill))
    return out


def link_domain_counts(msgs: pl.LazyFrame) -> dict[str, int]:
    """Domains of URLs in text plus external shares' links."""
    df = msgs.select(
        "links",
        pl.when(pl.col("share_kind") == "external")
        .then(pl.col("share_link"))
        .otherwise(None)
        .alias("ext"),
    ).collect()
    counts: dict[str, int] = {}
    urls: list[str] = [u for lst in df["links"].to_list() for u in (lst or [])]
    urls.extend(u for u in df["ext"].to_list() if u)
    for url in urls:
        domain = textproc.url_domain(url)
        if domain:
            counts[domain] = counts.get(domain, 0) + 1
    return counts


def top_link_domains(msgs: pl.LazyFrame, chat_id: int, limit: int = 10) -> list[RankedItem]:
    counts = link_domain_counts(msgs)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return [
        RankedItem(key=d, count=n, drilldown=MessageQuery(thread_id=chat_id, link_domain=d))
        for d, n in ranked
    ]


def voice_notes(
    msgs: pl.LazyFrame,
    media: pl.LazyFrame,
    ref: PersonRefFn,
    chat_id: int,
    durations: dict[int, float],
    status: DurationStatus,
    people: Sequence[int] | None = None,
) -> VoiceNotesSummary:
    """Voice-note counts (``n_audios``) and durations (seconds → ms) per person."""
    counts = (
        msgs.group_by("sender_id")
        .agg(pl.col("n_audios").cast(pl.Int64).sum().alias("n"))
        .filter(pl.col("n") > 0)
        .collect()
    )
    count_by = {int(s): int(n) for s, n in counts.iter_rows()}
    audio = (
        media.filter(pl.col("kind") == "audio")
        .join(msgs.select("message_id", "sender_id"), on="message_id", how="inner")
        .select("media_id", "sender_id")
        .collect()
    )
    dur_by: dict[int, list[float]] = {}
    for media_id, sender in audio.iter_rows():
        sec = durations.get(int(media_id))
        if sec is not None:
            dur_by.setdefault(int(sender), []).append(sec)
    per_person: list[VoiceNoteStats] = []
    for pid in _ordered_people(sorted(count_by), people):
        if pid not in count_by:
            continue
        ds_ = dur_by.get(pid, [])
        per_person.append(
            VoiceNoteStats(
                person=ref(pid),
                count=count_by[pid],
                with_duration=len(ds_),
                total_ms=round(sum(ds_) * 1000) if ds_ else None,
                avg_ms=sum(ds_) * 1000 / len(ds_) if ds_ else None,
                drilldown=MessageQuery(thread_id=chat_id, sender_id=pid, types=["audio"]),
            )
        )
    all_d = [d for v in dur_by.values() for d in v]
    return VoiceNotesSummary(
        status=status,
        count=sum(count_by.values()),
        with_duration=len(all_d),
        total_ms=round(sum(all_d) * 1000) if all_d else None,
        avg_ms=sum(all_d) * 1000 / len(all_d) if all_d else None,
        per_person=per_person,
    )


def gallery(
    msgs: pl.LazyFrame,
    media: pl.LazyFrame,
    ref: PersonRefFn,
    *,
    kinds: Sequence[str] = GALLERY_KINDS,
    sender: int | None = None,
    year: int | None = None,
    cursor: str | None = None,
    limit: int = 60,
) -> MediaGallery:
    """Chronological page of media items. ``cursor`` = opaque offset from the previous
    page's ``next_cursor``."""
    lf = (
        media.filter(pl.col("kind").cast(pl.String).is_in(list(kinds)))
        .join(
            msgs.select("message_id", "sender_id", "ts", "local_date"),
            on="message_id",
            how="inner",
        )
        .with_columns(pl.col("local_date").dt.year().alias("year"))
    )
    if sender is not None:
        lf = lf.filter(pl.col("sender_id") == sender)
    years = sorted(int(y) for y in lf.select(pl.col("year").unique()).collect()["year"].to_list())
    if year is not None:
        lf = lf.filter(pl.col("year") == year)
    df = lf.sort(["message_id", "media_id"]).collect()
    try:
        offset = max(0, int(cursor)) if cursor else 0
    except ValueError:
        offset = 0
    page = df.slice(offset, limit)
    items = [
        MediaItem(
            media_id=int(r["media_id"]),
            message_id=int(r["message_id"]),
            kind=r["kind"],
            sender=ref(int(r["sender_id"])),
            ts=r["ts"],
            exists=bool(r["exists"]),
            url=f"/api/media/{r['media_id']}" if r["exists"] else None,
            thumb_url=f"/api/media/{r['media_id']}/thumb" if r["exists"] else None,
        )
        for r in page.iter_rows(named=True)
    ]
    end = offset + page.height
    return MediaGallery(
        items=items,
        total=df.height,
        next_cursor=str(end) if end < df.height else None,
        years=years,
    )
