"""Awards / badges (spec §7.2 "Awards tab"), computed from a :func:`person_metrics` table.

Each award picks the person with the best value of one metric. Ties are allowed (all
people sharing the best value win). To keep tiny contributors from winning, each award
has a minimum-activity threshold; people below it are not eligible:

=====================  ============================  ==========================================
Award                  Metric (direction)            Eligibility
=====================  ============================  ==========================================
📚 The Novelist        avg_words (max)               ≥ 20 text messages
⚡ Speed Demon         reply_median_ms (min)         ≥ 5 replies
🦉 Night Owl           night_share (max, > 0)        ≥ 20 messages
🐓 Early Bird          early_share (max, > 0)        ≥ 20 messages
🎤 Conversation        initiation_rate (max, > 0)    ≥ 5 sessions taken part in, ≥ 20 messages
   Starter
🔁 Double Texter       msgs_per_turn (max)           ≥ 10 turns, ≥ 20 messages
👻 The Ghost           unanswered_turns (max, > 0)   ≥ 20 messages
😂 Comedian            laughs_caused (max, > 0)      —
🤡 Laugher             laugh_messages (max, > 0)     —
🎞️ Reel Dealer         reels_posts (max, > 0)        —
🎙️ Podcaster           voice_notes (max, > 0)        —
💖 Heart Giver         hearts_given (max, > 0)       —
🌟 Crowd Favorite      reactions_per_message (max)   ≥ 20 messages, ≥ 1 reaction received
🧠 Wordsmith           mattr (max)                   ≥ 100 tokens
😎 Emoji Overlord      emojis_per_message (max, >0)  ≥ 20 messages
📞 Caller              calls_started (max, > 0)      —
❓ The Curious         question_rate (max, > 0)      ≥ 20 text messages
🗑️ Second Thoughts     unsent (max, > 0)             —
=====================  ============================  ==========================================

Count-based awards only require the value to be > 0. "—" = no extra threshold.
"""

import math
from dataclasses import dataclass, field
from typing import Literal, TypeGuard

import polars as pl

from instagraph.store.dataset import MessageType

MIN_MESSAGES = 20
MIN_TEXT_MESSAGES = 20
MIN_REPLIES = 5
MIN_SESSIONS = 5
MIN_TURNS = 10
MIN_TOKENS = 100
RANKING_SIZE = 5
_EPS = 1e-9


@dataclass(frozen=True)
class AwardDef:
    key: str
    emoji: str
    title: str
    rule: str
    metric: str
    unit: Literal["count", "ratio", "ms", "per_message"]
    lower_is_better: bool = False
    require_positive: bool = True
    #: ``{column: minimum}`` all of which must hold for eligibility.
    thresholds: tuple[tuple[str, float], ...] = ()
    #: Message types for a Message Browser drilldown (winner's messages), if meaningful.
    drill_types: tuple[MessageType, ...] | None = None
    drill_share_kind: str | None = None

    def eligibility(self) -> str:
        labels = {
            "messages": "messages",
            "text_messages": "text messages",
            "replies": "replies",
            "sessions": "sessions",
            "turns": "turns",
            "tokens": "words",
            "reactions_received": "reactions received",
        }
        parts = [f"≥ {int(v)} {labels.get(c, c)}" for c, v in self.thresholds]
        return ", ".join(parts) if parts else "at least one"


_M = ("messages", float(MIN_MESSAGES))
_TM = ("text_messages", float(MIN_TEXT_MESSAGES))

