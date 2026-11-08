"""Chat Words tab, Emojis tab and term tracker (spec §6.5, §6.6, §7.2, §8.2).

* ``GET /api/chats/{chat_id}/words``  → :class:`WordsResponse`
* ``GET /api/chats/{chat_id}/emojis`` → :class:`EmojisResponse`
* ``GET /api/chats/{chat_id}/terms?q=…&mode=…`` → :class:`TermsResponse`

Chat-scoped data = the global filtered view restricted to the chat (same convention
as ``chats.py``). Text stats use ``is_text_message()`` rows only. Anonymize mode: word
lists are emptied (``text_hidden``), snippets null; see ``schemas/words.py``.
"""

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from typing import Annotated, Any

import polars as pl
from fastapi import APIRouter, Path, Query

from instagraph.analysis import emoji as E
from instagraph.analysis import text as T
from instagraph.api.deps import AnalysisContext, Context
from instagraph.api.filters import bucket_labels, is_counted_message, is_text_message
from instagraph.errors import ERROR_RESPONSES, ValidationApiError
from instagraph.resources import (
    EMOJI_CATEGORIES,
    emoji_category,
    stopwords_for,
    swear_words_for,
)
from instagraph.schemas.common import (
    Bucket,
    CategoryChart,
    MessageQuery,
    MessageRef,
    Series,
    TimeSeries,
)
from instagraph.schemas.emojis import (
    EmojiChain,
    EmojiCount,
    EmojisResponse,
    FirstUse,
    PersonEmojiStats,
)
from instagraph.schemas.words import (
    DistinctiveWords,
    LaughPair,
    LaughterStats,
    PersonCount,
    PersonMessages,
    PersonTerms,
    PersonTextStats,
    RepeatedMessage,
    SwearStats,
    TermCount,
    TermLists,
    TermMode,
    TermResult,
    TermsResponse,
    WordsResponse,
)
from instagraph.textproc import EMOTICONS

router = APIRouter(prefix="/api/chats", tags=["chats"], responses=ERROR_RESPONSES)

ChatId = Annotated[int, Path(ge=0, description="Thread id")]
SNIPPET_CHARS = 500
MAX_TERMS = 20


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def _thread(ctx: AnalysisContext, chat_id: int) -> dict[str, Any]:
    return ctx.ensure_chat(chat_id)


def _chat_frame(ctx: AnalysisContext, chat_id: int) -> pl.DataFrame:
    """Counted messages of the chat after the chat-scope filters (eager)."""
    return ctx.chat_messages(chat_id).filter(is_counted_message()).collect()


def _date_span(ctx: AnalysisContext, df: pl.DataFrame) -> tuple[date, date]:
    if not df.is_empty():
        lo, hi = df.select(
            pl.col("local_date").min().alias("lo"), pl.col("local_date").max().alias("hi")
        ).row(0)
        return lo, hi
    today = datetime.now(UTC).date()
    return ctx.filters.start or ctx.filters.end or today, ctx.filters.end or today


def _order(ctx: AnalysisContext, ids: Sequence[int]) -> list[int]:
    return sorted(set(ids), key=lambda p: (ctx.person_ref(p).color_index, p))


def _snippet(ctx: AnalysisContext, text: str | None) -> str | None:
    if text is None:
        return None
    return ctx.text(text[:SNIPPET_CHARS])


def _msg_ref(ctx: AnalysisContext, row: dict[str, Any]) -> MessageRef:
    return MessageRef(
        message_id=int(row["message_id"]),
        thread_id=int(row["thread_id"]),
        sender=ctx.person_ref(int(row["sender_id"])),
        ts=row["ts"],
        type=row["type"],
        text=_snippet(ctx, row.get("text")),
    )


