"""``GET /api/wrapped`` (year-in-review slides) and ``GET /api/wrapped/years`` (§5.10).

The global ``start``/``end`` filters are replaced by the chosen year; every other filter
(thread categories, groups, tz, people) still applies. ``people`` restricts the
rankings (top people, fastest replier, awards) but not the context used to compute
replies / laughter.
"""

from datetime import date
from typing import Annotated

import polars as pl
from fastapi import APIRouter, Query

from instagraph.analysis.awards import awards_won_by, compute_awards, personality_summary
from instagraph.analysis.person_metrics import person_metrics
from instagraph.analysis.wrapped import (
    WrappedData,
    compute_wrapped,
    load_stopwords,
    years_with_data,
)
from instagraph.api.deps import AnalysisContext, Context
from instagraph.api.routes._insights_common import (
    award_models,
    ensure_thread,
    message_refs,
    one_to_one_map,
    scoped_frames,
)
from instagraph.errors import ERROR_RESPONSES
from instagraph.schemas.common import MessageQuery
from instagraph.schemas.wrapped import (
    WrappedCall,
    WrappedDay,
    WrappedEmoji,
    WrappedMonth,
    WrappedPersonality,
    WrappedPersonCount,
    WrappedReactedMessage,
    WrappedReplier,
    WrappedResponse,
    WrappedStreak,
    WrappedWord,
    WrappedYear,
    WrappedYearsResponse,
)

router = APIRouter(tags=["wrapped"], responses=ERROR_RESPONSES)

YearQ = Annotated[int | None, Query(ge=1970, le=2100, description="Default: latest year")]
ChatQ = Annotated[int | None, Query(description="Chat id; omit for all chats")]


def _years(ctx: AnalysisContext, chat: int | None) -> list[tuple[int, int]]:
    f = ctx.filters.model_copy(update={"start": None, "end": None})
    msgs, _ = scoped_frames(ctx, f, chat)
    if f.people is not None:
        msgs = msgs.filter(pl.col("sender_id").is_in(list(f.people)))
    return years_with_data(msgs)


@router.get("/api/wrapped/years", response_model=WrappedYearsResponse)
def wrapped_years(ctx: Context, chat: ChatQ = None) -> WrappedYearsResponse:
    """Years that have at least one message (ignores the date-range filter)."""
    if chat is not None:
        ensure_thread(ctx, chat)

    def compute() -> WrappedYearsResponse:
        return WrappedYearsResponse(
            dataset_version=ctx.dataset_version,
            years=[WrappedYear(year=y, messages=n) for y, n in _years(ctx, chat)],
        )

    return ctx.cached("wrapped_years", compute, extra=chat)


