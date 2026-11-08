"""Text analysis (spec §6.5, chart catalogue §7.2 "Words tab").

All functions take message frames produced by
:func:`instagraph.api.filters.filtered_messages` (``LazyFrame`` or ``DataFrame``) and
are vectorized with Polars string ops (no per-row Python on the hot path).

Text stats only use :func:`~instagraph.api.filters.is_text_message` rows; the helpers
below apply that filter themselves (passing already-filtered frames is fine).

Tokenization (§6.5)
-------------------
lower-case → URLs removed → ``@mentions`` removed → emoji residue removed (keycaps,
U+FE0F, ZWJ: these are combining marks and would otherwise form "words") →
``textproc.WORD_RE`` tokens → ``’`` normalized to ``'``. Diacritics are kept for display;
``accent_insensitive=True`` folds tokens (NFKD + drop combining marks), so ``ahoj`` and
``áhoj`` count as one word (shown with its most frequent spelling).

Reusable API (for awards / wrapped / compare)
----------------------------------------------
* :func:`avg_words_per_message(msgs) -> dict[int, float]`
* :func:`lexical_diversity(msgs, window=500) -> dict[int, float]` (MATTR)
* :func:`vocabulary_size(msgs) -> dict[int, int]`
* :func:`question_rate(msgs) -> dict[int, float]`, :func:`message_rates(msgs)`
* :func:`laughter_stats(msgs, patterns) -> (laughs_by_person, caused_by_person)`
* :func:`top_words(msgs, stopwords, n=50) -> list[tuple[str, int]]` (one top word per
  scope: e.g. Wrapped "top word")
* :func:`tokens_frame`, :func:`count_terms`, :func:`top_terms_by_group`,
  :func:`log_odds_dirichlet`, :func:`tfidf_scores` — lower-level building blocks.

All ``dict[int, …]`` results are keyed by ``sender_id`` (or by ``group_col``).
"""

import re
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date

import polars as pl

from instagraph.api.filters import bucket_start, is_counted_message, is_text_message
from instagraph.textproc import WORD_PATTERN

Frame = pl.LazyFrame | pl.DataFrame

#: §6.1 word regex (shared with ``textproc.WORD_RE``; valid in Polars/Rust syntax).
WORD_PL = WORD_PATTERN
URL_PL = r"(?i)\b(?:https?://|www\.)[^\s<>\"']+"
#: ``textproc.MENTION_RE`` without look-behind (the leading char is consumed).
MENTION_PL = r"(?:^|[^\w@])@\w(?:[\w.]*\w)?"
#: ``textproc.HASHTAG_RE`` without look-behind.
HASHTAG_PL = r"(?:^|[^\w#&])#[\p{L}\p{M}\p{N}_]+"
#: Emoji residue that WORD_PL would otherwise match (keycaps, VS16, ZWJ, tags).
EMOJI_RESIDUE_PL = r"[0-9#*]\x{FE0F}?\x{20E3}|[\x{FE0F}\x{200D}\x{20E3}\x{E0020}-\x{E007F}]"
NUMERIC_PL = r"^\p{N}+$"

LAUGH_WINDOW_MS = 2 * 60 * 1000
#: ``{sender_id | None (overall): [(term, count)]}``
TermList = dict[int | None, list[tuple[str, int]]]
MATTR_WINDOW = 500
REPEATED_MAX_CHARS = 200

#: Word-count bins for the message length distribution (inclusive lower bounds).
LENGTH_BINS: tuple[tuple[str, int, int | None], ...] = (
    ("1", 1, 1),
    ("2", 2, 2),
    ("3", 3, 3),
    ("4", 4, 4),
    ("5", 5, 5),
    ("6-10", 6, 10),
    ("11-20", 11, 20),
    ("21-50", 21, 50),
    ("51-100", 51, 100),
    ("100+", 101, None),
)


def _lazy(msgs: Frame) -> pl.LazyFrame:
    return msgs.lazy() if isinstance(msgs, pl.DataFrame) else msgs


def _eager(msgs: Frame) -> pl.DataFrame:
    return msgs if isinstance(msgs, pl.DataFrame) else msgs.collect()


def text_messages(msgs: Frame) -> pl.LazyFrame:
    """``msgs`` restricted to text-stat rows (§6.1)."""
    return _lazy(msgs).filter(is_text_message())


def fold_accents_expr(expr: pl.Expr) -> pl.Expr:
    """Polars :func:`instagraph.textproc.strip_accents` (NFKD + drop combining marks)."""
    return expr.str.normalize("NFKD").str.replace_all(r"\p{Mn}", "")


def clean_text_expr(expr: pl.Expr) -> pl.Expr:
    """Text with URLs, @mentions and emoji residue removed (case unchanged)."""
    return (
        expr.str.replace_all(URL_PL, " ")
        .str.replace_all(MENTION_PL, " ")
        .str.replace_all(EMOJI_RESIDUE_PL, " ")
    )


def _ratio(a: float, b: float) -> float:
    return a / b if b else 0.0


# --------------------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------------------


def tokens_frame(
    msgs: Frame,
    *,
    accent_insensitive: bool = False,
    extra_cols: Iterable[str] = (),
) -> pl.LazyFrame:
    """One row per token of every text message, in message order.

    Columns: ``message_id, sender_id, token`` (lower-case, display form), ``key``
    (= ``token``, or its accent-folded form when ``accent_insensitive``) +
    ``extra_cols``.
    """
    cols = ["message_id", "sender_id", *extra_cols]
    tok = (
        text_messages(msgs)
        .select(
            *cols,
            clean_text_expr(pl.col("text"))
            .str.replace_all("’", "'", literal=True)
            .str.to_lowercase()
            .str.extract_all(WORD_PL)
            .alias("token"),
        )
        .explode("token")
        .filter(pl.col("token").is_not_null())
    )
    key = fold_accents_expr(pl.col("token")) if accent_insensitive else pl.col("token")
    return tok.with_columns(key.alias("key"))


