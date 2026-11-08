"""``GET /api/export/{scope}.json`` — full stats summary download (spec §11).

``scope`` is ``all`` or ``chat-<thread_id>``. Respects the global filters. Message
text is included only with ``include_messages=true`` (and never when anonymized).
Computed directly from the dataset tables.
"""

import re
from datetime import UTC, datetime
from typing import Annotated, Any

import polars as pl
from fastapi import APIRouter, Query, Response

from instagraph.api.deps import AnalysisContext, Context
from instagraph.api.filters import is_counted_message
from instagraph.errors import ERROR_RESPONSES, NotFoundError
from instagraph.schemas.export import (
    ExportChat,
    ExportCount,
    ExportMessage,
    ExportPerson,
    ExportResponse,
    ExportTotals,
)
from instagraph.textproc import normalize_emoji_expr

router = APIRouter(prefix="/api/export", tags=["export"], responses=ERROR_RESPONSES)

_SCOPE_RE = re.compile(r"^(?:all|chat-(\d+))$")
TOP_N = 50


def _int(v: Any) -> int:
    return int(v) if v is not None else 0


def _chat(ctx: AnalysisContext, row: dict[str, Any], stats: dict[str, Any] | None) -> ExportChat:
    return ExportChat(
        thread_id=row["thread_id"],
        title=ctx.thread_title(row["thread_id"]),
        category=row["category"],
        is_group=bool(row["is_group"]),
        participants=len(row["participants"] or []),
        messages=_int(stats["n"]) if stats else 0,
        first_ts=stats["first"] if stats else None,
        last_ts=stats["last"] if stats else None,
    )


def _counts(df: pl.DataFrame, key: str) -> list[ExportCount]:
    return [
        ExportCount(key=str(k), count=int(n))
        for k, n in zip(df[key].to_list(), df["n"].to_list(), strict=True)
    ]


