"""Text analysis (§6.5) with hand-calculated expectations."""

import math
from typing import Any

import polars as pl
import pytest

from instagraph.analysis import text as T
from instagraph.api.filters import add_local_time
from instagraph.resources import (
    builtin_stopwords,
    default_laughter_patterns,
    emoticons,
    stopwords_for,
    swear_words_for,
)
from instagraph.store.app_settings import DEFAULT_LAUGHTER_PATTERNS, AppSettings
from instagraph.testing import build_dataset
from instagraph.textproc import EMOTICONS

PEOPLE: list[dict[str, Any]] = [
    {"display_name": "Me", "is_owner": True},
    {"display_name": "Jana"},
    {"display_name": "Eva"},
]


def frame(messages: list[dict[str, Any]], threads: int = 1) -> pl.DataFrame:
    """Messages (thread 0 unless given) → filtered-like frame with local columns."""
    ths = [{"title": f"t{i}", "participants": [0, 1, 2]} for i in range(threads)]
    rows = []
    for i, m in enumerate(messages):
        row = {"thread_id": 0, "ts": f"2024-01-01T10:{i:02d}:00Z", **m}
        rows.append(row)
    ds = build_dataset(PEOPLE, ths, rows)
    return add_local_time(ds.messages, "UTC").collect()


SW = stopwords_for(AppSettings())


# --------------------------------------------------------------------------------------
# Tokenization
# --------------------------------------------------------------------------------------


def test_tokens_keep_diacritics_and_drop_urls_mentions_emojis() -> None:
    df = frame(
        [
            {
                "sender_id": 1,
                "text": "Áno, ĎAKUJEM @peter.x! pozri https://x.sk/a 1️⃣ ❤️ it’s",
            }
        ]
    )
    tok = T.tokens_frame(df).collect()
    assert tok["token"].to_list() == ["áno", "ďakujem", "pozri", "it's"]
    assert tok["key"].to_list() == tok["token"].to_list()


def test_accent_insensitive_keys() -> None:
    df = frame([{"sender_id": 1, "text": "Čau ľudia, ahoj áhoj"}])
    tok = T.tokens_frame(df, accent_insensitive=True).collect()
    assert tok["key"].to_list() == ["cau", "ludia", "ahoj", "ahoj"]
    assert tok["token"].to_list() == ["čau", "ľudia", "ahoj", "áhoj"]


def test_non_text_messages_ignored() -> None:
    df = frame(
        [
            {"sender_id": 1, "text": "pivo pivo"},
            {"sender_id": 1, "type": "like", "text": "Liked a message"},
            {"sender_id": 1, "is_unsent": True, "text": "pivo"},
        ]
    )
    assert T.tokens_frame(df).collect().height == 2


# --------------------------------------------------------------------------------------
# Stop-words / top words / n-grams
# --------------------------------------------------------------------------------------


def test_stopwords_include_forms_without_diacritics() -> None:
    sk = builtin_stopwords("sk")
    assert {"máš", "mas", "že", "ze", "ako"} <= sk
    assert {"já", "ja", "protože", "protoze"} <= builtin_stopwords("cs")
    assert {"the", "i'm", "don't"} <= builtin_stopwords("en")


def test_stopwords_for_merges_settings() -> None:
    s = AppSettings(
        stopwords_added={"sk": ["pivo"]},
        stopwords_removed={"sk": ["ako"], "cs": ["ako"]},
        ignored_words=["Hej"],
    )
    sw = stopwords_for(s)
    assert "pivo" in sw and "hej" in sw
    assert "ako" not in sw  # removed from both lists that contain it
    assert "máš" in sw


def test_top_words_removes_stopwords_numbers_and_merges_accents() -> None:
    df = frame(
        [
            {"sender_id": 1, "text": "ahoj ako sa máš 2024"},
            {"sender_id": 0, "text": "áhoj ahoj pivo"},
            {"sender_id": 0, "text": "Pivo mas"},
        ]
    )
    assert T.top_words(df, SW, 10) == [("ahoj", 2), ("pivo", 2), ("áhoj", 1)]
    # accent-insensitive: ahoj+áhoj merged, shown with the most frequent spelling
    assert T.top_words(df, SW, 10, accent_insensitive=True) == [("ahoj", 3), ("pivo", 2)]


