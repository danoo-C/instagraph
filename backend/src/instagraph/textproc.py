"""Shared text utilities for the importer and analysis (spec §3.4, §6.1, §6.5, §6.6).

Everything here is pure, deterministic and safe on any ``str`` (including empty).

* Words (§6.1): :data:`WORD_RE` (:data:`WORD_PATTERN`) — a run of letters / digits /
  combining marks that STARTS with a letter or digit, optionally ``'``-joined, applied
  after URLs and emoji keycaps are removed. Emoji components (variation selectors such as
  U+FE0F, the keycap U+20E3, ZWJ, skin-tone modifiers, tags) never form or extend a word,
  while combining diacritics after letters do. Use :func:`tokenize_words` /
  :func:`word_count`. The pattern is valid in both ``regex`` and Polars (Rust) syntax.
* Characters (§6.1): grapheme clusters of the stripped text, :func:`grapheme_count`.
* Emojis (§6.6): :func:`extract_emojis` (``emoji.emoji_list``: ZWJ sequences, flags and
  keycaps count as ONE emoji). :func:`normalize_emoji` strips U+FE0F and optionally skin
  tones / gender signs; :func:`normalize_emoji_expr` does the same in Polars.
* Accent-insensitive matching (§6.5): :func:`strip_accents`.
"""

import re
import unicodedata
from urllib.parse import urlsplit

import emoji as emoji_lib
import polars as pl
import regex

# --------------------------------------------------------------------------------------
# Encoding
# --------------------------------------------------------------------------------------


def fix_mojibake(s: str) -> str:
    """Undo Instagram's UTF-8-as-Latin-1 escaping (§3.4).

    ``"Ã¡"`` → ``"á"``, ``"ð\\x9f\\x98\\x82"`` → ``"😂"``. Strings that are already correct
    (contain chars > U+00FF, or are not valid UTF-8 bytes) are returned unchanged.
    """
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def fix_mojibake_counted(s: str) -> tuple[str, bool]:
    """Like :func:`fix_mojibake` but also report whether the string changed."""
    fixed = fix_mojibake(s)
    return fixed, fixed != s


# --------------------------------------------------------------------------------------
# URLs, mentions, hashtags
# --------------------------------------------------------------------------------------

URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"']+")
_URL_TRAILING = ".,!?;:)]}'\"…"
MENTION_RE = regex.compile(r"(?<![\w@])@([\w](?:[\w.]*[\w])?)")
HASHTAG_RE = regex.compile(r"(?<![\w#&])#([\p{L}\p{M}\p{N}_]+)")


def extract_urls(text: str) -> list[str]:
    """URLs (``http(s)://…`` or ``www.…``) in order, trailing punctuation removed."""
    return [m.group(0).rstrip(_URL_TRAILING) for m in URL_RE.finditer(text)]


def strip_urls(text: str, replacement: str = " ") -> str:
    """Remove URLs from ``text`` (each replaced by ``replacement``)."""
    return URL_RE.sub(replacement, text)


def url_domain(url: str) -> str | None:
    """Lower-case host without ``www.``; ``None`` if it can't be parsed."""
    candidate = url if "://" in url else f"http://{url}"
    try:
        host = urlsplit(candidate).hostname
    except ValueError:
        return None
    if not host:
        return None
    return host.removeprefix("www.")


def extract_mentions(text: str) -> list[str]:
    """``@username`` mentions without the ``@`` (emails are not mentions)."""
    return [m.group(1) for m in MENTION_RE.finditer(strip_urls(text))]


def extract_hashtags(text: str) -> list[str]:
    """``#hashtag`` tags without the ``#``."""
    return [m.group(1) for m in HASHTAG_RE.finditer(strip_urls(text))]


# --------------------------------------------------------------------------------------
# Words and characters
# --------------------------------------------------------------------------------------

#: Combining marks that may extend a word: every ``\p{M}`` except emoji components
#: (variation selectors incl. U+FE0F, the enclosing keycap U+20E3).
_WORD_MARK = r"[^\P{M}\uFE00-\uFE0F\u20E3\U000E0100-\U000E01EF]"
#: §6.1 word pattern — identical semantics in ``regex`` and Polars/Rust.
WORD_PATTERN = (
    rf"[\p{{L}}\p{{N}}](?:[\p{{L}}\p{{N}}]|{_WORD_MARK})*"
    rf"(?:['’]\p{{L}}(?:\p{{L}}|{_WORD_MARK})*)*"
)
#: Emoji keycaps (``1️⃣``, ``#️⃣``) — removed before word tokenization.
KEYCAP_PATTERN = r"[0-9#*]\uFE0F?\u20E3"
WORD_RE = regex.compile(WORD_PATTERN)
KEYCAP_RE = regex.compile(KEYCAP_PATTERN)
GRAPHEME_RE = regex.compile(r"\X")


def tokenize_words(
    text: str,
    *,
    lowercase: bool = False,
    remove_mentions: bool = False,
    remove_emojis: bool = False,
) -> list[str]:
    """Word tokens per §6.1 (URLs removed first).

    For word counts use the defaults. For text analysis (§6.5) pass
    ``lowercase=True, remove_mentions=True, remove_emojis=True``. Emoji keycaps (``1️⃣``)
    are always removed, so emojis never contribute words.
    """
    t = strip_urls(text)
    if "\u20e3" in t:
        t = KEYCAP_RE.sub(" ", t)
    if remove_mentions:
        t = MENTION_RE.sub(" ", t)
    if remove_emojis:
        # Stray variation selectors are word boundaries (``emoji`` would drop them).
        t = strip_emojis(t.replace("\ufe0f", " "))
    if lowercase:
        t = t.lower()
    return WORD_RE.findall(t)


