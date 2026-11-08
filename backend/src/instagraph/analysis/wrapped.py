"""Wrapped slides (spec §5.10): a year in review for all chats or one chat.

:func:`compute_wrapped` works on frames that are ALREADY restricted to the year (and
optionally to one chat) — see the route, which overrides the date filter with the year.
Everything is returned with ids only; the route turns them into ``PersonRef`` /
``MessageRef`` (anonymize-aware).
"""

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from importlib import resources

import polars as pl

from instagraph.analysis.awards import AwardResult, awards_won_by, compute_awards
from instagraph.analysis.person_metrics import person_metrics, tokens_expr
from instagraph.api.filters import is_counted_message, is_text_message
from instagraph.textproc import normalize_emoji_expr

TOP_N = 5
NIGHT_END_HOUR = 5
MIN_REPLIES_FASTEST = 3

#: Fallback stop-words (EN + SK + CS) used when no ``resources/stopwords_*.txt`` exist.
_DEFAULT_STOPWORDS_TEXT = """
a an and are as at be but by do for from have he her him his i if in is it its me my
no not of on or our she so that the their them then there they this to too up us was
we were what when which who will with you your yes ok okay im i'm it's dont don't just
like that's u ur
aj ako ale alebo ani asi až bol bola bolo ci či do ho ja je jej ju k ked keď kde
ma má mám mi mna mňa na nas nás ne nie o od ona oni po pre pri s sa si som su sú
ta tak tam ten tu ty uz už v vo z za že ze
ano byl být co jak jako jen já když mě mně než nebo pro se také ve
"""
DEFAULT_STOPWORDS: frozenset[str] = frozenset(_DEFAULT_STOPWORDS_TEXT.split())


def load_stopwords(
    added: Mapping[str, Iterable[str]] | None = None,
    removed: Mapping[str, Iterable[str]] | None = None,
    ignored: Iterable[str] = (),
) -> frozenset[str]:
    """Built-in stop-words (``resources/stopwords_*.txt`` if present, else
    :data:`DEFAULT_STOPWORDS`) adjusted by the settings' added/removed/ignored words."""
    words: set[str] = set()
    try:
        for entry in resources.files("instagraph.resources").iterdir():
            name = entry.name
            if name.startswith("stopwords_") and name.endswith(".txt"):
                for line in entry.read_text(encoding="utf-8").splitlines():
                    w = line.strip().lower()
                    if w and not w.startswith("#"):
                        words.add(w)
    except (OSError, ModuleNotFoundError):
        words = set()
    if not words:
        words = set(DEFAULT_STOPWORDS)
    for ws in (added or {}).values():
        words.update(w.lower() for w in ws)
    for ws in (removed or {}).values():
        words.difference_update(w.lower() for w in ws)
    words.update(w.lower() for w in ignored)
    return frozenset(words)


def longest_streak(days: Iterable[date]) -> tuple[int, date, date] | None:
    """Longest run of consecutive calendar days: ``(length, start, end)``."""
    ordered = sorted(set(days))
    if not ordered:
        return None
    best = (1, ordered[0], ordered[0])
    start = prev = ordered[0]
    for d in ordered[1:]:
        if d - prev != timedelta(days=1):
            start = d
        prev = d
        length = (d - start).days + 1
        if length > best[0]:
            best = (length, start, d)
    return best


def top_words(
    msgs: pl.LazyFrame | pl.DataFrame, stopwords: frozenset[str], n: int = TOP_N
) -> list[tuple[str, int]]:
    """Most frequent lower-cased words of text messages (stop-words, numbers and
    1-letter tokens removed). Ties are broken alphabetically."""
    tok = (
        msgs.lazy()
        .filter(is_text_message())
        .select(tokens_expr(pl.col("text")).alias("w"))
        .explode("w")
        .drop_nulls("w")
        .filter(
            (pl.col("w").str.len_chars() > 1)
            & ~pl.col("w").str.contains(r"^\d+$")
            & ~pl.col("w").is_in(list(stopwords))
        )
        .group_by("w")
        .agg(pl.len().alias("n"))
        .sort(["n", "w"], descending=[True, False])
        .head(n)
        .collect()
    )
    return [(str(w), int(c)) for w, c in tok.iter_rows()]


@dataclass
class WrappedData:
    total_messages: int = 0
    sent_by_me: int = 0
    received: int = 0
    #: ``(month_start, messages)`` for the 12 months of the year.
    months: list[tuple[date, int]] = field(default_factory=list)
    top_people: list[tuple[int, int]] = field(default_factory=list)
    top_emojis: list[tuple[str, int]] = field(default_factory=list)
    busiest_day: tuple[date, int] | None = None
    night_owl_share: float | None = None
    my_night_owl_share: float | None = None
    longest_streak: tuple[int, date, date] | None = None
    top_words: list[tuple[str, int]] = field(default_factory=list)
    #: ``(person_id, median_ms, replies)``
    fastest_replier: tuple[int, float, int] | None = None
    #: ``(message_id, n_reactions, top reaction emojis)``
    most_reacted: tuple[int, int, list[str]] | None = None
    #: ``(message_id, duration_ms)``
    longest_call: tuple[int, int] | None = None
    awards: list[AwardResult] = field(default_factory=list)
    my_awards: list[AwardResult] = field(default_factory=list)


