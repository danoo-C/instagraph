"""``GET /api/chats/{chat_id}/reactions`` — the Reactions tab (spec §6.7, §7.2).

Scope: like the Dynamics tab, the chat is explicit so thread-category filters don't hide
it. Reactions follow :func:`~instagraph.api.filters.filtered_reactions` (date range =
the reacted message's date; people filter = actor OR author in the list). Emoji merge
settings (skin tones / gendered) are applied. "Hearts" = ❤ reactions + likes.
"""

import polars as pl
from fastapi import APIRouter

from instagraph.analysis import reactions as rx
from instagraph.api.deps import AnalysisContext, Context
from instagraph.api.filters import (
    bucket_labels,
    is_counted_message,
)
from instagraph.api.routes.chat_dynamics import (
    bucketed_by_person,
    message_refs,
    month_axis,
    ordered_people,
    person_series,
)
from instagraph.errors import ERROR_RESPONSES
from instagraph.schemas.common import (
    CategoryChart,
    HeatmapData,
    MessageQuery,
    Series,
    Tile,
    TimeSeries,
)
from instagraph.schemas.reactions import (
    EmojiCount,
    PersonReactionStats,
    ReactedMessage,
    ReactionsResponse,
    Reactor,
    ReciprocityPair,
)

router = APIRouter(prefix="/api/chats", tags=["chats"], responses=ERROR_RESPONSES)

TOP_EMOJIS = 10
MOST_REACTED = 20