def build_export(
    ctx: AnalysisContext, scope: str, chat_id: int | None, include_messages: bool
) -> ExportResponse:
    ds = ctx.dataset
    threads = ds.threads_df()
    all_rows = ctx.messages()
    reactions = ctx.reactions()
    if chat_id is not None:
        all_rows = all_rows.filter(pl.col("thread_id") == chat_id)
        reactions = reactions.filter(pl.col("thread_id") == chat_id)
    rows = all_rows.collect()
    counted = rows.filter(is_counted_message())
    rx = reactions.select("message_id", "actor_id", "target_id", "emoji").collect()
    em = {
        "merge_skin_tones": ctx.settings.emoji_merge_skin_tones,
        "merge_gendered": ctx.settings.emoji_merge_gendered,
    }

    t = counted.select(
        pl.len().alias("messages"),
        pl.col("word_count").sum().alias("words"),
        pl.col("char_count").sum().alias("chars"),
        pl.col("n_photos").cast(pl.Int64).sum().alias("photos"),
        pl.col("n_videos").cast(pl.Int64).sum().alias("videos"),
        pl.col("n_audios").cast(pl.Int64).sum().alias("audios"),
        pl.col("n_gifs").cast(pl.Int64).sum().alias("gifs"),
        pl.col("n_stickers").cast(pl.Int64).sum().alias("stickers"),
        (pl.col("type") == "share").sum().alias("shares"),
        pl.col("links").list.len().sum().alias("links"),
        (pl.col("type") == "call").sum().alias("calls"),
        pl.col("call_duration_s")
        .filter(pl.col("type") == "call")
        .cast(pl.Int64)
        .sum()
        .alias("call_duration_s"),
        (pl.col("type") == "unsent").sum().alias("unsent"),
        pl.col("ts").min().alias("first_ts"),
        pl.col("ts").max().alias("last_ts"),
        pl.col("local_date").min().alias("first_date"),
        pl.col("local_date").max().alias("last_date"),
        pl.col("local_date").n_unique().alias("active_days"),
    ).row(0, named=True)
    first_date, last_date = t["first_date"], t["last_date"]
    totals = ExportTotals(
        messages=_int(t["messages"]),
        words=_int(t["words"]),
        chars=_int(t["chars"]),
        likes=int((rows["type"] == "like").sum()),
        unsent=_int(t["unsent"]),
        photos=_int(t["photos"]),
        videos=_int(t["videos"]),
        audios=_int(t["audios"]),
        gifs=_int(t["gifs"]),
        stickers=_int(t["stickers"]),
        shares=_int(t["shares"]),
        links=_int(t["links"]),
        calls=_int(t["calls"]),
        call_duration_s=_int(t["call_duration_s"]),
        reactions=rx.height,
        first_ts=t["first_ts"],
        last_ts=t["last_ts"],
        first_date=first_date,
        last_date=last_date,
        active_days=_int(t["active_days"]) if t["messages"] else 0,
        span_days=(last_date - first_date).days + 1 if first_date and last_date else 0,
    )

    per = counted.group_by("sender_id").agg(
        pl.len().alias("n"),
        pl.col("word_count").sum().alias("words"),
        pl.col("char_count").sum().alias("chars"),
        (
            pl.col("n_photos").cast(pl.Int64)
            + pl.col("n_videos")
            + pl.col("n_audios")
            + pl.col("n_gifs")
            + pl.col("n_stickers")
        )
        .sum()
        .alias("media"),
        pl.col("ts").min().alias("first"),
        pl.col("ts").max().alias("last"),
    )
    given = dict(rx.group_by("actor_id").len().iter_rows())
    received = dict(rx.group_by("target_id").len().iter_rows())
    people = [
        ExportPerson(
            person=ctx.person_ref(r["sender_id"]),
            messages=_int(r["n"]),
            words=_int(r["words"]),
            chars=_int(r["chars"]),
            media=_int(r["media"]),
            reactions_given=int(given.get(r["sender_id"], 0)),
            reactions_received=int(received.get(r["sender_id"], 0)),
            first_ts=r["first"],
            last_ts=r["last"],
        )
        for r in per.sort(["n", "sender_id"], descending=[True, False]).iter_rows(named=True)
    ]

    per_chat = {
        r["thread_id"]: r
        for r in counted.group_by("thread_id")
        .agg(
            pl.len().alias("n"), pl.col("ts").min().alias("first"), pl.col("ts").max().alias("last")
        )
        .iter_rows(named=True)
    }
    chat: ExportChat | None = None
    chats: list[ExportChat] = []
    if chat_id is not None:
        trow = threads.filter(pl.col("thread_id") == chat_id).row(0, named=True)
        chat = _chat(ctx, trow, per_chat.get(chat_id))
    else:
        for trow in threads.iter_rows(named=True):
            if trow["thread_id"] in per_chat:
                chats.append(_chat(ctx, trow, per_chat[trow["thread_id"]]))
        chats.sort(key=lambda c: (-c.messages, c.thread_id))

    types = rows.group_by(pl.col("type").cast(pl.String)).agg(pl.len().alias("n"))
    years = (
        counted.group_by(pl.col("local_date").dt.year().cast(pl.String).alias("year"))
        .agg(pl.len().alias("n"))
        .sort("year")
    )
    hours_map = dict(counted.group_by("hour").len().iter_rows())
    wd_map = dict(counted.group_by("weekday").len().iter_rows())
    emojis = (
        counted.select(pl.col("emojis").explode().alias("e"))
        .drop_nulls()
        .select(normalize_emoji_expr(pl.col("e"), **em).alias("e"))
        .group_by("e")
        .agg(pl.len().alias("n"))
        .sort(["n", "e"], descending=[True, False])
        .head(TOP_N)
    )
    top_reactions = (
        rx.select(normalize_emoji_expr(pl.col("emoji"), **em).alias("e"))
        .group_by("e")
        .agg(pl.len().alias("n"))
        .sort(["n", "e"], descending=[True, False])
        .head(TOP_N)
    )

    messages: list[ExportMessage] | None = None
    if include_messages:
        names = {pid: ref.name for pid, ref in ctx.person_refs().items()}
        messages = [
            ExportMessage(
                message_id=r["message_id"],
                thread_id=r["thread_id"],
                sender_id=r["sender_id"],
                sender=names.get(r["sender_id"], f"#{r['sender_id']}"),
                ts=r["local_ts"],
                type=r["type"],
                text=ctx.text(r["text"]),
            )
            for r in rows.sort("message_id")
            .select("message_id", "thread_id", "sender_id", "local_ts", "type", "text")
            .iter_rows(named=True)
        ]

    return ExportResponse(
        dataset_version=ctx.dataset_version,
        scope=scope,
        generated_at=datetime.now(UTC),
        filters=ctx.filters,
        chat=chat,
        totals=totals,
        people=people,
        chats=chats,
        types=_counts(types.sort(["n", "type"], descending=[True, False]), "type"),
        years=_counts(years, "year"),
        hours=[int(hours_map.get(h, 0)) for h in range(24)],
        weekdays=[int(wd_map.get(d, 0)) for d in range(1, 8)],
        top_emojis=_counts(emojis, "e"),
        top_reactions=_counts(top_reactions, "e"),
        messages=messages,
    )


@router.get("/{scope}.json", response_model=ExportResponse)
def export_json(
    ctx: Context,
    scope: str,
    response: Response,
    include_messages: Annotated[bool, Query(description="Add message rows (with text)")] = False,
) -> ExportResponse:
    """Full stats summary as a JSON attachment (``scope`` = ``all`` | ``chat-<id>``)."""
    m = _SCOPE_RE.match(scope)
    if m is None:
        raise NotFoundError(f"Unknown export scope {scope!r} (use 'all' or 'chat-<id>').")
    chat_id = int(m.group(1)) if m.group(1) else None
    if (
        chat_id is not None
        and ctx.dataset.threads_df().filter(pl.col("thread_id") == chat_id).is_empty()
    ):
        raise NotFoundError(f"Chat {chat_id} not found.")
    if include_messages:  # potentially huge: never cached
        result = build_export(ctx, scope, chat_id, True)
    else:
        result = ctx.cached("export", lambda: build_export(ctx, scope, chat_id, False), extra=scope)
    response.headers["Content-Disposition"] = f'attachment; filename="instagraph-{scope}.json"'
    return result