def test_ngrams_stay_within_messages_and_drop_all_stopword_grams() -> None:
    df = frame(
        [
            {"sender_id": 1, "text": "dobré ráno"},
            {"sender_id": 1, "text": "dobré ráno a ty"},
            {"sender_id": 0, "text": "ráno dobré"},
            {"sender_id": 0, "text": "a ty"},
            {"sender_id": 0, "text": "a ty"},
        ]
    )
    tok = T.tokens_frame(df).collect()
    bi = T.top_ngrams(tok, 2, SW, min_count=2)
    assert bi[None] == [("dobré ráno", 2)]  # "a ty" is stop-words only; no cross-message grams
    assert bi[1] == [("dobré ráno", 2)]
    assert 0 not in bi
    tri = T.top_ngrams(tok, 3, SW, min_count=1)
    assert ("dobré ráno a", 1) in tri[None] and ("ráno a ty", 1) in tri[None]


# --------------------------------------------------------------------------------------
# Distinctive words
# --------------------------------------------------------------------------------------


def test_log_odds_matches_formula_and_is_antisymmetric() -> None:
    counts = pl.DataFrame(
        {"sender_id": [0, 0, 1, 1], "key": ["a", "b", "a", "b"], "n": [8, 2, 2, 8]},
        schema={"sender_id": pl.UInt32, "key": pl.String, "n": pl.Int64},
    )
    res = T.log_odds_dirichlet(counts).sort("sender_id", "key")
    # hand calc for (0, "a"): y_i=8, y_j=2, a_w=10, a0=20, n_i=n_j=10
    d = math.log((8 + 10) / (10 + 20 - 8 - 10)) - math.log((2 + 10) / (10 + 20 - 2 - 10))
    z = d / math.sqrt(1 / 18 + 1 / 12)
    scores = dict(zip(zip(res["sender_id"], res["key"], strict=True), res["score"], strict=True))
    assert scores[(0, "a")] == pytest.approx(z)
    assert scores[(1, "a")] == pytest.approx(-z)
    assert scores[(0, "b")] == pytest.approx(-z)


def test_distinctive_terms_sanity() -> None:
    rows = [{"sender_id": 0, "text": "pivo pivo pivo kino"}] * 3 + [
        {"sender_id": 1, "text": "čaj čaj čaj kino"}
    ] * 3
    method, top = T.distinctive_terms(T.count_terms(T.tokens_frame(frame(rows)).collect()))
    assert method == "log_odds"
    assert top[0][0][0] == "pivo" and top[1][0][0] == "čaj"
    assert all(t != "kino" for t, _, _ in top[0])  # shared word is not distinctive


def test_distinctive_falls_back_to_tfidf_for_single_person() -> None:
    rows = [{"sender_id": 0, "text": "pivo pivo kino"}]
    method, top = T.distinctive_terms(T.count_terms(T.tokens_frame(frame(rows)).collect()))
    assert method == "tfidf"
    assert top[0][0][:2] == ("pivo", 2)


# --------------------------------------------------------------------------------------
# Vocabulary / MATTR / rates
# --------------------------------------------------------------------------------------


def test_mattr_hand_calculated() -> None:
    df = frame([{"sender_id": 0, "text": "a b a c"}, {"sender_id": 1, "text": "x x y"}])
    tok = T.tokens_frame(df).collect()
    # window 3: [a b a] = 2/3, [b a c] = 3/3 → 5/6; person 1 has N=3 ≥ W: [x x y] = 2/3
    mattr, vocab = T.mattr_and_vocabulary(tok, window=3)
    assert mattr[0] == pytest.approx(5 / 6)
    assert mattr[1] == pytest.approx(2 / 3)
    assert vocab == {0: 3, 1: 2}
    # fewer tokens than the window → plain TTR
    assert T.mattr_from_tokens(tok, window=500)[0] == pytest.approx(3 / 4)


def test_mattr_matches_naive_implementation() -> None:
    words = ["a", "b", "c", "a", "b", "d", "e", "a", "f", "g", "a", "b", "c", "h"]
    df = frame([{"sender_id": 0, "text": " ".join(words)}])
    w = 5
    naive = sum(len(set(words[i : i + w])) / w for i in range(len(words) - w + 1)) / (
        len(words) - w + 1
    )
    assert T.lexical_diversity(df, window=w)[0] == pytest.approx(naive)


