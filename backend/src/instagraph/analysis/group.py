"""Group chats (spec §7.2 Group tab) and the Overview social graph (§7.1 chart 16).

Definitions
-----------
* **Members** = thread participants plus everyone who sent a message in scope.
* **Lurkers** = members with < 1% of the group's messages (0 messages included).
* **Who replies to whom**: a counted message with ``reply_latency_ms`` set (first
  message of a turn that came within the reply window, §6.4) is a reply from its sender
  to the sender of the previous counted message in the thread. Computed on messages
  without the ``people`` filter (so the predecessor is never filtered away), then the
  edges are restricted to pairs touching the filtered people.
* **Who reacts to whom**: reaction ``actor_id`` → reacted message's author.
* **Membership**: ``system_event`` join/leave/removed; the payload (member name) is
  matched to people by display name / alias (case- and accent-insensitive). A member
  whose first event is a leave/removal (or who has no events) is considered a member
  since before the first message.
* **Mentions**: ``@token`` in text matched to members by normalized full name, alias,
  or (when unambiguous) first name; normalization = lowercase, accents stripped, only
  letters/digits kept.
* **Social graph**: nodes = people (owner excluded by default), size = messages sent in
  scope; an edge joins two people who are both members of a group chat in scope,
  weight = number of such shared group chats.
"""

import itertools
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime
from typing import Any

import polars as pl

from instagraph import textproc
from instagraph.api.filters import bucket_labels, bucket_range, bucket_start, is_counted_message
from instagraph.schemas.common import (
    Bucket,
    CategoryChart,
    HeatmapData,
    MessageQuery,
    PersonRef,
    Series,
    TimeSeries,
)
from instagraph.schemas.group import (
    ChordLink,
    GraphEdge,
    GraphNode,
    GroupEvent,
    InteractionMatrix,
    MembershipInterval,
    MemberStats,
    MemberTimeline,
)

PersonRefFn = Callable[[int], PersonRef]
TextFn = Callable[[str | None], str | None]

LURKER_SHARE = 0.01
MEMBERSHIP_EVENTS = ("join", "leave", "removed")
HISTORY_EVENTS = ("rename", "photo_change", "theme_change", "emoji_change")
ACTIVITY_TOP_N = 10
_NON_ALNUM = re.compile(r"[\W_]+", re.UNICODE)


def normalize_name(s: str) -> str:
    """Lowercase, accents stripped, letters/digits only (``"Jana Nováková"`` →
    ``"jananovakova"``)."""
    return _NON_ALNUM.sub("", textproc.strip_accents(s.casefold()))


# --------------------------------------------------------------------------------------
# Members & leaderboard
# --------------------------------------------------------------------------------------


def member_stats(
    msgs: pl.LazyFrame,
    reactions: pl.LazyFrame,
    ref: PersonRefFn,
    chat_id: int,
    participants: Sequence[int],
) -> list[MemberStats]:
    """Leaderboard rows sorted by messages desc (then person_id)."""
    agg = (
        msgs.filter(is_counted_message())
        .group_by("sender_id")
        .agg(
            pl.len().alias("messages"),
            pl.col("word_count").cast(pl.Int64).sum().alias("words"),
            (
                pl.col("n_photos").cast(pl.Int64)
                + pl.col("n_videos").cast(pl.Int64)
                + pl.col("n_audios").cast(pl.Int64)
                + pl.col("n_gifs").cast(pl.Int64)
                + pl.col("n_stickers").cast(pl.Int64)
            )
            .sum()
            .alias("media"),
        )
        .collect()
    )
    stats = {int(r["sender_id"]): r for r in agg.iter_rows(named=True)}
    received = {
        int(t): int(n)
        for t, n in reactions.group_by("target_id").agg(pl.len()).collect().iter_rows()
    }
    total = sum(int(r["messages"]) for r in stats.values())
    ids = set(participants) | set(stats)
    rows = [
        MemberStats(
            person=ref(pid),
            messages=int(stats.get(pid, {}).get("messages", 0)),
            share=int(stats.get(pid, {}).get("messages", 0)) / total if total else 0.0,
            words=int(stats.get(pid, {}).get("words", 0)),
            reactions_received=received.get(pid, 0),
            media=int(stats.get(pid, {}).get("media", 0)),
            drilldown=MessageQuery(thread_id=chat_id, sender_id=pid),
        )
        for pid in ids
    ]
    rows.sort(key=lambda m: (-m.messages, m.person.person_id))
    return rows