def _content_filter(stopwords: frozenset[str]) -> pl.Expr:
    """Tokens that are interesting for top-word lists: not a stop-word (incl. its
    folded form), not purely numeric, ≥ 2 characters."""
    sw = list(stopwords)
    return (
        ~pl.col("token").is_in(sw)
        & ~pl.col("key").is_in(sw)
        & ~pl.col("token").str.contains(NUMERIC_PL)
        & (pl.col("token").str.len_chars() >= 2)
    )


def display_map(tokens: pl.DataFrame) -> pl.DataFrame:
    """``key -> display`` (most frequent spelling of each key; ties alphabetical)."""
    return (
        tokens.group_by("key", "token")
        .len()
        .sort(["len", "token"], descending=[True, False])
        .group_by("key", maintain_order=True)
        .first()
        .select("key", pl.col("token").alias("display"))
    )


def count_terms(tokens: pl.DataFrame, group_col: str | None = "sender_id") -> pl.DataFrame:
    """Counts of ``key`` per group (``group_col=None`` → overall): ``[group], key, n``."""
    by = [group_col, "key"] if group_col else ["key"]
    return tokens.group_by(by).agg(pl.len().alias("n").cast(pl.Int64))


def top_terms_by_group(
    counts: pl.DataFrame, n: int, group_col: str | None = "sender_id", min_count: int = 1
) -> dict[int | None, list[tuple[str, int]]]:
    """Top ``n`` keys per group from :func:`count_terms` output (ties alphabetical)."""
    df = counts.filter(pl.col("n") >= min_count).sort(
        ["n", "key"], descending=[True, False], maintain_order=False
    )
    out: dict[int | None, list[tuple[str, int]]] = {}
    if group_col is None:
        out[None] = [(k, int(c)) for k, c in df.head(n).select("key", "n").iter_rows()]
        return out
    top = df.group_by(group_col, maintain_order=True).head(n)
    for g, k, c in top.select(group_col, "key", "n").iter_rows():
        out.setdefault(int(g), []).append((str(k), int(c)))
    return out


def annotate_tokens(tokens: pl.DataFrame, stopwords: frozenset[str]) -> pl.DataFrame:
    """Add ``h`` (UInt64 hash of ``key``, used as an integer code) and ``stop``
    (stop-word, incl. folded form, or a number) to a :func:`tokens_frame` result."""
    sw = list(stopwords)
    return tokens.with_columns(
        pl.col("key").hash(seed=7).alias("h"),
        (
            pl.col("token").is_in(sw)
            | pl.col("key").is_in(sw)
            | pl.col("token").str.contains(NUMERIC_PL)
        ).alias("stop"),
    )


def top_ngrams(
    tokens: pl.DataFrame,
    n: int,
    stopwords: frozenset[str],
    top: int = 20,
    min_count: int = 2,
    display: dict[str, str] | None = None,
) -> TermList:
    """Top ``top`` n-grams (n = 2 or 3, within one message) per sender and overall
    (key ``None``), used ≥ ``min_count`` times. n-grams made only of stop-words /
    numbers are dropped; terms are the ``display`` forms of the keys joined with
    spaces. ``tokens`` may be pre-annotated with :func:`annotate_tokens`.

    Grouping runs on integer key hashes (no string concatenation) for speed.
    """
    out: TermList = {}
    if tokens.is_empty():
        return out
    if "h" not in tokens.columns:
        tokens = annotate_tokens(tokens, stopwords)
    hcols = [f"h{k}" for k in range(n)]
    same = pl.lit(True)
    all_stop = pl.col("stop")
    shifted = [pl.col("h").alias("h0")]
    for k in range(1, n):
        shifted.append(pl.col("h").shift(-k).alias(f"h{k}"))
        same &= pl.col("message_id").shift(-k) == pl.col("message_id")
        all_stop &= pl.col("stop").shift(-k)
    per = (
        tokens.lazy()
        .select("sender_id", *shifted, (same & ~all_stop).alias("ok"))
        .filter(pl.col("ok"))
        .group_by("sender_id", *hcols)
        .agg(pl.len().alias("n").cast(pl.Int64))
        .collect()
    )
    top_per = (
        per.lazy()
        .filter(pl.col("n") >= min_count)
        .sort(["n", *hcols], descending=[True] + [False] * n)
        .group_by("sender_id", maintain_order=True)
        .head(top)
    )
    top_all = (
        per.lazy()
        .group_by(hcols)
        .agg(pl.col("n").sum())
        .filter(pl.col("n") >= min_count)
        .sort(["n", *hcols], descending=[True] + [False] * n)
        .head(top)
    )
    res_per, res_all = pl.collect_all([top_per, top_all])
    needed: set[int] = set()
    for frame in (res_per, res_all):
        for c in hcols:
            needed.update(frame[c].to_list())
    names: dict[int, str] = dict(
        tokens.lazy()
        .select("h", "key")
        .filter(pl.col("h").is_in(list(needed)))
        .unique("h")
        .collect()
        .iter_rows()
    )
    disp = display or {}

    def decode(hs: tuple[int, ...]) -> str:
        return " ".join(disp.get(names[h], names[h]) for h in hs)

    for row in res_per.select("sender_id", *hcols, "n").iter_rows():
        out.setdefault(int(row[0]), []).append((decode(row[1:-1]), int(row[-1])))
    out[None] = [(decode(r[:-1]), int(r[-1])) for r in res_all.select(*hcols, "n").iter_rows()]
    return out


