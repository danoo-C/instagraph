import polars as pl
import pytest

from instagraph import textproc as tp


@pytest.mark.parametrize(
    ("raw", "fixed"),
    [
        ("Ã", "Ã"),  # lone lead byte: not valid UTF-8, kept
        ("JaroslavÂ\xa0", "Jaroslav\xa0"),
        ("Ä\x8dÅ¡Å¥Å¾Ã½Ã¡Ã\xadÃ©ÃºÃ¤Ã´Å\x88", "čšťžýáíéúäôň"),
        ("ð\x9f\x98\x82", "😂"),
        ("ahoj ð\x9f\x98\x82", "ahoj 😂"),
    ],
)
def test_fix_mojibake(raw: str, fixed: str) -> None:
    assert tp.fix_mojibake(raw) == fixed


@pytest.mark.parametrize(
    "s", ["čšťžýáíéúäôň", "😂", "👨‍👩‍👧", "🇸🇰", "👍🏽", "plain ascii", "", "Ďakujem veľmi"]
)
def test_fix_mojibake_keeps_correct_strings(s: str) -> None:
    assert tp.fix_mojibake(s) == s
    # round trip: mojibake of a correct string is fixed back
    assert tp.fix_mojibake(s.encode("utf-8").decode("latin-1")) == s


def test_fix_mojibake_counted() -> None:
    assert tp.fix_mojibake_counted("Ã¡") == ("á", True)
    assert tp.fix_mojibake_counted("á") == ("á", False)


def test_urls() -> None:
    text = "pozri https://example.com/a?b=1, a www.sme.sk. (http://x.y/z)"
    assert tp.extract_urls(text) == ["https://example.com/a?b=1", "www.sme.sk", "http://x.y/z"]
    assert "example" not in tp.strip_urls(text)
    assert tp.url_domain("https://www.Instagram.com/reel/1") == "instagram.com"
    assert tp.url_domain("www.sme.sk/x") == "sme.sk"


def test_tokenize_words_slovak_and_apostrophes() -> None:
    assert tp.tokenize_words("Ďakujem, veľmi pekne! Don’t can't 123") == [
        "Ďakujem",
        "veľmi",
        "pekne",
        "Don’t",
        "can't",
        "123",
    ]
    assert tp.tokenize_words("see https://a.com/word now") == ["see", "now"]
    assert tp.tokenize_words("Ahoj @jana 😂", lowercase=True, remove_mentions=True) == ["ahoj"]
    assert tp.word_count(None) == 0
    assert tp.word_count("😂😂") == 0


def test_grapheme_count() -> None:
    assert tp.grapheme_count("  ahoj  ") == 4
    assert tp.grapheme_count("👨‍👩‍👧🇸🇰👍🏽") == 3
    assert tp.grapheme_count("čo") == 2
    assert tp.grapheme_count("čo") == 2  # combining caron
    assert tp.grapheme_count(None) == 0


def test_extract_emojis() -> None:
    text = "ahoj 😂 👨‍👩‍👧 🇸🇰 👍🏽 ❤️ 1️⃣"
    assert tp.extract_emojis(text) == ["😂", "👨‍👩‍👧", "🇸🇰", "👍🏽", "❤", "1⃣"]
    assert tp.extract_emojis(text, merge_skin_tones=True)[3] == "👍"
    assert tp.extract_emojis("🤷‍♀️", merge_gendered=True) == ["🤷"]
    assert tp.extract_emojis("🤷‍♀️") == ["🤷‍♀"]
    assert tp.extract_emojis(None) == []


def test_normalize_emoji() -> None:
    assert tp.normalize_emoji("❤️") == tp.normalize_emoji("❤") == "❤"
    assert tp.normalize_emoji("👍🏿") == "👍"
    assert tp.normalize_emoji("👍🏿", merge_skin_tones=False) == "👍🏿"


def test_normalize_emoji_expr_matches_python() -> None:
    values = ["❤️", "👍🏽", "🤷🏻‍♂️", "😂", "👨‍👩‍👧"]
    for skin in (True, False):
        for gender in (True, False):
            got = (
                pl.Series(values)
                .to_frame("e")
                .select(
                    tp.normalize_emoji_expr(
                        pl.col("e"), merge_skin_tones=skin, merge_gendered=gender
                    )
                )["e"]
                .to_list()
            )
            want = [
                tp.normalize_emoji(v, merge_skin_tones=skin, merge_gendered=gender) for v in values
            ]
            assert got == want


def test_emoji_only() -> None:
    assert tp.is_emoji_only("😂😂 ")
    assert not tp.is_emoji_only("ha 😂")
    assert not tp.is_emoji_only("")


def test_strip_accents() -> None:
    assert tp.strip_accents("áhoj Ďakujem veľmi čšťž") == "ahoj Dakujem velmi cstz"


def test_mentions_hashtags() -> None:
    text = "@jana.novak ideme? mail me@x.sk #vikend #Leto2024 https://x.com/#frag"
    assert tp.extract_mentions(text) == ["jana.novak"]
    assert tp.extract_hashtags(text) == ["vikend", "Leto2024"]


def test_emoticons() -> None:
    assert tp.extract_emoticons("ahoj :) super :D <3 xD :P") == [":)", ":D", "<3", "xD", ":P"]
    assert tp.extract_emoticons("dobre :))") == [":)"]
    assert tp.extract_emoticons("https://a.com/:p xdd") == []
    assert tp.extract_emoticons(None) == []