@router.get("/api/wrapped", response_model=WrappedResponse)
def wrapped(ctx: Context, year: YearQ = None, chat: ChatQ = None) -> WrappedResponse:
    """All slide data for one year and scope (all chats or ``chat``)."""
    if chat is not None:
        ensure_thread(ctx, chat)

    def compute() -> WrappedResponse:
        years = _years(ctx, chat)
        y = year if year is not None else (years[-1][0] if years else date.today().year)
        start, end = date(y, 1, 1), date(y, 12, 31)
        f = ctx.filters.model_copy(update={"start": start, "end": end})
        msgs, rx = scoped_frames(ctx, f, chat)
        people = f.people
        if people is not None:
            # Rankings only among the selected people; totals over their messages.
            sel = list(people)
            data = _run(
                ctx,
                msgs.filter(pl.col("sender_id").is_in(sel)),
                rx.filter(pl.col("actor_id").is_in(sel) | pl.col("target_id").is_in(sel)),
                y,
                chat,
            )
            metrics = person_metrics(
                msgs, rx, laughter_patterns=ctx.settings.laughter_patterns, people=sel
            )
            data.awards = compute_awards(metrics)
            owner = ctx.owner_id
            data.my_awards = awards_won_by(data.awards, owner) if owner is not None else []
        else:
            data = _run(ctx, msgs, rx, y, chat)

        base = MessageQuery(thread_id=chat, start=start, end=end)
        refs = message_refs(
            ctx,
            [
                m
                for m in (
                    data.most_reacted[0] if data.most_reacted else None,
                    data.longest_call[0] if data.longest_call else None,
                )
                if m is not None
            ],
        )
        emojis = [
            WrappedEmoji(emoji=e, count=n, drilldown=base.model_copy(update={"emoji": e}))
            for e, n in data.top_emojis
        ]
        anonymize = ctx.settings.anonymize
        reacted = None
        if data.most_reacted and data.most_reacted[0] in refs:
            ref = refs[data.most_reacted[0]]
            reacted = WrappedReactedMessage(
                message=ref,
                thread_title=ctx.thread_title(ref.thread_id),
                reactions=data.most_reacted[1],
                emojis=data.most_reacted[2],
                drilldown=MessageQuery(message_ids=[ref.message_id]),
            )
        call = None
        if data.longest_call and data.longest_call[0] in refs:
            ref = refs[data.longest_call[0]]
            call = WrappedCall(
                message=ref,
                thread_title=ctx.thread_title(ref.thread_id),
                duration_ms=data.longest_call[1],
                drilldown=MessageQuery(message_ids=[ref.message_id]),
            )
        return WrappedResponse(
            dataset_version=ctx.dataset_version,
            year=y,
            chat_id=chat,
            chat_title=ctx.thread_title(chat) if chat is not None else None,
            available_years=[yy for yy, _ in years],
            total_messages=data.total_messages,
            sent_by_me=data.sent_by_me,
            received=data.received,
            total_drilldown=base,
            months=[WrappedMonth(month=m, messages=n) for m, n in data.months],
            top_people=[
                WrappedPersonCount(
                    person=ctx.person_ref(p),
                    messages=n,
                    drilldown=base.model_copy(update={"sender_id": p}),
                )
                for p, n in data.top_people
            ],
            top_emoji=emojis[0] if emojis else None,
            top_emojis=emojis,
            busiest_day=(
                WrappedDay(
                    date=data.busiest_day[0],
                    messages=data.busiest_day[1],
                    drilldown=MessageQuery(
                        thread_id=chat, start=data.busiest_day[0], end=data.busiest_day[0]
                    ),
                )
                if data.busiest_day
                else None
            ),
            night_owl_share=data.night_owl_share,
            my_night_owl_share=data.my_night_owl_share,
            longest_streak=(
                WrappedStreak(
                    days=data.longest_streak[0],
                    start=data.longest_streak[1],
                    end=data.longest_streak[2],
                )
                if data.longest_streak
                else None
            ),
            top_word=None if anonymize or not data.top_words else data.top_words[0][0],
            top_words=[]
            if anonymize
            else [WrappedWord(word=w, count=n) for w, n in data.top_words],
            fastest_replier=(
                WrappedReplier(
                    person=ctx.person_ref(data.fastest_replier[0]),
                    median_ms=data.fastest_replier[1],
                    replies=data.fastest_replier[2],
                )
                if data.fastest_replier
                else None
            ),
            most_reacted_message=reacted,
            longest_call=call,
            personality=WrappedPersonality(
                summary=personality_summary(data.my_awards),
                awards=award_models(ctx, data.my_awards, thread_id=chat),
            ),
        )

    return ctx.cached("wrapped", compute, extra=(year, chat))


def _run(
    ctx: AnalysisContext, msgs: pl.LazyFrame, rx: pl.LazyFrame, year: int, chat: int | None
) -> WrappedData:
    s = ctx.settings
    return compute_wrapped(
        msgs,
        rx,
        year=year,
        owner_id=ctx.owner_id,
        chat_scope=chat is not None,
        one_to_one=one_to_one_map(ctx),
        laughter_patterns=s.laughter_patterns,
        stopwords=load_stopwords(s.stopwords_added, s.stopwords_removed, s.ignored_words),
        merge_skin_tones=s.emoji_merge_skin_tones,
        merge_gendered=s.emoji_merge_gendered,
    )
