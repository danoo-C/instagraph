"""``GET /api/people`` (list) and ``GET /api/people/{person_id}`` (person page, §5.7).

* Threads in common = chats passing the thread filters whose members (participants +
  senders in scope) include both the person and the owner.
* Messages exchanged = their counted messages + my messages in our 1:1 chat(s); for the
  owner it is every counted message in scope.
* First/last contact = earliest/latest of their messages and my messages in our 1:1.
* The global ``people`` filter restricts which people are LISTED; numbers are always
  computed with every sender so context (replies, 1:1 counts) stays correct.
* ``GET /api/people/{owner_id}`` returns the "Me" page: my habits across all chats.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Annotated

import polars as pl
from fastapi import APIRouter, Query

from instagraph.analysis.person_metrics import (
    hour_shares,
    metrics_by_person,
    person_metrics,
    weekday_shares,
)
from instagraph.api.deps import AnalysisContext, Context
from instagraph.api.filters import (
    allowed_thread_ids,
    bucket_labels,
    bucket_range,
    filtered_reactions,
    is_counted_message,
)
from instagraph.api.routes._insights_common import ensure_person, scoped_frames
from instagraph.errors import ERROR_RESPONSES
from instagraph.schemas.common import (
    CategoryChart,
    MessageQuery,
    PersonRef,
    Scalar,
    Series,
    Tile,
    TimeSeries,
    Unit,
)
from instagraph.schemas.people import (
    EmojiCount,
    PeopleListResponse,
    PeopleSort,
    PersonContact,
    PersonDetailResponse,
    PersonListItem,
    PersonStatRow,
    PersonThread,
)
from instagraph.textproc import normalize_emoji_expr, strip_accents

router = APIRouter(tags=["people"], responses=ERROR_RESPONSES)

TOP_EMOJIS = 10
TOP_CONTACTS = 10
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

STAT_ROWS: tuple[tuple[str, str, Unit, bool], ...] = (
    ("messages", "Messages", "count", False),
    ("words", "Words", "count", False),
    ("avg_words", "Avg words / message", "per_message", False),
    ("emojis_per_message", "Emojis / message", "per_message", False),
    ("reply_median_ms", "Median reply time", "ms", True),
    ("reply_rate", "Reply rate", "ratio", False),
    ("initiation_rate", "Initiation rate", "ratio", False),
    ("msgs_per_turn", "Messages / turn", "per_message", False),
    ("night_share", "Night owl (00–05)", "ratio", False),
    ("early_share", "Early bird (05–08)", "ratio", False),
    ("question_rate", "Question rate", "ratio", False),
    ("laugh_rate", "Laughter rate", "ratio", False),
    ("laughs_caused", "Laughs caused", "count", False),
    ("reactions_given", "Reactions given", "count", False),
    ("reactions_received", "Reactions received", "count", False),
    ("reactions_per_message", "Reactions / message", "per_message", False),
    ("photos", "Photos", "count", False),
    ("videos", "Videos", "count", False),
    ("voice_notes", "Voice notes", "count", False),
    ("reels_posts", "Reels & posts", "count", False),
    ("calls_started", "Calls started", "count", False),
    ("unsent", "Unsent", "count", False),
    ("mattr", "Lexical diversity (MATTR)", "ratio", False),
)


# --------------------------------------------------------------------------------------
# Shared computation
# --------------------------------------------------------------------------------------


@dataclass
class _Base:
    """Everything the list and the detail page share (cached per filters)."""

    counted: pl.DataFrame  # thread_id, sender_id, ts of counted messages in scope
    members: dict[int, set[int]]  # thread -> participants + senders (allowed threads)
    is_group: dict[int, bool]
    category: dict[int, str]
    one_to_one: dict[int, int] = field(default_factory=dict)  # thread -> other person


def _base(ctx: AnalysisContext) -> _Base:
    def compute() -> _Base:
        f = ctx.filters.model_copy(update={"people": None})
        msgs, _ = scoped_frames(ctx, f)
        counted = msgs.filter(is_counted_message()).select("thread_id", "sender_id", "ts").collect()
        allowed = set(allowed_thread_ids(ctx.dataset, f))
        threads = ctx.dataset.threads_df().filter(pl.col("thread_id").is_in(list(allowed)))
        members: dict[int, set[int]] = {}
        is_group: dict[int, bool] = {}
        category: dict[int, str] = {}
        for tid, parts, grp, cat in threads.select(
            "thread_id", "participants", "is_group", "category"
        ).iter_rows():
            members[int(tid)] = {int(p) for p in parts or []}
            is_group[int(tid)] = bool(grp)
            category[int(tid)] = str(cat)
        for tid, sid in counted.select("thread_id", "sender_id").unique().iter_rows():
            members.setdefault(int(tid), set()).add(int(sid))
        owner = ctx.owner_id
        one_to_one = {
            tid: next(iter(m - {owner}))
            for tid, m in members.items()
            if not is_group.get(tid, False) and owner in m and len(m - {owner}) == 1
        }
        return _Base(counted, members, is_group, category, one_to_one)

    return ctx.cached("people_base", compute)


def _shared_threads(base: _Base, pid: int, owner: int | None) -> list[int]:
    if pid == owner or owner is None:
        return sorted(t for t, m in base.members.items() if pid in m)
    return sorted(t for t, m in base.members.items() if pid in m and owner in m)


@dataclass(frozen=True)
class _Row:
    pid: int
    sent: int
    exchanged: int
    threads: int
    one_to_one: int
    groups: int
    first: datetime | None
    last: datetime | None


def _rows(ctx: AnalysisContext) -> list[_Row]:
    def compute() -> list[_Row]:
        base = _base(ctx)
        owner = ctx.owner_id
        c = base.counted
        per_sender = {
            int(s): (int(n), a, b)
            for s, n, a, b in c.group_by("sender_id")
            .agg(pl.len(), pl.col("ts").min().alias("a"), pl.col("ts").max().alias("b"))
            .iter_rows()
        }
        mine_by_thread = {
            int(t): (int(n), a, b)
            for t, n, a, b in c.filter(pl.col("sender_id") == owner)
            .group_by("thread_id")
            .agg(pl.len(), pl.col("ts").min().alias("a"), pl.col("ts").max().alias("b"))
            .iter_rows()
        }
        candidates: set[int] = set(per_sender)
        for m in base.members.values():
            candidates |= m
        known = set(ctx.dataset.people_df()["person_id"].to_list())
        rows: list[_Row] = []
        for pid in sorted(candidates & known):
            sent, first, last = per_sender.get(pid, (0, None, None))
            shared = _shared_threads(base, pid, owner)
            n_1to1 = sum(1 for t in shared if not base.is_group.get(t, False))
            if pid == owner:
                exchanged = c.height
                if c.height:
                    first, last = c["ts"].min(), c["ts"].max()
            else:
                exchanged = sent
                times = [t for t in (first, last) if t is not None]
                for tid, other in base.one_to_one.items():
                    if other == pid and tid in mine_by_thread:
                        n, a, b = mine_by_thread[tid]
                        exchanged += n
                        times += [a, b]
                if times:
                    first, last = min(times), max(times)
            rows.append(
                _Row(
                    pid=pid,
                    sent=sent,
                    exchanged=exchanged,
                    threads=len(shared),
                    one_to_one=n_1to1,
                    groups=len(shared) - n_1to1,
                    first=first,
                    last=last,
                )
            )
        return rows

    return ctx.cached("people_rows", compute)


def _norm(s: str) -> str:
    return strip_accents(s).casefold()


# --------------------------------------------------------------------------------------
# List
# --------------------------------------------------------------------------------------


@router.get("/api/people", response_model=PeopleListResponse)
def list_people(
    ctx: Context,
    q: Annotated[str | None, Query(description="Name search (accent-insensitive)")] = None,
    sort: PeopleSort = "exchanged",
    order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> PeopleListResponse:
    """Everyone in a chat passing the filters, with totals, threads in common and last
    contact. Sortable, searchable, paginated."""

    def compute() -> PeopleListResponse:
        refs = ctx.person_refs()
        people = ctx.dataset.people_df()
        deleted = dict(
            zip(
                people["person_id"].to_list(),
                people["is_deleted_account"].to_list(),
                strict=True,
            )
        )
        aliases = dict(zip(people["person_id"].to_list(), people["aliases"].to_list(), strict=True))
        rows = _rows(ctx)
        if ctx.filters.people is not None:
            sel = set(ctx.filters.people)
            rows = [r for r in rows if r.pid in sel]
        if q and q.strip():
            needle = _norm(q.strip())

            def matches(r: _Row) -> bool:
                names = [ctx.person_ref(r.pid).name]
                if not ctx.settings.anonymize:
                    names += list(aliases.get(r.pid) or [])
                return any(needle in _norm(n) for n in names)

            rows = [r for r in rows if matches(r)]
        desc = order == "desc"
        if sort == "name":
            rows = sorted(rows, key=lambda r: _norm(ctx.person_ref(r.pid).name), reverse=desc)
        elif sort == "last_contact":
            missing = [r for r in rows if r.last is None]
            present = sorted(
                (r for r in rows if r.last is not None),
                key=lambda r: (r.last, -r.pid),
                reverse=desc,
            )
            rows = present + missing
        else:
            attr = {"exchanged": "exchanged", "messages": "sent", "threads": "threads"}[sort]
            rows = sorted(
                rows,
                key=lambda r: (getattr(r, attr), -r.pid if desc else r.pid),
                reverse=desc,
            )
        page = rows[offset : offset + limit]
        return PeopleListResponse(
            dataset_version=ctx.dataset_version,
            total=len(rows),
            offset=offset,
            limit=limit,
            items=[
                PersonListItem(
                    person=refs.get(r.pid) or ctx.person_ref(r.pid),
                    is_deleted_account=bool(deleted.get(r.pid, False)),
                    messages_sent=r.sent,
                    messages_exchanged=r.exchanged,
                    threads_in_common=r.threads,
                    one_to_one_threads=r.one_to_one,
                    group_threads=r.groups,
                    first_contact=r.first,
                    last_contact=r.last,
                    drilldown=MessageQuery(sender_id=r.pid),
                )
                for r in page
            ],
        )

    return ctx.cached("people_list", compute, extra=(q, sort, order, offset, limit))


# --------------------------------------------------------------------------------------
# Detail
# --------------------------------------------------------------------------------------


def _top_emojis(ctx: AnalysisContext, frame: pl.DataFrame) -> list[EmojiCount]:
    if frame.is_empty():
        return []
    s = ctx.settings
    df = (
        frame.select("emojis")
        .explode("emojis")
        .drop_nulls("emojis")
        .select(
            normalize_emoji_expr(
                pl.col("emojis"),
                merge_skin_tones=s.emoji_merge_skin_tones,
                merge_gendered=s.emoji_merge_gendered,
            ).alias("e")
        )
        .group_by("e")
        .agg(pl.len().alias("n"))
        .sort(["n", "e"], descending=[True, False])
        .head(TOP_EMOJIS)
    )
    return [EmojiCount(emoji=str(e), count=int(n)) for e, n in df.iter_rows()]


def _series(key: str, name: str, ref: PersonRef | None, values: list[float]) -> Series:
    return Series(
        key=key,
        name=name,
        person_id=ref.person_id if ref else None,
        color_index=ref.color_index if ref else None,
        values=[float(v) for v in values],
    )


def _monthly(frames: Sequence[tuple[str, str, PersonRef | None, pl.DataFrame]]) -> TimeSeries:
    counts: list[dict[date, int]] = []
    months: set[date] = set()
    for _, _, _, fr in frames:
        if fr.is_empty():
            counts.append({})
            continue
        df = (
            fr.group_by(pl.col("local_date").dt.truncate("1mo").alias("m"))
            .agg(pl.len().alias("n"))
            .iter_rows()
        )
        d = {m: int(n) for m, n in df}
        counts.append(d)
        months |= set(d)
    if not months:
        return TimeSeries(bucket="month", x=[], series=[], unit="count")
    x = bucket_range(min(months), max(months), "month")
    series = [
        _series(key, name, ref, [float(c.get(m, 0)) for m in x])
        for (key, name, ref, _), c in zip(frames, counts, strict=True)
    ]
    return TimeSeries(bucket="month", x=bucket_labels(x), series=series, unit="count")


def _shares_chart(
    categories: list[str], frames: Sequence[tuple[str, str, PersonRef | None, list[float]]]
) -> CategoryChart:
    return CategoryChart(
        categories=categories,
        unit="ratio",
        series=[_series(k, n, r, v) for k, n, r, v in frames],
    )


def _stat_rows(pm: dict[int, dict[str, object]], pid: int, me: int | None) -> list[PersonStatRow]:
    def val(p: int | None, key: str) -> Scalar:
        if p is None:
            return None
        v = pm.get(p, {}).get(key)
        return v if isinstance(v, int | float) else None

    return [
        PersonStatRow(
            key=k,
            label=label,
            unit=unit,
            lower_is_better=low,
            person_value=val(pid, k),
            my_value=val(me, k),
        )
        for k, label, unit, low in STAT_ROWS
    ]


@router.get("/api/people/{person_id}", response_model=PersonDetailResponse)
def person_detail(person_id: int, ctx: Context) -> PersonDetailResponse:
    """One person across all chats they share with me (or the "Me" page for the owner)."""
    ensure_person(ctx, person_id)

    def compute() -> PersonDetailResponse:
        base = _base(ctx)
        owner = ctx.owner_id
        is_me = person_id == owner
        shared = _shared_threads(base, person_id, owner)
        f = ctx.filters.model_copy(update={"people": None})
        msgs, _ = scoped_frames(ctx, f)
        scope_lf = msgs.filter(pl.col("thread_id").is_in(shared))
        scope = scope_lf.filter(is_counted_message()).collect()
        rx_lf = filtered_reactions(ctx.dataset, f, messages=scope_lf)
        pm = metrics_by_person(
            person_metrics(scope_lf, rx_lf, laughter_patterns=ctx.settings.laughter_patterns)
        )
        ref = ctx.person_ref(person_id)
        theirs = scope.filter(pl.col("sender_id") == person_id)
        mine = scope.filter(pl.col("sender_id") == owner) if owner is not None else scope.clear()
        people = ctx.dataset.people_df().filter(pl.col("person_id") == person_id)
        deleted = bool(people["is_deleted_account"][0])
        row = next((r for r in _rows(ctx) if r.pid == person_id), None)
        rx = rx_lf.select("actor_id", "target_id").collect()

        # Threads
        by_thread: dict[int, Counter[str]] = {}
        last_by_thread: dict[int, datetime] = {}
        for tid, sid, n, last in (
            scope.group_by("thread_id", "sender_id").agg(pl.len(), pl.col("ts").max()).iter_rows()
        ):
            c = by_thread.setdefault(int(tid), Counter())
            c["all"] += int(n)
            if sid == person_id:
                c["theirs"] += int(n)
            if sid == owner:
                c["mine"] += int(n)
            prev = last_by_thread.get(int(tid))
            last_by_thread[int(tid)] = last if prev is None or last > prev else prev
        threads = [
            PersonThread(
                thread_id=t,
                title=ctx.thread_title(t),
                is_group=base.is_group.get(t, False),
                category=base.category.get(t, "inbox"),  # type: ignore[arg-type]
                messages=by_thread.get(t, Counter())["all"],
                their_messages=by_thread.get(t, Counter())["theirs"],
                my_messages=by_thread.get(t, Counter())["mine"],
                last_ts=last_by_thread.get(t),
                drilldown=MessageQuery(thread_id=t),
            )
            for t in shared
        ]
        threads.sort(key=lambda t: (-t.messages, t.thread_id))

        words = int(theirs["word_count"].sum()) if theirs.height else 0
        emojis = int(theirs["emojis"].list.len().sum()) if theirs.height else 0
        n_1to1 = sum(1 for t in shared if not base.is_group.get(t, False))
        tiles: list[Tile] = [
            Tile(
                key="messages_sent",
                label="Messages sent" if not is_me else "Messages I sent",
                value=theirs.height,
                unit="count",
                person=ref,
                drilldown=MessageQuery(sender_id=person_id),
            ),
        ]
        top_contacts: list[PersonContact] | None = None
        if is_me:
            received = scope.height - theirs.height
            others = {int(s) for s in scope["sender_id"].unique().to_list()} - {person_id}
            tiles += [
                Tile(key="messages_received", label="Messages received", value=received,
                     unit="count"),
                Tile(key="chats", label="Chats", value=len(shared), unit="count"),
                Tile(key="people", label="People I talked with", value=len(others),
                     unit="count"),
                Tile(key="reactions_given", label="Reactions given",
                     value=int((rx["actor_id"] == person_id).sum()), unit="count"),
                Tile(key="reactions_received", label="Reactions received",
                     value=int(((rx["target_id"] == person_id)
                                & (rx["actor_id"] != person_id)).sum()), unit="count"),
            ]  # fmt: skip
            contacts = sorted(
                (r for r in _rows(ctx) if r.pid != person_id and r.exchanged > 0),
                key=lambda r: (-r.exchanged, r.pid),
            )[:TOP_CONTACTS]
            top_contacts = [
                PersonContact(
                    person=ctx.person_ref(r.pid),
                    messages_exchanged=r.exchanged,
                    drilldown=MessageQuery(sender_id=r.pid),
                )
                for r in contacts
            ]
        else:
            my_1to1 = [t for t, o in base.one_to_one.items() if o == person_id]
            mine_1to1 = mine.filter(pl.col("thread_id").is_in(my_1to1)).height
            tiles += [
                Tile(key="my_messages", label="Messages I sent them (1:1)", value=mine_1to1,
                     unit="count", drilldown=MessageQuery(sender_id=owner, thread_ids=my_1to1)),
                Tile(key="messages_exchanged", label="Messages exchanged",
                     value=row.exchanged if row else theirs.height, unit="count"),
                Tile(key="threads_in_common", label="Chats in common", value=len(shared),
                     unit="count", detail=f"{n_1to1} 1:1 · {len(shared) - n_1to1} groups"),
                Tile(key="reactions_to_me", label="Reactions they gave me",
                     value=int(((rx["actor_id"] == person_id)
                                & (rx["target_id"] == owner)).sum()), unit="count"),
                Tile(key="reactions_from_me", label="Reactions I gave them",
                     value=int(((rx["actor_id"] == owner)
                                & (rx["target_id"] == person_id)).sum()), unit="count"),
            ]  # fmt: skip
        tiles += [
            Tile(key="words", label="Words", value=words, unit="count", person=ref),
            Tile(key="emojis", label="Emojis", value=emojis, unit="count", person=ref),
            Tile(
                key="first_contact",
                label="First message",
                value=row.first.isoformat() if row and row.first else None,
                unit="datetime",
            ),
            Tile(
                key="last_contact",
                label="Last message",
                value=row.last.isoformat() if row and row.last else None,
                unit="datetime",
            ),
        ]

        me_ref = ctx.person_ref(owner) if owner is not None else None
        if is_me:
            others_frame = scope.filter(pl.col("sender_id") != person_id)
            activity = _monthly(
                [
                    (str(person_id), ref.name, ref, theirs),
                    ("received", "Received", None, others_frame),
                ]
            )
            hour_frames = [(str(person_id), ref.name, ref, hour_shares(theirs))]
            weekday_frames = [(str(person_id), ref.name, ref, weekday_shares(theirs))]
            stats = _stat_rows(pm, person_id, None)
        else:
            series = [(str(person_id), ref.name, ref, theirs)]
            hour_frames = [(str(person_id), ref.name, ref, hour_shares(theirs))]
            weekday_frames = [(str(person_id), ref.name, ref, weekday_shares(theirs))]
            if owner is not None and me_ref is not None:
                series.append((str(owner), me_ref.name, me_ref, mine))
                hour_frames.append((str(owner), me_ref.name, me_ref, hour_shares(mine)))
                weekday_frames.append((str(owner), me_ref.name, me_ref, weekday_shares(mine)))
            activity = _monthly(series)
            stats = _stat_rows(pm, person_id, owner)

        return PersonDetailResponse(
            dataset_version=ctx.dataset_version,
            person=ref,
            is_me=is_me,
            is_deleted_account=deleted,
            tiles=tiles,
            threads=threads,
            activity=activity,
            hours=_shares_chart([str(h) for h in range(24)], hour_frames),
            weekdays=_shares_chart(WEEKDAYS, weekday_frames),
            stats=stats,
            top_emojis=_top_emojis(ctx, theirs),
            top_contacts=top_contacts,
        )

    return ctx.cached("person_detail", compute, extra=person_id)