def word_count(text: str | None) -> int:
    return len(tokenize_words(text)) if text else 0


def graphemes(text: str) -> list[str]:
    """Grapheme clusters of ``text`` (``regex`` ``\\X``)."""
    return GRAPHEME_RE.findall(text)


def grapheme_count(text: str | None) -> int:
    """Number of grapheme clusters, ignoring leading/trailing whitespace (§6.1)."""
    return len(GRAPHEME_RE.findall(text.strip())) if text else 0


def strip_accents(s: str) -> str:
    """Accent-insensitive form: NFKD + drop combining marks (``"áhoj"`` → ``"ahoj"``)."""
    decomposed = unicodedata.normalize("NFKD", s)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


# --------------------------------------------------------------------------------------
# Emojis
# --------------------------------------------------------------------------------------

SKIN_TONES = frozenset(chr(c) for c in range(0x1F3FB, 0x1F400))
VARIATION_SELECTOR = "️"
ZWJ = "‍"
_GENDER_SUFFIX_RE = re.compile("‍[♀♂]️?")
# Rust-regex (Polars) equivalents.
_PL_SKIN_TONES = r"[\x{1F3FB}-\x{1F3FF}]"
_PL_VS16 = r"\x{FE0F}"
_PL_GENDER = r"\x{200D}[\x{2640}\x{2642}]"


def normalize_emoji(
    e: str,
    *,
    merge_skin_tones: bool = True,
    merge_gendered: bool = False,
) -> str:
    """Canonical form of one emoji: U+FE0F always removed (``❤️`` == ``❤``).

    ``merge_skin_tones`` strips U+1F3FB–U+1F3FF; ``merge_gendered`` strips a trailing
    ZWJ + ♀/♂ (``🤷‍♀️`` → ``🤷``). Storage form (in Parquet) is
    ``normalize_emoji(e, merge_skin_tones=False)``.
    """
    if merge_gendered:
        e = _GENDER_SUFFIX_RE.sub("", e)
    if merge_skin_tones:
        e = "".join(c for c in e if c not in SKIN_TONES)
    return e.replace(VARIATION_SELECTOR, "")


def normalize_emoji_expr(
    expr: pl.Expr, *, merge_skin_tones: bool = True, merge_gendered: bool = False
) -> pl.Expr:
    """Polars version of :func:`normalize_emoji` for a String expression.

    For the ``emojis`` list column use ``pl.col("emojis").list.eval(
    normalize_emoji_expr(pl.element(), ...))``.
    """
    if merge_gendered:
        expr = expr.str.replace_all(_PL_GENDER + _PL_VS16 + "?", "")
    if merge_skin_tones:
        expr = expr.str.replace_all(_PL_SKIN_TONES, "")
    return expr.str.replace_all(_PL_VS16, "")


def extract_emojis(
    text: str | None,
    *,
    merge_skin_tones: bool = False,
    merge_gendered: bool = False,
) -> list[str]:
    """All emojis in ``text`` in order, normalized with :func:`normalize_emoji`.

    Defaults give the storage form (skin tones kept, U+FE0F removed).
    """
    if not text:
        return []
    return [
        normalize_emoji(
            item["emoji"], merge_skin_tones=merge_skin_tones, merge_gendered=merge_gendered
        )
        for item in emoji_lib.emoji_list(text)
    ]


def strip_emojis(text: str, replacement: str = " ") -> str:
    return emoji_lib.replace_emoji(text, replace=replacement)


def is_emoji_only(text: str | None) -> bool:
    """True if ``text`` has ≥1 emoji and nothing else except whitespace."""
    if not text or not text.strip():
        return False
    return bool(emoji_lib.emoji_list(text)) and not strip_emojis(text, "").strip()


# --------------------------------------------------------------------------------------
# Emoticons
# --------------------------------------------------------------------------------------

#: Text emoticons counted separately from emojis (§6.6). Case-sensitive on purpose:
#: ``xD``/``XD`` are emoticons, while lower-case ``xd`` is treated as laughter.
EMOTICONS: tuple[str, ...] = (
    ":)",
    ":-)",
    ":(",
    ":-(",
    ":D",
    ":-D",
    ";)",
    ";-)",
    ":P",
    ":-P",
    ":p",
    ":-p",
    ":O",
    ":o",
    ":*",
    ":/",
    ":-/",
    ":'(",
    "<3",
    "</3",
    "^^",
    "^_^",
    "-_-",
    "xD",
    "XD",
    ":3",
)
EMOTICON_RE = re.compile(
    r"(?<![\w:;<^/-])("
    + "|".join(re.escape(e) for e in sorted(EMOTICONS, key=len, reverse=True))
    + r")(?![\w/])"
)


def extract_emoticons(text: str | None) -> list[str]:
    """Text emoticons (``:)``, ``:D``, ``<3``, ``xD``…) in ``text`` (URLs ignored)."""
    if not text:
        return []
    return EMOTICON_RE.findall(strip_urls(text))