def _refs_by_id(
    ctx: AnalysisContext, df: pl.DataFrame, ids: Sequence[int]
) -> dict[int, MessageRef]:
    if not ids:
        return {}
    rows = df.filter(pl.col("message_id").is_in(list(ids))).select(
        "message_id", "thread_id", "sender_id", "ts", "type", "text"
    )
    return {int(r["message_id"]): _msg_ref(ctx, r) for r in rows.iter_rows(named=True)}


def _series(
    ctx: AnalysisContext, people: Sequence[int], values: Callable[[int], list[float | None]]
) -> list[Series]:
    out = []
    for pid in people:
        ref = ctx.person_ref(pid)
        out.append(
            Series(
                key=str(pid),
                name=ref.name,
                person_id=pid,
                color_index=ref.color_index,
                values=values(pid),
            )
        )
    return out


def _time_series(
    ctx: AnalysisContext,
    df: pl.DataFrame,
    value: pl.Expr,
    bucket: str,
    span: tuple[date, date],
    people: Sequence[int],
    unit: str = "count",
) -> TimeSeries:
    xs, per = T.bucketed_counts(df, value, bucket, span[0], span[1])
    zeros: list[float | None] = [0.0] * len(xs)
    return TimeSeries(
        bucket=bucket,  # type: ignore[arg-type]
        x=bucket_labels(xs),
        series=_series(ctx, people, lambda p: list(per.get(p, zeros))),
        unit=unit,  # type: ignore[arg-type]
    )


def _bucket(bucket: Bucket | None, span: tuple[date, date]) -> str:
    return bucket or T.auto_bucket(span[0], span[1])


# --------------------------------------------------------------------------------------
# Words
# --------------------------------------------------------------------------------------