def leaderboard_chart(members: Sequence[MemberStats]) -> CategoryChart:
    fields = (
        ("messages", "Messages"),
        ("words", "Words"),
        ("reactions_received", "Reactions received"),
        ("media", "Media"),
    )
    return CategoryChart(
        categories=[m.person.name for m in members],
        series=[
            Series(key=k, name=label, values=[float(getattr(m, k)) for m in members])
            for k, label in fields
        ],
        unit="count",
        drilldowns=[m.drilldown for m in members],
    )


def lurkers(members: Sequence[MemberStats]) -> list[MemberStats]:
    if sum(m.messages for m in members) == 0:
        return []
    return [m for m in members if m.share < LURKER_SHARE]


# --------------------------------------------------------------------------------------
# Interaction matrices
# --------------------------------------------------------------------------------------


def reply_pairs(msgs_without_people_filter: pl.LazyFrame) -> dict[tuple[int, int], int]:
    """``(replier, replied_to) -> count`` (see module docstring)."""
    df = (
        msgs_without_people_filter.filter(is_counted_message())
        .sort("message_id")
        .with_columns(pl.col("sender_id").shift(1).over("thread_id").alias("prev_sender"))
        .filter(
            pl.col("reply_latency_ms").is_not_null()
            & pl.col("prev_sender").is_not_null()
            & (pl.col("prev_sender") != pl.col("sender_id"))
        )
        .group_by("sender_id", "prev_sender")
        .agg(pl.len().alias("n"))
        .collect()
    )
    return {(int(a), int(b)): int(n) for a, b, n in df.iter_rows()}


def reaction_pairs(reactions: pl.LazyFrame) -> dict[tuple[int, int], int]:
    """``(actor, message author) -> count``."""
    df = reactions.group_by("actor_id", "target_id").agg(pl.len().alias("n")).collect()
    return {(int(a), int(b)): int(n) for a, b, n in df.iter_rows()}