def top_words(
    msgs: Frame, stopwords: frozenset[str], n: int = 50, *, accent_insensitive: bool = False
) -> list[tuple[str, int]]:
    """Top ``n`` content words over all of ``msgs`` (stop-words removed), as
    ``(word, count)``. E.g. Wrapped's "top word" = ``top_words(msgs, sw, 1)``."""
    tok = tokens_frame(msgs, accent_insensitive=accent_insensitive).collect()
    tok = tok.filter(_content_filter(stopwords))
    if tok.is_empty():
        return []
    disp = dict(display_map(tok).iter_rows())
    top = top_terms_by_group(count_terms(tok, None), n, None)[None]
    return [(disp.get(k, k), c) for k, c in top]


# --------------------------------------------------------------------------------------
# Distinctive words (Monroe et al. 2008)
# --------------------------------------------------------------------------------------


def log_odds_dirichlet(counts: pl.DataFrame, group_col: str = "sender_id") -> pl.DataFrame:
    """Weighted log-odds ratio with an informative Dirichlet prior (Monroe et al.).

    ``counts``: ``group_col, key, n``. Each group is compared against all other groups;
    the prior ``a_w`` is the term's count over the whole corpus (``a0 = N``).

    ``d_w = log((y_iw+a_w)/(n_i+a0-y_iw-a_w)) - log((y_jw+a_w)/(n_j+a0-y_jw-a_w))``,
    ``s²_w ≈ 1/(y_iw+a_w) + 1/(y_jw+a_w)``, ``score = d_w/s_w`` (z-score).

    Returns ``group_col, key, n, score`` (all rows; filter/sort as needed).
    """
    if counts.is_empty():
        return counts.with_columns(pl.lit(0.0).alias("score"))
    total = float(counts["n"].sum())
    alpha = counts.group_by("key").agg(pl.col("n").sum().cast(pl.Float64).alias("alpha"))
    group_tot = counts.group_by(group_col).agg(pl.col("n").sum().cast(pl.Float64).alias("n_i"))
    df = counts.join(alpha, on="key").join(group_tot, on=group_col)
    yi = pl.col("n").cast(pl.Float64)
    a = pl.col("alpha")
    yj = a - yi  # rest-of-corpus count of the term
    ni = pl.col("n_i")
    nj = total - ni
    a0 = total
    delta = ((yi + a) / (ni + a0 - yi - a)).log() - ((yj + a) / (nj + a0 - yj - a)).log()
    var = 1.0 / (yi + a) + 1.0 / (yj + a)
    return df.with_columns((delta / var.sqrt()).fill_nan(0.0).alias("score")).select(
        group_col, "key", "n", "score"
    )


def tfidf_scores(counts: pl.DataFrame, group_col: str = "sender_id") -> pl.DataFrame:
    """TF-IDF across groups (fallback for distinctive words): ``tf = n / n_group``,
    ``idf = ln((1+G)/(1+df)) + 1``. Returns ``group_col, key, n, score``."""
    if counts.is_empty():
        return counts.with_columns(pl.lit(0.0).alias("score"))
    groups = counts[group_col].n_unique()
    dfreq = counts.group_by("key").agg(pl.len().alias("df"))
    tot = counts.group_by(group_col).agg(pl.col("n").sum().alias("n_i"))
    return (
        counts.join(dfreq, on="key")
        .join(tot, on=group_col)
        .with_columns(
            (pl.col("n") / pl.col("n_i") * (((1 + groups) / (1 + pl.col("df"))).log() + 1.0)).alias(
                "score"
            )
        )
        .select(group_col, "key", "n", "score")
    )


def distinctive_terms(
    counts: pl.DataFrame, n: int = 25, group_col: str = "sender_id", min_count: int = 2
) -> tuple[str, dict[int, list[tuple[str, int, float]]]]:
    """Top ``n`` distinctive keys per group: ``(method, {group: [(key, count, score)]})``.

    ``method`` is ``"log_odds"`` when ≥ 2 groups have words, else ``"tfidf"``.
    Only positive scores and terms used ≥ ``min_count`` times by the group are kept.
    """
    groups = counts[group_col].n_unique() if not counts.is_empty() else 0
    if groups >= 2:
        method, scored = "log_odds", log_odds_dirichlet(counts, group_col)
    else:
        method, scored = "tfidf", tfidf_scores(counts, group_col)
    top = (
        scored.filter((pl.col("n") >= min_count) & (pl.col("score") > 0))
        .sort(["score", "key"], descending=[True, False])
        .group_by(group_col, maintain_order=True)
        .head(n)
    )
    out: dict[int, list[tuple[str, int, float]]] = {}
    for g, k, c, s in top.select(group_col, "key", "n", "score").iter_rows():
        out.setdefault(int(g), []).append((str(k), int(c), float(s)))
    return method, out


# --------------------------------------------------------------------------------------
# Vocabulary and lexical diversity
# --------------------------------------------------------------------------------------


def mattr_from_tokens(
    tokens: pl.DataFrame, window: int = MATTR_WINDOW, group_col: str = "sender_id"
) -> dict[int, float]:
    """MATTR per group (see :func:`mattr_and_vocabulary`)."""
    return mattr_and_vocabulary(tokens, window, group_col)[0]


