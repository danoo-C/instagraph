"""Shared helpers for the awards / wrapped / people routes (not a router module)."""

import polars as pl

from instagraph.analysis.awards import AwardResult, compute_awards
from instagraph.analysis.person_metrics import person_metrics
from instagraph.api.deps import AnalysisContext
from instagraph.api.filters import FilterParams, filtered_messages, filtered_reactions
from instagraph.errors import NotFoundError
from instagraph.schemas.awards import Award, AwardRankEntry
from instagraph.schemas.common import MessageQuery, MessageRef


def scoped_frames(
    ctx: AnalysisContext, filters: FilterParams | None = None, thread_id: int | None = None
) -> tuple[pl.LazyFrame, pl.LazyFrame]:
    """(messages, reactions) for ``filters`` WITHOUT the people filter, optionally one chat."""
    f = (filters or ctx.filters).model_copy(update={"people": None})
    # thread_id → chat scope: thread-category filters are ignored (see filtered_messages).
    msgs = filtered_messages(ctx.dataset, f, thread_id=thread_id)
    return msgs, filtered_reactions(ctx.dataset, f, messages=msgs)


def scoped_metrics(
    ctx: AnalysisContext, filters: FilterParams | None = None, thread_id: int | None = None
) -> pl.DataFrame:
    """:func:`person_metrics` for the scope; output rows restricted by the people filter."""
    msgs, rx = scoped_frames(ctx, filters, thread_id)
    people = (filters or ctx.filters).people
    return person_metrics(msgs, rx, laughter_patterns=ctx.settings.laughter_patterns, people=people)


def scoped_awards(
    ctx: AnalysisContext, filters: FilterParams | None = None, thread_id: int | None = None
) -> list[AwardResult]:
    return compute_awards(scoped_metrics(ctx, filters, thread_id))


def award_models(
    ctx: AnalysisContext, results: list[AwardResult], thread_id: int | None = None
) -> list[Award]:
    out: list[Award] = []
    for r in results:
        a = r.award
        drill: MessageQuery | None = None
        if r.winners and a.drill_types is not None:
            drill = MessageQuery(
                thread_id=thread_id,
                sender_ids=list(r.winners),
                types=list(a.drill_types),
            )
        out.append(
            Award(
                key=a.key,
                emoji=a.emoji,
                title=a.title,
                rule=a.rule,
                eligibility=a.eligibility(),
                unit=a.unit,
                lower_is_better=a.lower_is_better,
                winners=[ctx.person_ref(p) for p in r.winners],
                value=r.value,
                ranking=[AwardRankEntry(person=ctx.person_ref(p), value=v) for p, v in r.ranking],
                drilldown=drill,
            )
        )
    return out


SNIPPET_CHARS = 280


def ensure_thread(ctx: AnalysisContext, thread_id: int) -> None:
    """404 if ``thread_id`` is not in the dataset."""
    ctx.ensure_chat(thread_id)


def ensure_person(ctx: AnalysisContext, person_id: int) -> None:
    """404 if ``person_id`` is not in the dataset."""
    if ctx.dataset.people_df().filter(pl.col("person_id") == person_id).is_empty():
        raise NotFoundError(f"Person {person_id} not found.")


def one_to_one_map(ctx: AnalysisContext) -> dict[int, int]:
    """``thread_id -> other person`` for 1:1 chats that include the owner."""
    owner = ctx.owner_id
    out: dict[int, int] = {}
    for tid, is_group, parts in (
        ctx.dataset.threads_df().select("thread_id", "is_group", "participants").iter_rows()
    ):
        others = [p for p in (parts or []) if p != owner]
        if not is_group and len(others) == 1 and owner in (parts or []):
            out[int(tid)] = int(others[0])
    return out


def message_refs(ctx: AnalysisContext, message_ids: list[int]) -> dict[int, MessageRef]:
    """``message_id -> MessageRef`` (snippet text via ``ctx.text``)."""
    if not message_ids:
        return {}
    df = (
        ctx.dataset.messages.filter(pl.col("message_id").is_in(message_ids))
        .select("message_id", "thread_id", "sender_id", "ts", "type", "text")
        .collect()
    )
    out: dict[int, MessageRef] = {}
    for r in df.iter_rows(named=True):
        text = r["text"]
        if text is not None and len(text) > SNIPPET_CHARS:
            text = text[: SNIPPET_CHARS - 1] + "…"
        out[int(r["message_id"])] = MessageRef(
            message_id=int(r["message_id"]),
            thread_id=int(r["thread_id"]),
            sender=ctx.person_ref(int(r["sender_id"])),
            ts=r["ts"],
            type=str(r["type"]),  # type: ignore[arg-type]
            text=ctx.text(text),
        )
    return out