def compute_wrapped(
    messages: pl.LazyFrame,
    reactions: pl.LazyFrame,
    *,
    year: int,
    owner_id: int | None,
    chat_scope: bool,
    one_to_one: Mapping[int, int] | None = None,
    laughter_patterns: Iterable[str] | None = None,
    stopwords: frozenset[str] = DEFAULT_STOPWORDS,
    merge_skin_tones: bool = True,
    merge_gendered: bool = False,
) -> WrappedData:
    """Wrapped data for frames already limited to ``year`` (and one chat if ``chat_scope``).

    * Top people: in a chat, all senders by messages; across all chats, contacts (not the
      owner) by messages exchanged = their messages + my messages in our 1:1 chat
      (``one_to_one``: ``thread_id -> other person``).
    * Fastest replier: lowest median reply time among people with ≥ 3 replies.
    """
    out = WrappedData()
    counted = messages.filter(is_counted_message()).collect()
    month_starts = [date(year, m, 1) for m in range(1, 13)]
    if counted.is_empty():
        out.months = [(m, 0) for m in month_starts]
        return out

    out.total_messages = counted.height
    if owner_id is not None:
        out.sent_by_me = int((counted["sender_id"] == owner_id).sum())
        out.received = out.total_messages - out.sent_by_me

    by_month = dict(
        counted.group_by(pl.col("local_date").dt.month().alias("m"))
        .agg(pl.len().alias("n"))
        .iter_rows()
    )
    out.months = [(m, int(by_month.get(m.month, 0))) for m in month_starts]

    # Top people
    if chat_scope or owner_id is None:
        ranked = counted.group_by("sender_id").agg(pl.len().alias("n"))
        pairs = [(int(p), int(n)) for p, n in ranked.iter_rows()]
    else:
        scores: Counter[int] = Counter()
        for pid, n in (
            counted.filter(pl.col("sender_id") != owner_id)
            .group_by("sender_id")
            .agg(pl.len().alias("n"))
            .iter_rows()
        ):
            scores[int(pid)] += int(n)
        mine = (
            counted.filter(pl.col("sender_id") == owner_id)
            .group_by("thread_id")
            .agg(pl.len().alias("n"))
        )
        for tid, n in mine.iter_rows():
            other = (one_to_one or {}).get(int(tid))
            if other is not None and other != owner_id:
                scores[other] += int(n)
        pairs = list(scores.items())
    pairs.sort(key=lambda t: (-t[1], t[0]))
    out.top_people = pairs[:TOP_N]

    # Emojis
    emo = (
        counted.select(pl.col("emojis"))
        .explode("emojis")
        .drop_nulls("emojis")
        .select(
            normalize_emoji_expr(
                pl.col("emojis"), merge_skin_tones=merge_skin_tones, merge_gendered=merge_gendered
            ).alias("e")
        )
        .group_by("e")
        .agg(pl.len().alias("n"))
        .sort(["n", "e"], descending=[True, False])
        .head(TOP_N)
    )
    out.top_emojis = [(str(e), int(n)) for e, n in emo.iter_rows()]

    # Busiest day, streak, night owl
    days = (
        counted.group_by("local_date")
        .agg(pl.len().alias("n"))
        .sort(["n", "local_date"], descending=[True, False])
    )
    d0, n0 = days.row(0)
    out.busiest_day = (d0, int(n0))
    out.longest_streak = longest_streak(days["local_date"].to_list())
    night = counted["hour"] < NIGHT_END_HOUR
    out.night_owl_share = float(night.sum()) / counted.height
    if owner_id is not None and out.sent_by_me:
        mine_night = counted.filter(pl.col("sender_id") == owner_id)["hour"] < NIGHT_END_HOUR
        out.my_night_owl_share = float(mine_night.sum()) / out.sent_by_me

    out.top_words = top_words(counted, stopwords)

    # Metrics-based slides
    metrics = person_metrics(messages, reactions, laughter_patterns=laughter_patterns)
    fast = metrics.filter(
        (pl.col("replies") >= MIN_REPLIES_FASTEST) & pl.col("reply_median_ms").is_not_null()
    ).sort(["reply_median_ms", "sender_id"])
    if fast.height:
        r = fast.row(0, named=True)
        out.fastest_replier = (int(r["sender_id"]), float(r["reply_median_ms"]), int(r["replies"]))

    rx = (
        reactions.join(counted.lazy().select("message_id"), on="message_id")
        .group_by("message_id")
        .agg(pl.len().alias("n"), pl.col("emoji"))
        .sort(["n", "message_id"], descending=[True, False])
        .head(1)
        .collect()
    )
    if rx.height:
        mid, n, emojis = rx.row(0)
        top = [e for e, _ in Counter(emojis).most_common(3)]
        out.most_reacted = (int(mid), int(n), top)

    calls = counted.filter(
        (pl.col("type") == "call") & (pl.col("call_duration_s").fill_null(0) > 0)
    ).sort(["call_duration_s", "message_id"], descending=[True, False])
    if calls.height:
        c = calls.row(0, named=True)
        out.longest_call = (int(c["message_id"]), int(c["call_duration_s"]) * 1000)

    out.awards = compute_awards(metrics)
    if owner_id is not None:
        out.my_awards = awards_won_by(out.awards, owner_id)
    return out


def years_with_data(messages: pl.LazyFrame) -> list[tuple[int, int]]:
    """``(year, counted messages)`` ascending, for years with ≥ 1 message."""
    df = (
        messages.filter(is_counted_message())
        .group_by(pl.col("local_date").dt.year().alias("y"))
        .agg(pl.len().alias("n"))
        .sort("y")
        .collect()
    )
    return [(int(y), int(n)) for y, n in df.iter_rows()]