def mattr_and_vocabulary(
    tokens: pl.DataFrame, window: int = MATTR_WINDOW, group_col: str = "sender_id"
) -> tuple[dict[int, float], dict[int, int]]:
    """Moving-average type-token ratio and vocabulary size (unique keys) per group,
    fully vectorized.

    Token ``i`` (previous occurrence of the same type at ``p``, or -1) is new in every
    window start ``s`` with ``max(p+1, i-W+1, 0) ≤ s ≤ min(i, N-W)``; summing these
    counts gives Σ(types per window) → MATTR = Σ / (W·(N-W+1)). Groups with fewer
    than ``W`` tokens get the plain TTR.
    """
    if tokens.is_empty():
        return {}, {}
    w = window
    h = pl.col("h") if "h" in tokens.columns else pl.col("key").hash(seed=7)
    df = (
        tokens.lazy()
        .select(group_col, h.alias("h"))
        .with_columns(
            pl.int_range(pl.len()).over(group_col).alias("i"),
            pl.len().over(group_col).cast(pl.Int64).alias("N"),
        )
        .with_columns(pl.col("i").shift(1).over([group_col, "h"]).fill_null(-1).alias("p"))
    )
    lo = pl.max_horizontal(pl.col("p") + 1, pl.col("i") - w + 1, pl.lit(0))
    hi = pl.min_horizontal(pl.col("i"), pl.col("N") - w)
    agg = (
        df.with_columns(pl.max_horizontal(hi - lo + 1, pl.lit(0)).alias("c"))
        .group_by(group_col)
        .agg(
            pl.col("c").sum().alias("c"),
            pl.col("N").first().alias("N"),
            (pl.col("p") < 0).sum().alias("types"),
        )
        .collect()
    )
    out: dict[int, float] = {}
    vocab: dict[int, int] = {}
    for g, c, n, types in agg.select(group_col, "c", "N", "types").iter_rows():
        out[int(g)] = types / n if n < w else c / (w * (n - w + 1))
        vocab[int(g)] = int(types)
    return out, vocab


def lexical_diversity(msgs: Frame, window: int = MATTR_WINDOW) -> dict[int, float]:
    """MATTR (window 500) per sender over all tokens (stop-words included)."""
    return mattr_from_tokens(tokens_frame(msgs).collect(), window)


def vocabulary_size(msgs: Frame, *, accent_insensitive: bool = False) -> dict[int, int]:
    """Unique lower-case words per sender (stop-words included)."""
    tok = tokens_frame(msgs, accent_insensitive=accent_insensitive)
    df = tok.group_by("sender_id").agg(pl.col("key").n_unique().alias("v")).collect()
    return {int(g): int(v) for g, v in df.iter_rows()}


# --------------------------------------------------------------------------------------
# Per-message rates
# --------------------------------------------------------------------------------------


def rate_flags_exprs() -> list[pl.Expr]:
    """Boolean columns ``is_question``, ``is_exclamation``, ``is_caps``, ``is_ellipsis``
    for a text frame (§6.5). ALL CAPS = ≥ 3 upper-case letters and no lower-case ones
    (URLs / mentions ignored)."""
    clean = clean_text_expr(pl.col("text"))
    return [
        pl.col("text").str.contains("?", literal=True).alias("is_question"),
        pl.col("text").str.contains("!", literal=True).alias("is_exclamation"),
        (
            (clean.str.count_matches(r"\p{Lu}") >= 3) & (clean.str.count_matches(r"\p{Ll}") == 0)
        ).alias("is_caps"),
        (
            pl.col("text").str.contains("...", literal=True)
            | pl.col("text").str.contains("…", literal=True)
        ).alias("is_ellipsis"),
    ]


def message_rates(msgs: Frame) -> dict[int, dict[str, float]]:
    """Per sender: ``question``, ``exclamation``, ``caps``, ``ellipsis`` rates (0..1)
    over text messages."""
    df = (
        text_messages(msgs)
        .select("sender_id", *rate_flags_exprs())
        .group_by("sender_id")
        .agg(
            pl.col("is_question").mean().alias("question"),
            pl.col("is_exclamation").mean().alias("exclamation"),
            pl.col("is_caps").mean().alias("caps"),
            pl.col("is_ellipsis").mean().alias("ellipsis"),
        )
        .collect()
    )
    return {
        int(r["sender_id"]): {
            k: float(r[k]) for k in ("question", "exclamation", "caps", "ellipsis")
        }
        for r in df.iter_rows(named=True)
    }


def question_rate(msgs: Frame) -> dict[int, float]:
    """Messages containing ``?`` / text messages, per sender."""
    return {k: v["question"] for k, v in message_rates(msgs).items()}


def avg_words_per_message(msgs: Frame) -> dict[int, float]:
    """Mean ``word_count`` of text messages per sender."""
    df = (
        text_messages(msgs)
        .group_by("sender_id")
        .agg(pl.col("word_count").mean().alias("m"))
        .collect()
    )
    return {int(g): float(m) for g, m in df.iter_rows()}


# --------------------------------------------------------------------------------------
# Laughter
# --------------------------------------------------------------------------------------


def _pattern_ok(p: str) -> bool:
    try:
        pl.select(pl.lit("x").str.contains(p))
    except pl.exceptions.PolarsError:
        return False
    return True