def compute_reactions(ctx: AnalysisContext, chat_id: int) -> ReactionsResponse:
    msgs_lf = ctx.chat_messages(chat_id, people=False)
    msgs = msgs_lf.collect()
    reactions = rx.normalize_reactions(
        ctx.chat_reactions(chat_id),
        merge_skin_tones=ctx.settings.emoji_merge_skin_tones,
        merge_gendered=ctx.settings.emoji_merge_gendered,
    ).collect()

    people = ordered_people(
        ctx,
        [
            *msgs["sender_id"].to_list(),
            *reactions["actor_id"].to_list(),
            *reactions["target_id"].to_list(),
        ],
    )
    gr = {
        int(r["person_id"]): r for r in rx.reactions_given_received(reactions).iter_rows(named=True)
    }
    ht = {int(r["person_id"]): r for r in rx.hearts(reactions, msgs).iter_rows(named=True)}
    n_msgs = dict(msgs.filter(is_counted_message()).group_by("sender_id").agg(pl.len()).iter_rows())
    top_given = rx.top_reaction_emojis(reactions, "actor_id", TOP_EMOJIS)
    top_recv = rx.top_reaction_emojis(reactions, "target_id", TOP_EMOJIS)

    def emoji_list(df: pl.DataFrame, pid: int, *, given: bool) -> list[EmojiCount]:
        out: list[EmojiCount] = []
        for e, n in df.filter(pl.col("person_id") == pid).select("emoji", "count").iter_rows():
            dd = (
                MessageQuery(thread_id=chat_id, reactor_id=pid, reaction_emoji=e)
                if given
                else MessageQuery(thread_id=chat_id, sender_id=pid, reaction_emoji=e)
            )
            out.append(EmojiCount(emoji=e, count=int(n), drilldown=dd))
        return out

    person_rows: list[PersonReactionStats] = []
    for p in people:
        g = gr.get(p, {})
        h = ht.get(p, {})
        received = int(g.get("received", 0))
        sent = int(n_msgs.get(p, 0))
        person_rows.append(
            PersonReactionStats(
                person=ctx.person_ref(p),
                given=int(g.get("given", 0)),
                received=received,
                messages=sent,
                received_per_100_messages=received * 100 / sent if sent else None,
                self_reactions=int(g.get("self_reactions", 0)),
                heart_reactions_given=int(h.get("heart_reactions_given", 0)),
                likes_given=int(h.get("likes_given", 0)),
                hearts_given=int(h.get("hearts_given", 0)),
                heart_reactions_received=int(h.get("heart_reactions_received", 0)),
                likes_received=int(h.get("likes_received", 0)),
                hearts_received=int(h.get("hearts_received", 0)),
                top_given=emoji_list(top_given, p, given=True),
                top_received=emoji_list(top_recv, p, given=False),
                given_drilldown=MessageQuery(thread_id=chat_id, reactor_id=p),
                received_drilldown=MessageQuery(thread_id=chat_id, sender_id=p, has_reactions=True),
            )
        )

    # ---- matrix (everyone involved, incl. people outside the people filter) ---------
    involved = sorted(
        {int(i) for i in [*reactions["actor_id"].to_list(), *reactions["target_id"].to_list()]},
        key=lambda i: (ctx.person_ref(i).color_index, i),
    )
    pos = {p: i for i, p in enumerate(involved)}
    values = [[0.0] * len(involved) for _ in involved]
    for a, t, n in rx.reaction_matrix(reactions).iter_rows():
        values[pos[int(a)]][pos[int(t)]] = float(n)
    matrix_names = [ctx.person_ref(p).name for p in involved]

    # ---- most reacted --------------------------------------------------------------
    top = rx.most_reacted(reactions, MOST_REACTED)
    top_ids = [int(i) for i in top["message_id"].to_list()]
    refs = message_refs(ctx, top_ids)
    reactors: dict[int, list[Reactor]] = {}
    for mid, actor, emoji in (
        reactions.filter(pl.col("message_id").is_in(top_ids))
        .sort("message_id", "actor_id")
        .select("message_id", "actor_id", "emoji")
        .iter_rows()
    ):
        reactors.setdefault(int(mid), []).append(
            Reactor(person=ctx.person_ref(int(actor)), emoji=emoji)
        )
    most = [
        ReactedMessage(message=refs[mid], count=int(n), reactors=reactors.get(mid, []))
        for mid, n in top.iter_rows()
        if mid in refs
    ]

    # ---- over time -----------------------------------------------------------------
    x = month_axis(reactions["local_date"])
    over_time = TimeSeries(
        bucket="month",
        x=bucket_labels(x),
        series=person_series(
            ctx, people, bucketed_by_person(reactions, pl.len(), x, people, person_col="actor_id")
        ),
        unit="count",
    )

    # ---- reciprocity & tiles -------------------------------------------------------
    pairs = [
        ReciprocityPair(
            a=ctx.person_ref(int(r["a_id"])),
            b=ctx.person_ref(int(r["b_id"])),
            a_to_b=int(r["a_to_b"]),
            b_to_a=int(r["b_to_a"]),
            reciprocity=float(r["reciprocity"]),
        )
        for r in rx.reciprocity(reactions).iter_rows(named=True)
    ]
    total = reactions.height
    self_total = int((reactions["actor_id"] == reactions["target_id"]).sum())
    likes_total = sum(r.likes_given for r in person_rows)
    heart_reactions = int((reactions["emoji"] == rx.HEART).sum())
    tiles = [
        Tile(
            key="total_reactions",
            label="Reactions",
            value=total,
            unit="count",
            drilldown=MessageQuery(thread_id=chat_id, has_reactions=True),
        ),
        Tile(key="self_reactions", label="Self-reactions", value=self_total, unit="count"),
        Tile(
            key="likes",
            label="Likes",
            value=likes_total,
            unit="count",
            drilldown=MessageQuery(thread_id=chat_id, types=["like"]),
        ),
        Tile(
            key="hearts",
            label="Hearts (❤ reactions + likes)",
            value=heart_reactions + likes_total,
            unit="count",
            detail=f"{heart_reactions} ❤ reactions, {likes_total} likes",
        ),
    ]
    thread = ctx.ensure_chat(chat_id)
    if not thread["is_group"] and len(pairs) == 1:
        tiles.append(
            Tile(
                key="reciprocity",
                label="Reaction reciprocity",
                value=pairs[0].reciprocity,
                unit="ratio",
                detail=f"{pairs[0].a_to_b} vs {pairs[0].b_to_a}",
            )
        )

    return ReactionsResponse(
        dataset_version=ctx.dataset_version,
        chat_id=chat_id,
        tiles=tiles,
        people=person_rows,
        given_received=CategoryChart(
            categories=[r.person.name for r in person_rows],
            series=[
                Series(key="given", name="Given", values=[float(r.given) for r in person_rows]),
                Series(
                    key="received",
                    name="Received",
                    values=[float(r.received) for r in person_rows],
                ),
            ],
            unit="count",
        ),
        top_emojis=[
            EmojiCount(
                emoji=e,
                count=int(n),
                drilldown=MessageQuery(thread_id=chat_id, reaction_emoji=e),
            )
            for e, n in rx.top_reaction_emojis(reactions, None, TOP_EMOJIS)
            .select("emoji", "count")
            .iter_rows()
        ],
        matrix=HeatmapData(
            x_labels=matrix_names, y_labels=matrix_names, values=values, unit="count"
        ),
        matrix_people=[ctx.person_ref(p) for p in involved],
        most_reacted=most,
        over_time=over_time,
        reciprocity=pairs,
    )


@router.get("/{chat_id}/reactions", response_model=ReactionsResponse)
def chat_reactions(chat_id: int, ctx: Context) -> ReactionsResponse:
    """Reactions given/received, top emojis, matrix, most reacted messages, hearts."""
    ctx = ctx.for_chat(chat_id)
    return ctx.cached("chat_reactions", lambda: compute_reactions(ctx, chat_id), extra=chat_id)
