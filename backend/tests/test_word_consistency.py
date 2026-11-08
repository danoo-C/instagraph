"""Every place that counts / tokenizes words must agree (§6.1): ``textproc``, the
importer (``derive.text_metrics``), ``testing.build_dataset``, ``analysis.text`` (Polars),
``analysis.person_metrics`` and wrapped (which reuses ``person_metrics.tokens_expr``)."""

import unicodedata

import polars as pl
import pytest

from instagraph import textproc as tp
from instagraph.analysis.person_metrics import tokens_expr
from instagraph.analysis.text import tokens_frame
from instagraph.importer.derive import text_metrics
from instagraph.testing import build_dataset

TEXTS = [
    "super ❤️",
    "super ❤️👍🏽",
    "❤️",
    "👨‍👩‍👧 rodina",
    "🇸🇰 Slovensko",
    "1️⃣ prvý, 2️⃣ druhý #️⃣",
    "ok👍🏽ok",
    "Ďakujem, veľmi pekne!",
    unicodedata.normalize("NFD", "Ďakujem, veľmi pekne! Čaute žiaci"),  # combining marks
    "Don’t can't it's 123",
    "pozri https://example.com/word teraz",
    "Ahoj @jana.k, kde si?",
    "hello️world",
    "#vikend a @peter",
    "plain ascii text only",
    "हिन्दी नमस्ते",
]

EXPECTED_COUNTS = {"super ❤️": 1, "super ❤️👍🏽": 1, "❤️": 0, "1️⃣ prvý, 2️⃣ druhý #️⃣": 2}


def test_expected_counts() -> None:
    for text, n in EXPECTED_COUNTS.items():
        assert tp.word_count(text) == n, text
    nfd = unicodedata.normalize("NFD", "Čaute žiaci")
    assert tp.tokenize_words(nfd) == nfd.split()


def test_importer_and_testing_word_counts_agree() -> None:
    words, _, _ = text_metrics(TEXTS)
    assert words == [tp.word_count(t) for t in TEXTS]
    ds = build_dataset(
        people=[{"display_name": "Me", "is_owner": True}],
        threads=[{"title": "t", "folder": "t_1", "participants": [0]}],
        messages=[
            {"thread_id": 0, "sender_id": 0, "ts": f"2024-01-01T00:{i:02d}:00Z", "text": t}
            for i, t in enumerate(TEXTS)
        ],
    )
    stored = ds.messages.sort("message_id").collect()["word_count"].to_list()
    assert stored == words


def test_polars_tokenizers_agree_with_textproc() -> None:
    df = pl.DataFrame(
        {
            "message_id": list(range(len(TEXTS))),
            "sender_id": [0] * len(TEXTS),
            "type": ["text"] * len(TEXTS),
            "text": TEXTS,
            "char_count": [len(t) for t in TEXTS],
        }
    )
    # person_metrics / wrapped: lower-case, URLs and mentions removed.
    pm = df.select(tokens_expr(pl.col("text")).alias("t"))["t"].to_list()
    for text, toks in zip(TEXTS, pm, strict=True):
        expected = tp.tokenize_words(text, lowercase=True, remove_mentions=True)
        assert toks == expected, text
    # analysis.text (word lists): lower-case, URLs, mentions and emojis removed.
    tf = tokens_frame(df).collect()
    for i, text in enumerate(TEXTS):
        toks = tf.filter(pl.col("message_id") == i)["token"].to_list()
        expected = tp.tokenize_words(
            text.replace("’", "'"), lowercase=True, remove_mentions=True, remove_emojis=True
        )
        assert toks == expected, text


@pytest.mark.parametrize("text", TEXTS)
def test_word_count_equals_polars_word_pattern(text: str) -> None:
    s = pl.Series([text]).str.replace_all(r"(?i)\b(?:https?://|www\.)[^\s<>\"']+", " ")
    s = s.str.replace_all(tp.KEYCAP_PATTERN, " ")
    assert s.str.extract_all(tp.WORD_PATTERN).list.len().item() == tp.word_count(text)