def compile_laughter(patterns: Iterable[str]) -> tuple[str | None, list[str]]:
    """Combine patterns into one case-insensitive Rust regex.

    Returns ``(regex or None if no valid pattern, invalid_patterns)``. Patterns that
    are not valid Rust regexes (e.g. look-around) are reported and skipped.
    """
    valid: list[str] = []
    invalid: list[str] = []
    for p in patterns:
        if not p:
            continue
        (valid if _pattern_ok(p) else invalid).append(p)
    if not valid:
        return None, invalid
    return "(?i)(?:" + "|".join(f"(?:{p})" for p in valid) + ")", invalid


_BOUNDARY_RE = re.compile(r"(?<!\\)((?:\\\\)*)\\b(?:\{[a-z-]+\})?")


def prefilter_regex(regex: str) -> str | None:
    """``regex`` without word-boundary assertions (a cheap superset filter).

    Unicode ``\\b`` makes the Rust regex engine fall back to a slow path; matching
    the boundary-free pattern first and the exact one only on candidate rows is
    ~20x faster. ``None`` if there is nothing to strip (or the result is invalid).
    """
    pre = _BOUNDARY_RE.sub(r"\1", regex)
    if pre == regex or not _pattern_ok(pre):
        return None
    return pre


def regex_hits(df: pl.DataFrame, text: pl.Expr, regex: str) -> pl.DataFrame:
    """Rows of ``df`` whose ``text`` expression matches ``regex``:
    ``message_id, n`` (number of non-overlapping matches, > 0)."""
    cand = df.select("message_id", text.alias("_t"))
    pre = prefilter_regex(regex)
    if pre is not None:
        cand = cand.filter(pl.col("_t").str.contains(pre))
    return (
        cand.with_columns(pl.col("_t").str.count_matches(regex).alias("n"))
        .filter(pl.col("n") > 0)
        .select("message_id", "n")
    )


def laughter_hits(text_df: pl.DataFrame, patterns: Iterable[str]) -> pl.DataFrame:
    """Laughter messages among ``text_df`` rows: ``message_id, n`` (pattern matches).

    Patterns with word boundaries (slow in Rust regex on non-ASCII text) run only on
    rows pre-selected by their boundary-free version; the rest run directly.
    """
    valid = [p for p in patterns if p and _pattern_ok(p)]
    slow = [p for p in valid if prefilter_regex(p) is not None]
    fast = [p for p in valid if p not in slow]
    frames = []
    for group in (fast, slow):
        if group:
            rx = "(?i)(?:" + "|".join(f"(?:{p})" for p in group) + ")"
            frames.append(regex_hits(text_df, pl.col("text"), rx))
    if not frames:
        return pl.DataFrame(schema={"message_id": pl.UInt32, "n": pl.UInt32})
    return pl.concat(frames).group_by("message_id").agg(pl.col("n").sum())


@dataclass
class LaughterResult:
    """Laughter per person (§6.5).

    ``laughs``: laughter messages per sender; ``matches``: total pattern matches;
    ``caused``: laughter messages credited to a person ("who makes whom laugh");
    ``pairs``: ``(laugher, causer) -> count``; ``text_messages`` per sender.
    """

    laughs: dict[int, int] = field(default_factory=dict)
    matches: dict[int, int] = field(default_factory=dict)
    caused: dict[int, int] = field(default_factory=dict)
    pairs: dict[tuple[int, int], int] = field(default_factory=dict)
    text_messages: dict[int, int] = field(default_factory=dict)
    invalid_patterns: list[str] = field(default_factory=list)

    def per_100(self, pid: int) -> float:
        return 100.0 * _ratio(self.laughs.get(pid, 0), self.text_messages.get(pid, 0))


def laughter_analysis(msgs: Frame, patterns: Iterable[str]) -> LaughterResult:
    """Laughter counts and "who makes whom laugh".

    A laughter message from B is credited to A when A sent the last message before
    B's current run in the same thread and it was ≤ 2 minutes earlier. Context uses
    all counted messages (a reel can be funny too); laughter only text messages.
    """
    patterns = list(patterns)
    _, invalid = compile_laughter(patterns)
    res = LaughterResult(invalid_patterns=invalid)
    ctx = (
        _lazy(msgs)
        .filter(is_counted_message())
        .select(
            "message_id",
            "thread_id",
            "sender_id",
            pl.col("ts").dt.epoch("ms").alias("t"),
            is_text_message().alias("is_text"),
            "text",
        )
        .sort("message_id")
        .collect()
    )
    hits = laughter_hits(ctx.filter(pl.col("is_text")), patterns)
    df = (
        ctx.drop("text")
        .join(hits.rename({"n": "nm"}), on="message_id", how="left", maintain_order="left")
        .with_columns(pl.col("nm").fill_null(0), pl.col("nm").is_not_null().alias("laugh"))
        .with_columns(
            pl.col("sender_id").shift(1).over("thread_id").alias("ps"),
            pl.col("t").shift(1).over("thread_id").alias("pt"),
        )
        .with_columns(
            (pl.col("ps").is_not_null() & (pl.col("ps") != pl.col("sender_id"))).alias("chg")
        )
        .with_columns(
            pl.when(pl.col("chg")).then(pl.col("ps")).forward_fill().over("thread_id").alias("os"),
            pl.when(pl.col("chg")).then(pl.col("pt")).forward_fill().over("thread_id").alias("ot"),
        )
    )
    if df.is_empty():
        return res
    per = df.group_by("sender_id").agg(
        pl.col("is_text").sum().alias("tm"),
        pl.col("laugh").sum().alias("l"),
        pl.col("nm").sum().alias("m"),
    )
    for pid, tm, lc, mc in per.iter_rows():
        res.text_messages[int(pid)] = int(tm)
        res.laughs[int(pid)] = int(lc)
        res.matches[int(pid)] = int(mc)
    credited = df.filter(
        pl.col("laugh")
        & pl.col("os").is_not_null()
        & ((pl.col("t") - pl.col("ot")) <= LAUGH_WINDOW_MS)
    )
    pairs = credited.group_by("sender_id", "os").len()
    for laugher, causer, c in pairs.iter_rows():
        res.pairs[(int(laugher), int(causer))] = int(c)
        res.caused[int(causer)] = res.caused.get(int(causer), 0) + int(c)
    return res