def _build_words(
    ctx: AnalysisContext, chat_id: int, thread: dict[str, Any], accent_insensitive: bool
) -> WordsResponse:
    s = ctx.settings
    df = _chat_frame(ctx, chat_id)
    swear_list = swear_words_for(s) if s.swear_words_enabled else None
    a = T.analyze_words(
        df,
        stopwords=stopwords_for(s),
        laughter_patterns=s.laughter_patterns,
        swear_words=swear_list,
        accent_insensitive=accent_insensitive,
    )
    hidden = s.anonymize
    people = _order(ctx, list(a.text_messages))
    base_q = MessageQuery(thread_id=chat_id)

    def q_person(pid: int) -> MessageQuery:
        return base_q.model_copy(update={"sender_id": pid})

    def term_q(term: str, pid: int | None = None) -> MessageQuery:
        return base_q.model_copy(
            update={
                "sender_id": pid,
                "text": term,
                "text_mode": "word",
                "accent_insensitive": accent_insensitive or None,
            }
        )

    def terms(lst: list[tuple[str, int]], pid: int | None = None) -> list[TermCount]:
        if hidden:
            return []
        return [TermCount(term=t, count=c, drilldown=term_q(t, pid)) for t, c in lst]

    def term_lists(d: T.TermList) -> TermLists:
        return TermLists(
            overall=terms(d.get(None, [])),
            by_person=[
                PersonTerms(person=ctx.person_ref(p), terms=terms(d.get(p, []), p)) for p in people
            ],
        )

    lr = a.laughter
    stats = []
    for p in people:
        r = a.rates.get(p, {})
        stats.append(
            PersonTextStats(
                person=ctx.person_ref(p),
                text_messages=a.text_messages.get(p, 0),
                words=a.words.get(p, 0),
                avg_words_per_message=a.avg_words.get(p, 0.0),
                vocabulary_size=a.vocabulary.get(p, 0),
                lexical_diversity=a.mattr.get(p, 0.0),
                question_rate=r.get("question", 0.0),
                exclamation_rate=r.get("exclamation", 0.0),
                caps_rate=r.get("caps", 0.0),
                ellipsis_rate=r.get("ellipsis", 0.0),
                laugh_messages=lr.laughs.get(p, 0),
                laughs_per_100=lr.per_100(p),
                laughs_caused=lr.caused.get(p, 0),
                links=a.links.get(p, 0),
                mentions=a.mentions.get(p, 0),
                hashtags=a.hashtags.get(p, 0),
                swear_words=None if a.swear is None else a.swear.get(p, 0),
                drilldown=q_person(p),
            )
        )

    rate_keys = ("question", "exclamation", "caps", "ellipsis")
    rates = CategoryChart(
        categories=list(rate_keys),
        series=_series(ctx, people, lambda p: [a.rates.get(p, {}).get(k, 0.0) for k in rate_keys]),
        unit="ratio",
    )
    labels = [b[0] for b in T.LENGTH_BINS]
    lengths = CategoryChart(
        categories=labels,
        series=_series(
            ctx, people, lambda p: [float(v) for v in a.length_bins.get(p, [0] * len(labels))]
        ),
        unit="count",
    )
    laugh_people = _order(ctx, [*people, *lr.caused])
    laughter = LaughterStats(
        patterns=list(s.laughter_patterns),
        invalid_patterns=lr.invalid_patterns,
        chart=CategoryChart(
            categories=[ctx.person_ref(p).name for p in laugh_people],
            series=[
                Series(
                    key="laughs",
                    name="Laughs",
                    values=[float(lr.laughs.get(p, 0)) for p in laugh_people],
                ),
                Series(
                    key="caused",
                    name="Caused",
                    values=[float(lr.caused.get(p, 0)) for p in laugh_people],
                ),
            ],
            unit="count",
        ),
        pairs=[
            LaughPair(laugher=ctx.person_ref(lg), causer=ctx.person_ref(cs), count=c)
            for (lg, cs), c in sorted(lr.pairs.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
    )

    repeated: list[RepeatedMessage] = []
    if not hidden:
        for key, n, by in a.repeated.iter_rows():
            repeated.append(
                RepeatedMessage(
                    text=key,
                    count=int(n),
                    by_person=[
                        PersonCount(person=ctx.person_ref(int(b["sender_id"])), count=int(b["c"]))
                        for b in by
                    ],
                    drilldown=base_q.model_copy(update={"text": key, "text_mode": "plain"}),
                )
            )

    longest: list[PersonMessages] = []
    for p in people:
        rows = a.longest.filter(pl.col("sender_id") == p)
        refs = [_msg_ref(ctx, r) for r in rows.iter_rows(named=True)]
        longest.append(
            PersonMessages(
                person=ctx.person_ref(p),
                messages=refs,
                drilldown=base_q.model_copy(update={"message_ids": [m.message_id for m in refs]}),
            )
        )

    swear: SwearStats | None = None
    if a.swear is not None:
        swear = SwearStats(
            by_person=[
                PersonCount(person=ctx.person_ref(p), count=a.swear.get(p, 0)) for p in people
            ],
            top=[TermCount(term=t, count=c, drilldown=term_q(t)) for t, c in a.top_swear or []],
        )

    def mention_list(lst: list[tuple[str, int]], prefix: str) -> list[TermCount]:
        if hidden:
            return []
        return [
            TermCount(
                term=v,
                count=c,
                drilldown=base_q.model_copy(update={"text": prefix + v, "text_mode": "plain"}),
            )
            for v, c in lst
        ]

    return WordsResponse(
        dataset_version=ctx.dataset_version,
        chat_id=chat_id,
        text_hidden=hidden,
        accent_insensitive=accent_insensitive,
        people=stats,
        top_words=term_lists(a.top_words),
        bigrams=term_lists(a.bigrams),
        trigrams=term_lists(a.trigrams),
        distinctive=DistinctiveWords(
            method="log_odds" if a.distinctive_method == "log_odds" else "tfidf",
            by_person=[
                PersonTerms(
                    person=ctx.person_ref(p),
                    terms=[]
                    if hidden
                    else [
                        TermCount(term=t, count=c, score=sc, drilldown=term_q(t, p))
                        for t, c, sc in a.distinctive.get(p, [])
                    ],
                )
                for p in people
            ],
        ),
        rates=rates,
        length_distribution=lengths,
        laughter=laughter,
        top_mentions=mention_list(a.top_mentions, "@"),
        top_hashtags=mention_list(a.top_hashtags, "#"),
        repeated_messages=repeated,
        longest_messages=longest,
        swear=swear,
        saved_terms=list(s.custom_terms.get(str(thread.get("folder") or ""), [])),
    )


@router.get("/{chat_id}/words", response_model=WordsResponse)
def chat_words(
    chat_id: ChatId,
    ctx: Context,
    accent_insensitive: Annotated[
        bool, Query(description="Merge words that differ only in diacritics")
    ] = False,
) -> WordsResponse:
    """Words tab: vocabulary, MATTR, top words / n-grams, distinctive words, rates,
    laughter, repeated & longest messages, swear words (if enabled)."""
    ctx = ctx.for_chat(chat_id)
    thread = _thread(ctx, chat_id)
    return ctx.cached(
        "chat_words",
        lambda: _build_words(ctx, chat_id, thread, accent_insensitive),
        extra=(chat_id, accent_insensitive),
    )


# --------------------------------------------------------------------------------------
# Term tracker
# --------------------------------------------------------------------------------------


def _parse_terms(q: list[str] | None, mode: str) -> list[str]:
    out: list[str] = []
    for raw in q or []:
        parts = [raw] if mode == "regex" else raw.split(",")
        for p in parts:
            p = p.strip()
            if p and p not in out:
                out.append(p)
    return out


def _build_terms(
    ctx: AnalysisContext,
    chat_id: int,
    terms: list[str],
    mode: str,
    case_sensitive: bool,
    accent_insensitive: bool,
    bucket: Bucket | None,
) -> TermsResponse:
    df = _chat_frame(ctx, chat_id)
    span = _date_span(ctx, df)
    b = _bucket(bucket, span)
    try:
        hits = T.term_matches(
            df,
            terms,
            mode=mode,
            case_sensitive=case_sensitive,
            accent_insensitive=accent_insensitive,
        )
    except ValueError as exc:
        raise ValidationApiError(str(exc)) from exc
    people = _order(ctx, df.filter(is_text_message())["sender_id"].unique().to_list())
    results: list[TermResult] = []
    for i, term in enumerate(terms):
        col = f"n_{i}"
        sub = hits.filter(pl.col(col) > 0) if not hits.is_empty() else hits
        q = MessageQuery(
            thread_id=chat_id,
            text=term,
            text_mode=mode,  # type: ignore[arg-type]
            case_sensitive=case_sensitive or None,
            accent_insensitive=accent_insensitive or None,
        )
        per: dict[int, int] = {}
        first = last = None
        if not sub.is_empty():
            per = {
                int(g): int(c)
                for g, c in sub.group_by("sender_id").agg(pl.col(col).sum()).iter_rows()
            }
            first = _msg_ref(ctx, sub.row(0, named=True))
            last = _msg_ref(ctx, sub.row(-1, named=True))
        results.append(
            TermResult(
                term=term,
                occurrences=sum(per.values()),
                messages=sub.height,
                by_person=[
                    PersonCount(
                        person=ctx.person_ref(p),
                        count=per.get(p, 0),
                        drilldown=q.model_copy(update={"sender_id": p}),
                    )
                    for p in _order(ctx, [*people, *per])
                ],
                first=first,
                last=last,
                timeline=_time_series(ctx, sub, pl.col(col), b, span, _order(ctx, [*people, *per])),
                drilldown=q,
            )
        )
    return TermsResponse(
        dataset_version=ctx.dataset_version,
        chat_id=chat_id,
        mode=mode,  # type: ignore[arg-type]
        case_sensitive=case_sensitive,
        accent_insensitive=accent_insensitive,
        terms=results,
    )


@router.get("/{chat_id}/terms", response_model=TermsResponse)
def chat_terms(
    chat_id: ChatId,
    ctx: Context,
    q: Annotated[
        list[str] | None,
        Query(
            description="Terms (repeatable; comma-separated too, except in regex mode). "
            "Empty = the terms saved for this chat in settings."
        ),
    ] = None,
    mode: Annotated[TermMode, Query(description="plain substring, whole word, or regex")] = "word",
    case_sensitive: bool = False,
    accent_insensitive: bool = False,
    bucket: Bucket | None = None,
) -> TermsResponse:
    """Term tracker: usage over time per person, totals, first and last use."""
    ctx = ctx.for_chat(chat_id)
    thread = _thread(ctx, chat_id)
    terms = _parse_terms(q, mode)
    if not terms:
        terms = list(ctx.settings.custom_terms.get(str(thread.get("folder") or ""), []))
    if len(terms) > MAX_TERMS:
        raise ValidationApiError(f"At most {MAX_TERMS} terms at once.")
    return ctx.cached(
        "chat_terms",
        lambda: _build_terms(ctx, chat_id, terms, mode, case_sensitive, accent_insensitive, bucket),
        extra=(chat_id, tuple(terms), mode, case_sensitive, accent_insensitive, bucket),
    )


# --------------------------------------------------------------------------------------
# Emojis
# --------------------------------------------------------------------------------------


def _emoji_count(e: str, c: int, q: MessageQuery, score: float | None = None) -> EmojiCount:
    return EmojiCount(
        emoji=e,
        count=c,
        category=emoji_category(e),
        score=score,
        drilldown=q.model_copy(update={"emoji": e}),
    )


def _build_emojis(ctx: AnalysisContext, chat_id: int, bucket: Bucket | None) -> EmojisResponse:
    s = ctx.settings
    merge = {"merge_skin_tones": s.emoji_merge_skin_tones, "merge_gendered": s.emoji_merge_gendered}
    df = _chat_frame(ctx, chat_id)
    span = _date_span(ctx, df)
    b = _bucket(bucket, span)
    long = E.emoji_long(df, extra_cols=("local_date",), **merge).collect()
    totals = E.emoji_totals(df, **merge)
    people = _order(ctx, list(totals))
    counts = E.emoji_counts(long)
    top_per = E.top_from_counts(counts, 20)
    overall_counts = E.emoji_counts(long, None)
    top_all = E.top_from_counts(overall_counts, 20, None)[None]
    signature = E.signature_emojis(counts)
    chains = E.longest_chains(df, **merge)
    emoticons = E.emoticon_counts(df)
    cats = E.category_counts(long)
    base_q = MessageQuery(thread_id=chat_id)

    # first use of each top emoji
    top_set = [e for e, _ in top_all]
    first_ids = (
        long.filter(pl.col("emoji").is_in(top_set))
        .group_by("emoji")
        .agg(pl.col("message_id").min())
        if top_set
        else pl.DataFrame(schema={"emoji": pl.String, "message_id": pl.UInt32})
    )
    first_map = dict(first_ids.iter_rows())
    chain_ids = [c.message_id for c in chains.values()]
    refs = _refs_by_id(ctx, df, [*first_map.values(), *chain_ids])

    def chain_model(c: E.EmojiChain | None) -> EmojiChain | None:
        if c is None or c.message_id not in refs:
            return None
        return EmojiChain(length=c.length, emojis=c.emojis, message=refs[c.message_id])

    emo_per: dict[int, int] = {}
    for sid, _, n in emoticons.iter_rows():
        emo_per[int(sid)] = emo_per.get(int(sid), 0) + int(n)

    people_stats = []
    for p in people:
        t = totals[p]
        qp = base_q.model_copy(update={"sender_id": p})
        sig = signature.get(p)
        people_stats.append(
            PersonEmojiStats(
                person=ctx.person_ref(p),
                text_messages=t.text_messages,
                total=t.total,
                per_message=t.per_message,
                with_emoji_rate=t.with_emoji_rate,
                unique=t.unique,
                emoji_only_messages=t.emoji_only_messages,
                emoticons=emo_per.get(p, 0),
                top=[_emoji_count(e, c, qp) for e, c in top_per.get(p, [])],
                signature=_emoji_count(sig[0], sig[1], qp, sig[2]) if sig else None,
                longest_chain=chain_model(chains.get(p)),
                drilldown=qp,
            )
        )

    best_chain = max(chains.values(), key=lambda c: (c.length, -c.message_id), default=None)

    # emoticons chart
    emo_order = (
        emoticons.group_by("emoticon")
        .agg(pl.col("n").sum())
        .sort(["n", "emoticon"], descending=[True, False])["emoticon"]
        .to_list()
    )
    emo_order = [e for e in emo_order if e in EMOTICONS]
    emo_map = {(int(sid), str(e)): int(n) for sid, e, n in emoticons.iter_rows()}
    emo_chart = CategoryChart(
        categories=emo_order,
        series=_series(ctx, people, lambda p: [float(emo_map.get((p, e), 0)) for e in emo_order]),
        unit="count",
        drilldowns=[base_q.model_copy(update={"emoticon": e}) for e in emo_order],
    )

    cat_map = {(int(sid), str(c)): int(n) for sid, c, n in cats.iter_rows()}
    cat_chart = CategoryChart(
        categories=list(EMOJI_CATEGORIES),
        series=_series(
            ctx, people, lambda p: [float(cat_map.get((p, c), 0)) for c in EMOJI_CATEGORIES]
        ),
        unit="count",
    )

    top10 = top_set[:10]
    xs, per_emoji = T.bucketed_counts(
        long.filter(pl.col("emoji").is_in(top10)).with_columns(
            pl.col("emoji")
            .replace_strict({e: i for i, e in enumerate(top10)}, return_dtype=pl.UInt32)
            .alias("_idx")
        ),
        pl.lit(1),
        b,
        span[0],
        span[1],
        group_col="_idx",
    )
    zeros: list[float | None] = [0.0] * len(xs)
    top_over_time = TimeSeries(
        bucket=b,  # type: ignore[arg-type]
        x=bucket_labels(xs),
        series=[
            Series(key=e, name=e, values=list(per_emoji.get(i, zeros))) for i, e in enumerate(top10)
        ],
        unit="count",
    )
    hearts = long.filter(E.is_heart_expr())

    return EmojisResponse(
        dataset_version=ctx.dataset_version,
        chat_id=chat_id,
        merge_skin_tones=s.emoji_merge_skin_tones,
        merge_gendered=s.emoji_merge_gendered,
        total=long.height,
        unique=overall_counts.height,
        emoji_only_messages=sum(t.emoji_only_messages for t in totals.values()),
        people=people_stats,
        top=[_emoji_count(e, c, base_q) for e, c in top_all],
        longest_chain=chain_model(best_chain),
        usage_over_time=_time_series(ctx, long, pl.lit(1), b, span, people),
        top_over_time=top_over_time,
        first_use=[
            FirstUse(emoji=e, message=refs[first_map[e]])
            for e in top_set
            if e in first_map and first_map[e] in refs
        ],
        emoticons=emo_chart,
        categories=cat_chart,
        hearts_over_time=_time_series(ctx, hearts, pl.lit(1), b, span, people),
    )


@router.get("/{chat_id}/emojis", response_model=EmojisResponse)
def chat_emojis(chat_id: ChatId, ctx: Context, bucket: Bucket | None = None) -> EmojisResponse:
    """Emojis tab: totals, top / signature emojis, chains, usage over time, emoticons,
    categories, hearts."""
    ctx = ctx.for_chat(chat_id)
    return ctx.cached(
        "chat_emojis", lambda: _build_emojis(ctx, chat_id, bucket), extra=(chat_id, bucket)
    )