def interaction_matrix(
    pairs: Mapping[tuple[int, int], int],
    order: Sequence[int],
    ref: PersonRefFn,
    people_filter: Iterable[int] | None = None,
) -> InteractionMatrix:
    """Square matrix over ``order`` (+ anyone else appearing in ``pairs``)."""
    allowed = set(people_filter) if people_filter is not None else None
    kept = {
        k: v
        for k, v in pairs.items()
        if v and (allowed is None or k[0] in allowed or k[1] in allowed)
    }
    ids = list(order)
    ids.extend(sorted({p for k in kept for p in k if p not in ids}))
    pos = {p: i for i, p in enumerate(ids)}
    values = [[0.0] * len(ids) for _ in ids]
    for (a, b), n in kept.items():
        values[pos[a]][pos[b]] += n
    refs = [ref(p) for p in ids]
    return InteractionMatrix(
        people=refs,
        matrix=HeatmapData(
            x_labels=[r.name for r in refs],
            y_labels=[r.name for r in refs],
            values=values,
            unit="count",
        ),
        links=[
            ChordLink(source=a, target=b, value=n)
            for (a, b), n in sorted(kept.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
    )


# --------------------------------------------------------------------------------------
# Activity stream
# --------------------------------------------------------------------------------------


def activity_stream(
    msgs: pl.LazyFrame, ref: PersonRefFn, bucket: Bucket = "month", top_n: int = ACTIVITY_TOP_N
) -> TimeSeries:
    """Messages per member per bucket; members beyond ``top_n`` are summed as "others"."""
    df = (
        msgs.filter(is_counted_message())
        .select(bucket_start(pl.col("local_date"), bucket).alias("b"), "sender_id")
        .group_by("b", "sender_id")
        .agg(pl.len().alias("n"))
        .collect()
    )
    if df.is_empty():
        return TimeSeries(bucket=bucket, x=[], series=[], unit="count")
    lo, hi = df["b"].min(), df["b"].max()
    assert isinstance(lo, date) and isinstance(hi, date)
    xs = bucket_range(lo, hi, bucket)
    totals: dict[int, int] = {}
    counts: dict[tuple[date, int], int] = {}
    for b, s, n in df.iter_rows():
        totals[int(s)] = totals.get(int(s), 0) + int(n)
        counts[(b, int(s))] = int(n)
    ranked = sorted(totals, key=lambda p: (-totals[p], p))
    top, rest = ranked[:top_n], ranked[top_n:]
    series = []
    for pid in top:
        r = ref(pid)
        series.append(
            Series(
                key=str(pid),
                name=r.name,
                person_id=pid,
                color_index=r.color_index,
                values=[float(counts.get((x, pid), 0)) for x in xs],
            )
        )
    if rest:
        series.append(
            Series(
                key="others",
                name="Others",
                values=[float(sum(counts.get((x, p), 0) for p in rest)) for x in xs],
            )
        )
    return TimeSeries(bucket=bucket, x=bucket_labels(xs), series=series, unit="count")


# --------------------------------------------------------------------------------------
# Events: membership and history
# --------------------------------------------------------------------------------------


def name_index(names: Mapping[int, Sequence[str]], prefer: Iterable[int] = ()) -> dict[str, int]:
    """Normalized name → person_id. Persons in ``prefer`` win on conflicts."""
    preferred = set(prefer)
    index: dict[str, int] = {}
    for pid in sorted(names, key=lambda p: (p not in preferred, p)):
        for n in names[pid]:
            key = normalize_name(n)
            if key:
                index.setdefault(key, pid)
    return index


def system_events(events: pl.LazyFrame, kinds: Sequence[str]) -> list[dict[str, Any]]:
    return (
        events.filter(
            (pl.col("type") == "system") & pl.col("system_event").cast(pl.String).is_in(kinds)
        )
        .sort("message_id")
        .select("message_id", "ts", "sender_id", "system_event", "system_payload")
        .collect()
        .to_dicts()
    )


def build_events(
    rows: Sequence[Mapping[str, Any]],
    ref: PersonRefFn,
    text: TextFn,
    index: Mapping[str, int],
    match_member: bool,
) -> list[GroupEvent]:
    out = []
    for r in rows:
        payload = r["system_payload"]
        member = None
        if match_member and payload:
            pid = index.get(normalize_name(str(payload)))
            member = ref(pid) if pid is not None else None
        out.append(
            GroupEvent(
                message_id=int(r["message_id"]),
                ts=r["ts"],
                event=r["system_event"],
                actor=ref(int(r["sender_id"])),
                member=member,
                value=text(payload),
            )
        )
    return out


def membership_intervals(
    events: Sequence[GroupEvent], members: Sequence[int], ref: PersonRefFn
) -> list[MemberTimeline]:
    """Gantt bars per member (see module docstring)."""
    by_member: dict[int, list[GroupEvent]] = {}
    for e in events:
        if e.member is not None:
            by_member.setdefault(e.member.person_id, []).append(e)
    ids = list(members) + sorted(p for p in by_member if p not in members)
    out: list[MemberTimeline] = []
    for pid in ids:
        intervals: list[MembershipInterval] = []
        start: datetime | None = None
        is_in = True  # assumed member since before the first message …
        evs = by_member.get(pid, [])
        if evs and evs[0].event == "join":
            is_in = False  # … unless the first thing we see is them joining
        for e in evs:
            if e.event == "join" and not is_in:
                start, is_in = e.ts, True
            elif e.event in ("leave", "removed") and is_in:
                intervals.append(MembershipInterval(start=start, end=e.ts))
                is_in = False
        if is_in:
            intervals.append(MembershipInterval(start=start, end=None))
        out.append(MemberTimeline(person=ref(pid), intervals=intervals))
    return out


# --------------------------------------------------------------------------------------
# Mentions
# --------------------------------------------------------------------------------------


def mention_index(names: Mapping[int, Sequence[str]]) -> dict[str, int]:
    """Mention token key → member: full names / aliases, then unambiguous first names."""
    index: dict[str, int] = {}
    for pid in sorted(names):
        for n in names[pid]:
            key = normalize_name(n)
            if key:
                index.setdefault(key, pid)
    first: dict[str, set[int]] = {}
    for pid, ns in names.items():
        for n in ns:
            parts = n.split()
            if parts:
                key = normalize_name(parts[0])
                if key:
                    first.setdefault(key, set()).add(pid)
    for key, pids in first.items():
        if len(pids) == 1 and key not in index:
            index[key] = next(iter(pids))
    return index


def mention_counts(msgs: pl.LazyFrame, index: Mapping[str, int]) -> dict[int, int]:
    texts = (
        msgs.filter(is_counted_message() & pl.col("text").str.contains("@", literal=True))
        .select("text")
        .collect()["text"]
        .to_list()
    )
    counts: dict[int, int] = {}
    for t in texts:
        for token in textproc.extract_mentions(t):
            pid = index.get(normalize_name(token))
            if pid is not None:
                counts[pid] = counts.get(pid, 0) + 1
    return counts


def mentions_chart(counts: Mapping[int, int], ref: PersonRefFn, chat_id: int) -> CategoryChart:
    ranked = sorted(counts, key=lambda p: (-counts[p], p))
    return CategoryChart(
        categories=[ref(p).name for p in ranked],
        series=[Series(key="mentions", name="Mentions", values=[float(counts[p]) for p in ranked])],
        unit="count",
        drilldowns=[MessageQuery(thread_id=chat_id, text="@") for _ in ranked],
    )


# --------------------------------------------------------------------------------------
# Social graph
# --------------------------------------------------------------------------------------


def social_graph(
    msgs: pl.LazyFrame,
    threads: pl.DataFrame,
    ref: PersonRefFn,
    *,
    owner_id: int | None,
    include_owner: bool = False,
    max_nodes: int = 150,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Nodes and edges of the shared-group-chat network (see module docstring).

    ``msgs`` = filtered messages (all chats in scope); ``threads`` = threads table.
    """
    counted = msgs.filter(is_counted_message())
    sent = {
        int(s): int(n) for s, n in counted.group_by("sender_id").agg(pl.len()).collect().iter_rows()
    }
    active = counted.group_by("thread_id").agg(pl.col("sender_id").unique()).collect()
    senders_by_thread = {int(t): [int(s) for s in ss] for t, ss in active.iter_rows()}
    groups = threads.filter(
        pl.col("is_group") & pl.col("thread_id").is_in(list(senders_by_thread))
    ).select("thread_id", "participants")
    excluded = {owner_id} if (owner_id is not None and not include_owner) else set()

    group_count: dict[int, int] = {}
    shared: dict[tuple[int, int], list[int]] = {}
    for tid, participants in groups.iter_rows():
        members = sorted(
            (set(participants or []) | set(senders_by_thread.get(int(tid), []))) - excluded
        )
        for p in members:
            group_count[p] = group_count.get(p, 0) + 1
        for a, b in itertools.combinations(members, 2):
            shared.setdefault((a, b), []).append(int(tid))

    candidates = (set(sent) | set(group_count)) - excluded
    ranked = sorted(candidates, key=lambda p: (-sent.get(p, 0), -group_count.get(p, 0), p))
    kept = set(ranked[:max_nodes])
    nodes = [
        GraphNode(person=ref(p), messages=sent.get(p, 0), group_chats=group_count.get(p, 0))
        for p in ranked[:max_nodes]
    ]
    edges = [
        GraphEdge(
            source=a,
            target=b,
            weight=len(tids),
            thread_ids=sorted(tids),
            drilldown=MessageQuery(thread_ids=sorted(tids)),
        )
        for (a, b), tids in sorted(shared.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        if a in kept and b in kept
    ]
    return nodes, edges