def laughter_stats(msgs: Frame, patterns: Iterable[str]) -> tuple[dict[int, int], dict[int, int]]:
    """``(laughs_by_person, caused_by_person)`` — laughter messages sent per person,
    and laughter messages credited to each person (see :func:`laughter_analysis`)."""
    r = laughter_analysis(msgs, patterns)
    return r.laughs, r.caused


# --------------------------------------------------------------------------------------
# Links, mentions, hashtags
# --------------------------------------------------------------------------------------


def entity_frames(msgs: Frame) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """``(links, mentions, hashtags)`` long frames with ``message_id, sender_id, value``.

    Mentions/hashtags are lower-cased, without ``@``/``#``; links come from the
    ``links`` column.
    """
    base = text_messages(msgs).select(
        "message_id",
        "sender_id",
        "links",
        pl.col("text").str.replace_all(URL_PL, " ").alias("_t"),
    )
    df = base.collect()
    links = (
        df.select("message_id", "sender_id", pl.col("links").alias("value"))
        .explode("value")
        .drop_nulls("value")
    )
    mentions = (
        df.select(
            "message_id",
            "sender_id",
            pl.col("_t").str.extract_all(MENTION_PL).alias("value"),
        )
        .explode("value")
        .drop_nulls("value")
        .with_columns(pl.col("value").str.replace(r"^[^@]*@", "").str.to_lowercase())
    )
    hashtags = (
        df.select(
            "message_id",
            "sender_id",
            pl.col("_t").str.extract_all(HASHTAG_PL).alias("value"),
        )
        .explode("value")
        .drop_nulls("value")
        .with_columns(pl.col("value").str.replace(r"^[^#]*#", "").str.to_lowercase())
    )
    return links, mentions, hashtags


# --------------------------------------------------------------------------------------
# Repeated / longest messages, length distribution
# --------------------------------------------------------------------------------------


def repeated_messages(msgs: Frame, n: int = 20, min_count: int = 2) -> pl.DataFrame:
    """Most repeated exact messages (trimmed, case-insensitive; only messages of
    ≤ :data:`REPEATED_MAX_CHARS` characters are considered — long ones never repeat).

    Returns ``key, n, by_sender`` (list of struct ``sender_id, c``), sorted by ``n``.
    """
    df = (
        text_messages(msgs)
        .filter(pl.col("char_count") <= REPEATED_MAX_CHARS)
        .select("sender_id", pl.col("text").str.strip_chars().str.to_lowercase().alias("key"))
        .filter(pl.col("key") != "")
        .group_by("key", "sender_id")
        .agg(pl.len().alias("c"))
        .group_by("key")
        .agg(
            pl.col("c").sum().alias("n"),
            pl.struct("sender_id", "c").sort_by("c", descending=True).alias("by_sender"),
        )
        .filter(pl.col("n") >= min_count)
        .sort(["n", "key"], descending=[True, False])
        .head(n)
        .collect()
    )
    return df


def longest_messages(msgs: Frame, n: int = 10) -> pl.DataFrame:
    """Top ``n`` text messages per sender by word count (then characters).

    Columns: ``message_id, thread_id, sender_id, ts, type, text, word_count, char_count``.
    """
    return (
        text_messages(msgs)
        .select(
            "message_id", "thread_id", "sender_id", "ts", "type", "text", "word_count", "char_count"
        )
        .sort(["word_count", "char_count", "message_id"], descending=[True, True, False])
        .group_by("sender_id", maintain_order=True)
        .head(n)
        .collect()
    )


def length_bin_expr(col: str = "word_count") -> pl.Expr:
    """Label of the :data:`LENGTH_BINS` bin for a word count."""
    expr: pl.Expr = pl.lit(LENGTH_BINS[-1][0])
    for label, lo, hi in reversed(LENGTH_BINS[:-1]):
        assert hi is not None
        expr = pl.when(pl.col(col).is_between(lo, hi)).then(pl.lit(label)).otherwise(expr)
    return expr


def length_distribution(msgs: Frame) -> dict[int, list[int]]:
    """Per sender: counts per :data:`LENGTH_BINS` bin (text messages with ≥ 1 word)."""
    labels = [b[0] for b in LENGTH_BINS]
    df = (
        text_messages(msgs)
        .filter(pl.col("word_count") > 0)
        .select("sender_id", length_bin_expr().alias("bin"))
        .group_by("sender_id", "bin")
        .len()
        .collect()
    )
    out: dict[int, list[int]] = {}
    for pid, b, c in df.iter_rows():
        row = out.setdefault(int(pid), [0] * len(labels))
        row[labels.index(b)] = int(c)
    return out


# --------------------------------------------------------------------------------------
# Swear words
# --------------------------------------------------------------------------------------


