"""Words tab (spec §6.5, §7.2) and term tracker (§8.2 ``/terms``) response models.

Anonymize mode: every list made of the user's words (top words, n-grams, distinctive
words, repeated messages, mentions, hashtags) is returned EMPTY and ``text_hidden`` is
true; message snippets are null. Numeric stats (counts, rates, MATTR, laughter, length
distribution) are kept. The term tracker still works (the user typed the terms), but
its message snippets are null.
"""

from typing import Literal

from pydantic import Field

from instagraph.schemas.common import (
    ApiModel,
    ApiResponse,
    CategoryChart,
    MessageQuery,
    MessageRef,
    PersonRef,
    TimeSeries,
)

TermMode = Literal["plain", "word", "regex"]


class PersonCount(ApiModel):
    person: PersonRef
    count: int
    drilldown: MessageQuery | None = None


class TermCount(ApiModel):
    """A word / phrase / n-gram with its count (and score for distinctive words)."""

    term: str
    count: int
    score: float | None = Field(default=None, description="Log-odds z-score or TF-IDF")
    drilldown: MessageQuery | None = None


class PersonTerms(ApiModel):
    person: PersonRef
    terms: list[TermCount]


class TermLists(ApiModel):
    """Overall list plus one list per person."""

    overall: list[TermCount]
    by_person: list[PersonTerms]


class DistinctiveWords(ApiModel):
    method: Literal["log_odds", "tfidf"] = Field(
        description="log_odds = weighted log-odds with informative Dirichlet prior "
        "(Monroe et al.); tfidf = fallback when fewer than 2 people wrote"
    )
    by_person: list[PersonTerms]


class PersonTextStats(ApiModel):
    """One row of the per-person Words table (text messages only)."""

    person: PersonRef
    text_messages: int
    words: int
    avg_words_per_message: float
    vocabulary_size: int = Field(description="Unique lower-case words (stop-words included)")
    lexical_diversity: float = Field(description="MATTR, window 500 (0..1)")
    question_rate: float
    exclamation_rate: float
    caps_rate: float
    ellipsis_rate: float
    laugh_messages: int
    laughs_per_100: float
    laughs_caused: int = Field(description="Laughter messages credited to this person")
    links: int
    mentions: int
    hashtags: int
    swear_words: int | None = Field(default=None, description="null unless enabled")
    drilldown: MessageQuery


class LaughPair(ApiModel):
    """``laugher`` laughed within 2 minutes after ``causer``'s message ``count`` times."""

    laugher: PersonRef
    causer: PersonRef
    count: int


class LaughterStats(ApiModel):
    patterns: list[str]
    invalid_patterns: list[str] = Field(description="Patterns skipped (not valid regex)")
    chart: CategoryChart = Field(
        description="categories = people; series 'laughs' (sent) and 'caused'"
    )
    pairs: list[LaughPair]


class RepeatedMessage(ApiModel):
    text: str | None
    count: int
    by_person: list[PersonCount]
    drilldown: MessageQuery | None = None


class PersonMessages(ApiModel):
    person: PersonRef
    messages: list[MessageRef]
    drilldown: MessageQuery | None = None


class SwearStats(ApiModel):
    by_person: list[PersonCount]
    top: list[TermCount]


class WordsResponse(ApiResponse):
    chat_id: int
    text_hidden: bool = Field(description="True in anonymize mode: word lists are empty")
    accent_insensitive: bool
    people: list[PersonTextStats]
    top_words: TermLists
    bigrams: TermLists
    trigrams: TermLists
    distinctive: DistinctiveWords
    rates: CategoryChart = Field(
        description="categories: question, exclamation, caps, ellipsis; one series per person"
    )
    length_distribution: CategoryChart = Field(
        description="Word-count bins; one series per person (message counts)"
    )
    laughter: LaughterStats
    top_mentions: list[TermCount]
    top_hashtags: list[TermCount]
    repeated_messages: list[RepeatedMessage]
    longest_messages: list[PersonMessages]
    swear: SwearStats | None = Field(default=None, description="null unless enabled")
    saved_terms: list[str] = Field(description="Term-tracker terms saved for this chat")


class TermResult(ApiModel):
    term: str
    occurrences: int
    messages: int
    by_person: list[PersonCount]
    first: MessageRef | None = None
    last: MessageRef | None = None
    timeline: TimeSeries = Field(description="Occurrences per bucket, one series per person")
    drilldown: MessageQuery


class TermsResponse(ApiResponse):
    chat_id: int
    mode: TermMode
    case_sensitive: bool
    accent_insensitive: bool
    terms: list[TermResult]