def test_rates() -> None:
    df = frame(
        [
            {"sender_id": 0, "text": "Ideš?"},
            {"sender_id": 0, "text": "ÁNO IDEM!"},
            {"sender_id": 0, "text": "hmm..."},
            {"sender_id": 0, "text": "no…  OK"},  # "OK" has only 2 letters: not caps
        ]
    )
    r = T.message_rates(df)[0]
    assert r == {"question": 0.25, "exclamation": 0.25, "caps": 0.25, "ellipsis": 0.5}
    assert T.question_rate(df) == {0: 0.25}
    assert T.avg_words_per_message(df) == {0: pytest.approx(6 / 4)}


# --------------------------------------------------------------------------------------
# Laughter
# --------------------------------------------------------------------------------------


def _ts(minute: int, second: int = 0) -> str:
    return f"2024-01-01T10:{minute:02d}:{second:02d}Z"


def test_laughter_counts_and_who_makes_whom_laugh() -> None:
    df = frame(
        [
            {"sender_id": 0, "ts": _ts(0), "text": "vtip"},
            {"sender_id": 1, "ts": _ts(1), "text": "hahaha"},  # credited to 0
            {"sender_id": 1, "ts": _ts(1, 30), "text": "XD 😂"},  # same run, 1.5 min → 0
            {"sender_id": 1, "ts": _ts(5), "text": "lol"},  # 5 min after 0 → no credit
            {"sender_id": 0, "ts": _ts(6), "text": "Smiech? nie, to je ahah"},  # not ≥2 "ha"
            {"sender_id": 2, "ts": _ts(7), "text": "hehe"},  # credited to 0
            {"sender_id": 0, "ts": _ts(8), "text": "chachacha"},  # \b: not laughter
        ]
    )
    r = T.laughter_analysis(df, DEFAULT_LAUGHTER_PATTERNS)
    assert r.laughs == {0: 0, 1: 3, 2: 1}
    assert r.caused == {0: 3}
    assert r.pairs == {(1, 0): 2, (2, 0): 1}
    assert r.matches[1] == 4  # hahaha, XD, 😂, lol
    assert r.per_100(1) == pytest.approx(100.0)
    assert T.laughter_stats(df, DEFAULT_LAUGHTER_PATTERNS) == ({0: 0, 1: 3, 2: 1}, {0: 3})


def test_laughter_slovak_words_and_invalid_patterns() -> None:
    df = frame(
        [
            {"sender_id": 0, "text": "hahač"},  # Unicode word boundary: no match
            {"sender_id": 1, "text": "čau haha"},
        ]
    )
    r = T.laughter_analysis(df, [*DEFAULT_LAUGHTER_PATTERNS, "(?<=x)y"])
    assert r.laughs == {0: 0, 1: 1}
    assert r.invalid_patterns == ["(?<=x)y"]
    assert T.laughter_analysis(df, []).laughs == {0: 0, 1: 0}


def test_prefilter_regex() -> None:
    assert T.prefilter_regex(r"(?i)\bha\b") == "(?i)ha"
    assert T.prefilter_regex(r"\b{start-half}x\b{end-half}") == "x"
    assert T.prefilter_regex(r"a\\b") is None  # escaped backslash, not a boundary
    assert T.prefilter_regex("😂") is None


# --------------------------------------------------------------------------------------
# Entities, repeated, longest, length bins, swear words
# --------------------------------------------------------------------------------------


def test_entities() -> None:
    df = frame(
        [
            {"sender_id": 0, "text": "@Peter pozri https://x.com/@nie #Víkend mail a@b.sk"},
            {"sender_id": 1, "text": "@peter #víkend #vikend"},
        ]
    )
    links, mentions, hashtags = T.entity_frames(df)
    assert links.height == 1
    assert mentions["value"].to_list() == ["peter", "peter"]
    assert hashtags["value"].to_list() == ["víkend", "víkend", "vikend"]