AWARDS: tuple[AwardDef, ...] = (
    AwardDef("novelist", "📚", "The Novelist", "Highest average words per message",
             "avg_words", "per_message", thresholds=(_TM,)),
    AwardDef("speed_demon", "⚡", "Speed Demon", "Lowest median reply time",
             "reply_median_ms", "ms", lower_is_better=True, require_positive=False,
             thresholds=(("replies", float(MIN_REPLIES)),)),
    AwardDef("night_owl", "🦉", "Night Owl", "Highest share of messages between 00:00 and 05:00",
             "night_share", "ratio", thresholds=(_M,)),
    AwardDef("early_bird", "🐓", "Early Bird", "Highest share of messages between 05:00 and 08:00",
             "early_share", "ratio", thresholds=(_M,)),
    AwardDef("conversation_starter", "🎤", "Conversation Starter", "Highest initiation rate",
             "initiation_rate", "ratio",
             thresholds=(("sessions", float(MIN_SESSIONS)), _M)),
    AwardDef("double_texter", "🔁", "Double Texter", "Most messages per turn",
             "msgs_per_turn", "per_message", thresholds=(("turns", float(MIN_TURNS)), _M)),
    AwardDef("ghost", "👻", "The Ghost", "Most unanswered turns",
             "unanswered_turns", "count", thresholds=(_M,)),
    AwardDef("comedian", "😂", "Comedian", "Causes the most laughter",
             "laughs_caused", "count"),
    AwardDef("laugher", "🤡", "Laugher", "Laughs the most", "laugh_messages", "count"),
    AwardDef("reel_dealer", "🎞️", "Reel Dealer", "Most reels/posts shared",
             "reels_posts", "count", drill_types=("share",)),
    AwardDef("podcaster", "🎙️", "Podcaster", "Most voice notes",
             "voice_notes", "count", drill_types=("audio",)),
    AwardDef("heart_giver", "💖", "Heart Giver", "Most heart reactions given",
             "hearts_given", "count"),
    AwardDef("crowd_favorite", "🌟", "Crowd Favorite", "Most reactions received per message",
             "reactions_per_message", "per_message",
             thresholds=(_M, ("reactions_received", 1.0))),
    AwardDef("wordsmith", "🧠", "Wordsmith", "Highest lexical diversity (MATTR)",
             "mattr", "ratio", thresholds=(("tokens", float(MIN_TOKENS)),)),
    AwardDef("emoji_overlord", "😎", "Emoji Overlord", "Most emojis per message",
             "emojis_per_message", "per_message", thresholds=(_M,)),
    AwardDef("caller", "📞", "Caller", "Most calls started",
             "calls_started", "count", drill_types=("call",)),
    AwardDef("curious", "❓", "The Curious", "Highest question rate",
             "question_rate", "ratio", thresholds=(_TM,)),
    AwardDef("second_thoughts", "🗑️", "Second Thoughts", "Most unsent messages",
             "unsent", "count", drill_types=("unsent",)),
)  # fmt: skip

AWARDS_BY_KEY: dict[str, AwardDef] = {a.key: a for a in AWARDS}


@dataclass
class AwardResult:
    award: AwardDef
    winners: list[int] = field(default_factory=list)
    value: float | None = None
    #: Top eligible people ``(person_id, value)`` best first (incl. winners).
    ranking: list[tuple[int, float]] = field(default_factory=list)


def _valid(v: object) -> TypeGuard[int | float]:
    return isinstance(v, int | float) and not math.isnan(float(v))


def compute_awards(
    metrics: pl.DataFrame,
    *,
    candidates: set[int] | None = None,
    awards: tuple[AwardDef, ...] = AWARDS,
) -> list[AwardResult]:
    """Evaluate every award over a :func:`person_metrics` table.

    ``candidates`` limits who can win (e.g. chat participants); ``None`` = everyone.
    Awards nobody qualifies for are returned with empty ``winners``.
    """
    rows = [
        r
        for r in metrics.iter_rows(named=True)
        if candidates is None or r["sender_id"] in candidates
    ]
    results: list[AwardResult] = []
    for a in awards:
        scored: list[tuple[int, float]] = []
        for r in rows:
            v = r.get(a.metric)
            if not _valid(v):
                continue
            fv = float(v)
            if a.require_positive and fv <= 0:
                continue
            if any(not _valid(r.get(c)) or float(r[c]) < m for c, m in a.thresholds):
                continue
            scored.append((int(r["sender_id"]), fv))
        scored.sort(key=lambda t: (t[1] if a.lower_is_better else -t[1], t[0]))
        res = AwardResult(award=a, ranking=scored[:RANKING_SIZE])
        if scored:
            best = scored[0][1]
            res.value = best
            res.winners = [pid for pid, v in scored if abs(v - best) <= _EPS * max(1.0, abs(best))]
        results.append(res)
    return results


def awards_won_by(results: list[AwardResult], person_id: int) -> list[AwardResult]:
    """The subset of ``results`` that ``person_id`` won."""
    return [r for r in results if person_id in r.winners]


def personality_summary(won: list[AwardResult]) -> str:
    """One-line "your personality" text from the awards someone won (§5.10)."""
    if not won:
        return "A quiet legend: no awards this time, but every chat needs a good listener."
    titles = [f"{r.award.emoji} {r.award.title}" for r in won]
    if len(titles) == 1:
        return f"You are {titles[0]}."
    return "You are " + ", ".join(titles[:-1]) + f" and {titles[-1]}."