def swear_matcher(words: Iterable[str]) -> pl.Expr:
    """Boolean expression over a tokens frame (``token``, ``key``) matching swear
    words: exact entries match accent-insensitively; ``prefix*`` entries match tokens
    starting with the prefix (accent-insensitively only if the prefix has no
    diacritics)."""
    from instagraph.textproc import strip_accents

    exact: set[str] = set()
    pre_plain: list[str] = []
    pre_acc: list[str] = []
    for raw in words:
        w = raw.strip().lower()
        if not w:
            continue
        if w.endswith("*"):
            p = w[:-1]
            if not p:
                continue
            (pre_plain if strip_accents(p) == p else pre_acc).append(p)
        else:
            exact.add(w)
            exact.add(strip_accents(w))
    folded = fold_accents_expr(pl.col("token"))
    cond = pl.col("token").is_in(list(exact)) | folded.is_in(list(exact))
    if pre_plain:
        rx = "^(?:" + "|".join(pl.escape_regex(p) for p in pre_plain) + ")"
        cond |= folded.str.contains(rx)
    if pre_acc:
        rx = "^(?:" + "|".join(pl.escape_regex(p) for p in pre_acc) + ")"
        cond |= pl.col("token").str.contains(rx)
    return cond


def swear_counts(
    tokens: pl.DataFrame, words: Iterable[str]
) -> tuple[dict[int, int], list[tuple[str, int]]]:
    """``({sender: swear tokens}, [(word, count)] top 20 overall)``."""
    uniq = tokens.select(pl.col("token").unique())
    bad = uniq.filter(swear_matcher(words))["token"].to_list()
    hits = tokens.filter(pl.col("token").is_in(bad))
    per = {int(g): int(c) for g, c in hits.group_by("sender_id").len().iter_rows()}
    top = [
        (str(k), int(c))
        for k, c in hits.group_by("token")
        .len()
        .sort(["len", "token"], descending=[True, False])
        .head(20)
        .iter_rows()
    ]
    return per, top


# --------------------------------------------------------------------------------------
# Term tracker
# --------------------------------------------------------------------------------------


def term_regex(
    term: str, *, mode: str = "word", case_sensitive: bool = False, accent_insensitive: bool = False
) -> str:
    """Rust regex for one tracked term (raises ``ValueError`` if invalid).

    ``plain`` = substring, ``word`` = whole word(s) (no word char on either side),
    ``regex`` = as given. Accent-insensitive mode folds the term (match against folded
    text, see :func:`term_text_expr`).
    """
    from instagraph.textproc import strip_accents

    t = strip_accents(term) if accent_insensitive and mode != "regex" else term
    if mode == "regex":
        body = t
    elif mode == "plain":
        body = pl.escape_regex(t)
    elif mode == "word":
        body = r"\b{start-half}" + pl.escape_regex(t) + r"\b{end-half}"
    else:
        raise ValueError(f"unknown mode {mode!r}")
    rx = body if case_sensitive else f"(?i){body}"
    if not _pattern_ok(rx):
        raise ValueError(f"invalid regex: {term!r}")
    return rx


def term_text_expr(accent_insensitive: bool) -> pl.Expr:
    """The text a term regex is matched against."""
    t = pl.col("text").fill_null("")
    return fold_accents_expr(t) if accent_insensitive else t


def term_matches(
    msgs: Frame,
    terms: list[str],
    *,
    mode: str = "word",
    case_sensitive: bool = False,
    accent_insensitive: bool = False,
) -> pl.DataFrame:
    """Text messages with ≥ 1 match of any term, one column ``n_<i>`` (matches of
    ``terms[i]``) per term plus ``message_id, thread_id, sender_id, ts, type, text,
    local_date``. Raises ``ValueError`` for an invalid regex."""
    regexes = [
        term_regex(
            t, mode=mode, case_sensitive=case_sensitive, accent_insensitive=accent_insensitive
        )
        for t in terms
    ]
    if not regexes:
        return pl.DataFrame()
    txt = term_text_expr(accent_insensitive)
    cols = [txt.str.count_matches(rx).alias(f"n_{i}") for i, rx in enumerate(regexes)]
    any_hit = pl.any_horizontal([pl.col(f"n_{i}") > 0 for i in range(len(regexes))])
    return (
        text_messages(msgs)
        .select("message_id", "thread_id", "sender_id", "ts", "type", "text", "local_date", *cols)
        .filter(any_hit)
        .sort("message_id")
        .collect()
    )


def bucketed_counts(
    df: pl.DataFrame,
    value: pl.Expr,
    bucket: str,
    start: date,
    end: date,
    group_col: str = "sender_id",
) -> tuple[list[date], dict[int, list[float]]]:
    """Sum ``value`` per (bucket of ``local_date``, group), zero-filled over
    ``start..end``. Returns ``(bucket dates, {group: values})``."""
    from instagraph.api.filters import bucket_range

    xs = bucket_range(start, end, bucket)
    index = {d: i for i, d in enumerate(xs)}
    out: dict[int, list[float]] = {}
    if df.is_empty():
        return xs, out
    agg = (
        df.select(
            bucket_start(pl.col("local_date"), bucket).alias("b"), group_col, value.alias("v")
        )
        .group_by("b", group_col)
        .agg(pl.col("v").sum())
    )
    for b, g, v in agg.iter_rows():
        if b in index:
            out.setdefault(int(g), [0.0] * len(xs))[index[b]] = float(v)
    return xs, out


def auto_bucket(start: date, end: date) -> str:
    """Bucket that keeps a time series readable: ≤ 62 days → day, ≤ 18 months →
    week, else month."""
    days = (end - start).days
    if days <= 62:
        return "day"
    if days <= 548:
        return "week"
    return "month"


# --------------------------------------------------------------------------------------
# Full Words-tab analysis
# --------------------------------------------------------------------------------------


