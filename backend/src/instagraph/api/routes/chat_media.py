"""Chat detail tabs: Media & Links, Calls, Group (spec §7.2).

Explicit paths only (``/api/chats/{chat_id}/media|calls|group``) — other modules share
the ``/api/chats`` prefix.
"""

from typing import Annotated, Any

import polars as pl
from fastapi import APIRouter, Query

from instagraph.analysis import calls as calls_analysis
from instagraph.analysis import group as group_analysis
from instagraph.analysis import media as media_analysis
from instagraph.api.deps import AnalysisContext, Context, StoreDep
from instagraph.api.filters import filtered_messages
from instagraph.errors import ERROR_RESPONSES, ApiError
from instagraph.media.audio import audio_durations_or_start
from instagraph.schemas.calls import CallsResponse
from instagraph.schemas.common import Bucket
from instagraph.schemas.group import GroupResponse
from instagraph.schemas.media import ChatMediaResponse
from instagraph.store.dataset import MediaKind

router = APIRouter(prefix="/api/chats", tags=["chats"], responses=ERROR_RESPONSES)


def _thread(ctx: AnalysisContext, chat_id: int) -> dict[str, Any]:
    return ctx.ensure_chat(chat_id)


def _participants(ctx: AnalysisContext, thread: dict[str, Any]) -> list[int]:
    ids = [int(p) for p in thread["participants"] or []]
    if ctx.filters.people is not None:
        ids = [p for p in ids if p in ctx.filters.people]
    owner = ctx.owner_id
    return sorted(ids, key=lambda p: (p != owner, p))


def _chat_messages(ctx: AnalysisContext, chat_id: int) -> pl.LazyFrame:
    return ctx.chat_messages(chat_id)


@router.get("/{chat_id}/media", response_model=ChatMediaResponse)
def chat_media(
    chat_id: int,
    ctx: Context,
    store: StoreDep,
    bucket: Bucket = "month",
    sender: Annotated[int | None, Query(description="Gallery: only this sender")] = None,
    year: Annotated[int | None, Query(description="Gallery: only this local year")] = None,
    kind: Annotated[
        list[MediaKind] | None, Query(description="Gallery kinds (default photo, video)")
    ] = None,
    cursor: Annotated[str | None, Query(description="Gallery page cursor")] = None,
    limit: Annotated[int, Query(ge=1, le=500, description="Gallery page size")] = 60,
) -> ChatMediaResponse:
    """Media & Links tab: per-person counts, type mix, reels exchange, top accounts and
    domains, voice notes, and one page of the photo/video gallery."""
    ctx = ctx.for_chat(chat_id)
    thread = _thread(ctx, chat_id)
    status, durations = audio_durations_or_start(ctx.dataset, store.data_dir)
    kinds = tuple(kind) if kind else media_analysis.GALLERY_KINDS

    def compute() -> ChatMediaResponse:
        msgs = _chat_messages(ctx, chat_id)
        people = _participants(ctx, thread)
        ref = ctx.person_ref
        rows = media_analysis.build_person_counts(msgs, ref, chat_id, people)
        return ChatMediaResponse(
            dataset_version=ctx.dataset_version,
            chat_id=chat_id,
            per_person=rows,
            counts_chart=media_analysis.counts_chart(rows),
            type_mix=media_analysis.type_mix(msgs, bucket),
            reels_exchange=media_analysis.reels_exchange(msgs, ref, bucket, people),
            top_shared_accounts=media_analysis.top_shared_accounts(
                msgs, chat_id, anonymize=ctx.settings.anonymize
            ),
            top_link_domains=media_analysis.top_link_domains(msgs, chat_id),
            voice_notes=media_analysis.voice_notes(
                msgs, ctx.dataset.media, ref, chat_id, durations, status, people
            ),
            gallery=media_analysis.gallery(
                msgs,
                ctx.dataset.media,
                ref,
                kinds=kinds,
                sender=sender,
                year=year,
                cursor=cursor,
                limit=limit,
            ),
        )

    extra = (chat_id, bucket, sender, year, kinds, cursor, limit, status)
    return ctx.cached("chat_media", compute, extra=extra)


@router.get("/{chat_id}/calls", response_model=CallsResponse)
def chat_calls(chat_id: int, ctx: Context) -> CallsResponse:
    """Calls tab: counts, durations (ms), per month, starters, missed, heatmap, top 10."""
    ctx = ctx.for_chat(chat_id)
    thread = _thread(ctx, chat_id)

    def compute() -> CallsResponse:
        return calls_analysis.analyze_calls(
            _chat_messages(ctx, chat_id),
            ctx.person_ref,
            chat_id=chat_id,
            dataset_version=ctx.dataset_version,
            people=_participants(ctx, thread),
        )

    return ctx.cached("chat_calls", compute, extra=chat_id)


@router.get("/{chat_id}/group", response_model=GroupResponse)
def chat_group(chat_id: int, ctx: Context, bucket: Bucket = "month") -> GroupResponse:
    """Group tab (group chats only; 400 ``not_a_group`` for 1:1 chats)."""
    ctx = ctx.for_chat(chat_id)
    thread = _thread(ctx, chat_id)
    if not thread["is_group"]:
        raise ApiError("not_a_group", f"Chat {chat_id} is not a group chat.", 400)

    def compute() -> GroupResponse:
        ds, f = ctx.dataset, ctx.filters
        msgs = _chat_messages(ctx, chat_id)
        no_people = ctx.chat_messages(chat_id, people=False)
        events_lf = filtered_messages(
            ds, f.model_copy(update={"people": None, "exclude_system": False}), thread_id=chat_id
        )
        reactions = ctx.chat_reactions(chat_id)
        ref, text = ctx.person_ref, ctx.text
        participants = [int(p) for p in thread["participants"] or []]

        members = group_analysis.member_stats(msgs, reactions, ref, chat_id, participants)
        order = [m.person.person_id for m in members]
        people_names = ds.people_df()
        names: dict[int, list[str]] = {
            int(pid): [n for n in [display, *(aliases or [])] if n]
            for pid, display, aliases in people_names.select(
                "person_id", "display_name", "aliases"
            ).iter_rows()
        }
        member_ids = set(order) | set(participants)
        member_names = {p: names.get(p, []) for p in member_ids}
        index = group_analysis.name_index(names, prefer=member_ids)

        membership = group_analysis.build_events(
            group_analysis.system_events(events_lf, group_analysis.MEMBERSHIP_EVENTS),
            ref,
            text,
            index,
            match_member=True,
        )
        history = group_analysis.build_events(
            group_analysis.system_events(events_lf, group_analysis.HISTORY_EVENTS),
            ref,
            text,
            index,
            match_member=False,
        )
        mentions = group_analysis.mention_counts(msgs, group_analysis.mention_index(member_names))
        return GroupResponse(
            dataset_version=ctx.dataset_version,
            chat_id=chat_id,
            members=members,
            leaderboard=group_analysis.leaderboard_chart(members),
            lurkers=group_analysis.lurkers(members),
            replies=group_analysis.interaction_matrix(
                group_analysis.reply_pairs(no_people), order, ref, f.people
            ),
            reactions=group_analysis.interaction_matrix(
                group_analysis.reaction_pairs(reactions), order, ref
            ),
            activity=group_analysis.activity_stream(msgs, ref, bucket),
            membership_events=membership,
            membership=group_analysis.membership_intervals(
                membership, list(dict.fromkeys(participants + order)), ref
            ),
            history=history,
            mentions=group_analysis.mentions_chart(mentions, ref, chat_id),
        )

    return ctx.cached("chat_group", compute, extra=(chat_id, bucket))