def test_repeated_and_longest_messages_and_length_bins() -> None:
    df = frame(
        [
            {"sender_id": 0, "text": "ok"},
            {"sender_id": 1, "text": "OK "},
            {"sender_id": 0, "text": "ok"},
            {"sender_id": 0, "text": "jedna dva tri štyri päť šesť"},
            {"sender_id": 1, "text": "haha"},
        ]
    )
    rep = T.repeated_messages(df)
    assert rep["key"].to_list() == ["ok"]
    assert rep["n"].to_list() == [3]
    assert rep["by_sender"].to_list()[0] == [{"sender_id": 0, "c": 2}, {"sender_id": 1, "c": 1}]
    longest = T.longest_messages(df, n=2)
    assert longest.filter(pl.col("sender_id") == 0)["message_id"].to_list() == [3, 0]
    bins = T.length_distribution(df)
    labels = [b[0] for b in T.LENGTH_BINS]
    assert bins[0][labels.index("1")] == 2 and bins[0][labels.index("6-10")] == 1
    assert bins[1][labels.index("1")] == 2


def test_swear_counts_prefix_and_accents() -> None:
    df = frame(
        [
            {"sender_id": 0, "text": "Kurva, to je kurvasky dobré, do piče"},
            {"sender_id": 1, "text": "picture pica hovná hovna"},
        ]
    )
    tok = T.tokens_frame(df).collect()
    per, top = T.swear_counts(tok, ["kurv*", "pič*", "pica", "hovna"])
    # pič* has diacritics → only matches "piče", never "picture"
    assert per == {0: 3, 1: 3}
    assert dict(top) == {"kurva": 1, "kurvasky": 1, "piče": 1, "pica": 1, "hovná": 1, "hovna": 1}


def test_swear_words_for_defaults() -> None:
    assert "kurva*" in swear_words_for(AppSettings())
    assert swear_words_for(AppSettings(swear_words=["x"])) == ["x"]


# --------------------------------------------------------------------------------------
# Term tracker
# --------------------------------------------------------------------------------------


TERM_ROWS = [
    {"sender_id": 0, "text": "Ahoj, ahojte! AHOJ"},
    {"sender_id": 1, "text": "áhoj"},
    {"sender_id": 1, "text": "pivo.net a x.net"},
]


@pytest.mark.parametrize(
    ("term", "kwargs", "expected"),
    [
        ("ahoj", {"mode": "word"}, {0: 2}),
        ("ahoj", {"mode": "plain"}, {0: 3}),
        ("ahoj", {"mode": "word", "case_sensitive": True}, {}),
        ("Ahoj", {"mode": "word", "case_sensitive": True}, {0: 1}),
        ("ahoj", {"mode": "word", "accent_insensitive": True}, {0: 2, 1: 1}),
        ("áhoj", {"mode": "word", "accent_insensitive": True}, {0: 2, 1: 1}),
        (r"aho[jy]\w*", {"mode": "regex"}, {0: 3}),
        (".net", {"mode": "word"}, {}),  # preceded by a word char both times
        (".net", {"mode": "plain"}, {1: 2}),
    ],
)
def test_term_matches(term: str, kwargs: dict[str, Any], expected: dict[int, int]) -> None:
    hits = T.term_matches(frame(TERM_ROWS), [term], **kwargs)
    got: dict[int, int] = {}
    for sid, n in hits.select("sender_id", "n_0").iter_rows():
        got[sid] = got.get(sid, 0) + n
    assert got == expected


def test_term_regex_invalid() -> None:
    with pytest.raises(ValueError):
        T.term_regex("(", mode="regex")
    with pytest.raises(ValueError):
        T.term_regex("x", mode="fuzzy")


# --------------------------------------------------------------------------------------
# Full analysis + resources
# --------------------------------------------------------------------------------------


def test_analyze_words_smoke() -> None:
    df = frame(
        [
            {"sender_id": 0, "text": "pivo dnes večer?"},
            {"sender_id": 1, "text": "haha jasné, pivo"},
            {"sender_id": 1, "text": "kurva dobre"},
        ]
    )
    a = T.analyze_words(df, stopwords=SW, laughter_patterns=DEFAULT_LAUGHTER_PATTERNS)
    assert a.text_messages == {0: 1, 1: 2}
    assert a.words == {0: 3, 1: 5}
    assert a.top_words[None][0] == ("pivo", 2)
    assert a.laughter.caused == {0: 1}
    assert a.swear is None
    b = T.analyze_words(df, stopwords=SW, laughter_patterns=[], swear_words=["kurva"])
    assert b.swear == {1: 1}


def test_resource_files_mirror_code_defaults() -> None:
    assert list(default_laughter_patterns()) == DEFAULT_LAUGHTER_PATTERNS
    assert set(emoticons()) == set(EMOTICONS)