@dataclass
class WordsAnalysis:
    """Everything the Words tab needs, keyed by ``sender_id`` (``None`` = overall)."""

    text_messages: dict[int, int]
    words: dict[int, int]
    avg_words: dict[int, float]
    vocabulary: dict[int, int]
    mattr: dict[int, float]
    rates: dict[int, dict[str, float]]
    top_words: TermList
    bigrams: TermList
    trigrams: TermList
    distinctive_method: str
    distinctive: dict[int, list[tuple[str, int, float]]]
    length_bins: dict[int, list[int]]
    laughter: LaughterResult
    links: dict[int, int]
    mentions: dict[int, int]
    hashtags: dict[int, int]
    top_mentions: list[tuple[str, int]]
    top_hashtags: list[tuple[str, int]]
    repeated: pl.DataFrame
    longest: pl.DataFrame
    swear: dict[int, int] | None
    top_swear: list[tuple[str, int]] | None


def _per_sender(df: pl.DataFrame) -> dict[int, int]:
    return {int(g): int(c) for g, c in df.group_by("sender_id").len().iter_rows()}


def _top_values(df: pl.DataFrame, n: int = 20) -> list[tuple[str, int]]:
    top = df.group_by("value").len().sort(["len", "value"], descending=[True, False]).head(n)
    return [(str(v), int(c)) for v, c in top.iter_rows()]


def analyze_words(
    msgs: Frame,
    *,
    stopwords: frozenset[str],
    laughter_patterns: Iterable[str],
    swear_words: list[str] | None = None,
    accent_insensitive: bool = False,
    top_n: int = 50,
    ngram_n: int = 20,
    distinctive_n: int = 25,
    context: Frame | None = None,
) -> WordsAnalysis:
    """Compute the whole Words tab in one pass over ``msgs``.

    ``context`` (default ``msgs``) = counted messages used for "who makes whom laugh".
    ``swear_words=None`` disables swear counting.
    """
    lf = _lazy(msgs)
    text_df = text_messages(lf).collect()
    tok = annotate_tokens(
        tokens_frame(text_df, accent_insensitive=accent_insensitive).collect(), stopwords
    )

    def stats() -> pl.DataFrame:
        return text_df.group_by("sender_id").agg(
            pl.len().alias("n"),
            pl.col("word_count").sum().alias("w"),
            pl.col("word_count").mean().alias("avg"),
        )

    content = tok.filter(~pl.col("stop") & (pl.col("token").str.len_chars() >= 2))

    def words_part() -> tuple[TermList, str, dict[int, list[tuple[str, int, float]]]]:
        disp: dict[str, str] = (
            dict(display_map(content if not content.is_empty() else tok).iter_rows())
            if accent_insensitive
            else {}
        )

        def relabel(d: TermList) -> TermList:
            return {g: [(disp.get(k, k), c) for k, c in lst] for g, lst in d.items()}

        per = count_terms(content)
        top: TermList = relabel(top_terms_by_group(per, top_n))
        top.update(relabel(top_terms_by_group(count_terms(content, None), top_n, None)))
        method, dist = distinctive_terms(per, distinctive_n)
        return top, method, {g: [(disp.get(k, k), c, s) for k, c, s in v] for g, v in dist.items()}

    all_disp = dict(display_map(tok).iter_rows()) if accent_insensitive else None

    def grams(k: int) -> TermList:
        return top_ngrams(tok, k, stopwords, ngram_n, display=all_disp)

    def swear_part() -> tuple[dict[int, int] | None, list[tuple[str, int]] | None]:
        if swear_words is None:
            return None, None
        return swear_counts(tok, swear_words)

    ctx_frame = context if context is not None else lf
    with ThreadPoolExecutor(max_workers=6) as pool:
        f_stats = pool.submit(stats)
        f_mattr = pool.submit(mattr_and_vocabulary, tok)
        f_words = pool.submit(words_part)
        f_bi = pool.submit(grams, 2)
        f_tri = pool.submit(grams, 3)
        f_ent = pool.submit(entity_frames, text_df)
        f_swear = pool.submit(swear_part)
        f_rates = pool.submit(message_rates, text_df)
        f_len = pool.submit(length_distribution, text_df)
        f_laugh = pool.submit(laughter_analysis, ctx_frame, list(laughter_patterns))
        f_rep = pool.submit(repeated_messages, text_df)
        f_long = pool.submit(longest_messages, text_df)
    st = f_stats.result()
    n_msgs = {int(g): int(n) for g, n, _, _ in st.iter_rows()}
    words = {int(g): int(w) for g, _, w, _ in st.iter_rows()}
    avg = {int(g): float(a) for g, _, _, a in st.iter_rows()}
    mattr, vocab = f_mattr.result()
    top, method, dist = f_words.result()
    links, mentions, hashtags = f_ent.result()
    swear, top_swear = f_swear.result()

    return WordsAnalysis(
        text_messages=n_msgs,
        words=words,
        avg_words=avg,
        vocabulary=vocab,
        mattr=mattr,
        rates=f_rates.result(),
        top_words=top,
        bigrams=f_bi.result(),
        trigrams=f_tri.result(),
        distinctive_method=method,
        distinctive=dist,
        length_bins=f_len.result(),
        laughter=f_laugh.result(),
        links=_per_sender(links),
        mentions=_per_sender(mentions),
        hashtags=_per_sender(hashtags),
        top_mentions=_top_values(mentions),
        top_hashtags=_top_values(hashtags),
        repeated=f_rep.result(),
        longest=f_long.result(),
        swear=swear,
        top_swear=top_swear,
    )
