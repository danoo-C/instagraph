"""Built-in data files for text / emoji analysis (spec §5.11, §6.5, §6.6).

All loaders are offline and cached. Files (in this package directory):

* ``stopwords_{en,sk,cs}.txt`` — one lower-case word per line, ``#`` comments.
* ``laughter.json`` — default laughter patterns (mirror of
  ``AppSettings.laughter_patterns`` defaults).
* ``emoticons.json`` — text emoticons (mirror of ``textproc.EMOTICONS``).
* ``swear_words.json`` — small EN/SK/CS list; ``*`` suffix = prefix match.
* ``emoji_categories.json`` — emoji (no U+FE0F, no skin tone) → category; generated
  from the installed ``emoji`` package + Unicode emoji group ranges.
* ``hearts.json`` — heart emoji symbols (❤ 🧡 💛 …) for "hearts over time".

Settings-aware helpers (:func:`stopwords_for`, :func:`laughter_patterns_for`,
:func:`swear_words_for`) merge the user's edits from :class:`AppSettings`.
"""

import json
import unicodedata
from functools import lru_cache
from importlib import resources
from typing import Any

from instagraph.store.app_settings import AppSettings

STOPWORD_LANGUAGES: tuple[str, ...] = ("en", "sk", "cs")
EMOJI_CATEGORIES: tuple[str, ...] = (
    "smileys",
    "hearts",
    "people",
    "animals",
    "food",
    "travel",
    "activities",
    "objects",
    "symbols",
    "flags",
)
_SKIN_TONES = frozenset(chr(c) for c in range(0x1F3FB, 0x1F400))


def _fold(s: str) -> str:
    decomposed = unicodedata.normalize("NFKD", s)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _read_text(name: str) -> str:
    return resources.files("instagraph.resources").joinpath(name).read_text(encoding="utf-8")


def _read_json(name: str) -> Any:
    return json.loads(_read_text(name))


# --------------------------------------------------------------------------------------
# Stop-words
# --------------------------------------------------------------------------------------


@lru_cache(maxsize=8)
def builtin_stopwords(lang: str) -> frozenset[str]:
    """Built-in stop-words for ``lang`` (``en``/``sk``/``cs``); unknown → empty."""
    if lang not in STOPWORD_LANGUAGES:
        return frozenset()
    words = set()
    for line in _read_text(f"stopwords_{lang}.txt").splitlines():
        w = line.strip().lower().replace("’", "'")
        if w and not w.startswith("#"):
            words.add(w)
    return frozenset(words)


def _norm_words(words: list[str]) -> set[str]:
    return {w.strip().lower().replace("’", "'") for w in words if w.strip()}


@lru_cache(maxsize=32)
def _stopwords_cached(
    added: tuple[tuple[str, tuple[str, ...]], ...],
    removed: tuple[tuple[str, tuple[str, ...]], ...],
    ignored: tuple[str, ...],
) -> frozenset[str]:
    add = dict(added)
    rem = dict(removed)
    out: set[str] = set()
    for lang in sorted(set(STOPWORD_LANGUAGES) | set(add)):
        words = set(builtin_stopwords(lang)) - _norm_words(list(rem.get(lang, ())))
        words |= _norm_words(list(add.get(lang, ())))
        out |= words
    out |= _norm_words(list(ignored))
    # Forms without diacritics (people often type without them).
    out |= {_fold(w) for w in out}
    return frozenset(out)


def stopwords_for(settings: AppSettings) -> frozenset[str]:
    """All stop-words (EN+SK+CS built-ins - removed + added) plus ``ignored_words``,
    lower-cased, each also in its accent-stripped form. Cached per settings content."""

    def freeze(d: dict[str, list[str]]) -> tuple[tuple[str, tuple[str, ...]], ...]:
        return tuple(sorted((k, tuple(sorted(v))) for k, v in d.items()))

    return _stopwords_cached(
        freeze(settings.stopwords_added),
        freeze(settings.stopwords_removed),
        tuple(sorted(settings.ignored_words)),
    )


# --------------------------------------------------------------------------------------
# Laughter, emoticons, swear words
# --------------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def default_laughter_patterns() -> tuple[str, ...]:
    return tuple(_read_json("laughter.json")["patterns"])


def laughter_patterns_for(settings: AppSettings) -> list[str]:
    """The user's laughter patterns (settings win; they default to the built-ins)."""
    return list(settings.laughter_patterns)


@lru_cache(maxsize=1)
def emoticons() -> tuple[str, ...]:
    return tuple(_read_json("emoticons.json")["emoticons"])


@lru_cache(maxsize=1)
def builtin_swear_words() -> tuple[str, ...]:
    data = _read_json("swear_words.json")
    words: list[str] = []
    for lang in STOPWORD_LANGUAGES:
        for w in data.get(lang, []):
            if w not in words:
                words.append(w)
    return tuple(words)


def swear_words_for(settings: AppSettings) -> list[str]:
    """``settings.swear_words`` if non-empty (the user's edited list), else built-ins."""
    return list(settings.swear_words) if settings.swear_words else list(builtin_swear_words())


# --------------------------------------------------------------------------------------
# Emoji categories and hearts
# --------------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def emoji_category_map() -> dict[str, str]:
    """Emoji (U+FE0F removed, skin tones removed) → category (see ``EMOJI_CATEGORIES``)."""
    data: dict[str, str] = _read_json("emoji_categories.json")["emojis"]
    return data


@lru_cache(maxsize=4096)
def emoji_category(e: str) -> str:
    """Category of one emoji; tolerant of skin tones, U+FE0F and gendered ZWJ variants.

    Unknown emojis fall back to the category of their first code point, else
    ``"symbols"``.
    """
    m = emoji_category_map()
    base = e.replace("\ufe0f", "")
    if base in m:
        return m[base]
    base = "".join(c for c in base if c not in _SKIN_TONES)
    if base in m:
        return m[base]
    for suffix in ("\u200d\u2640", "\u200d\u2642"):
        if base.endswith(suffix) and base.removesuffix(suffix) in m:
            return m[base.removesuffix(suffix)]
    if any(0x1F1E6 <= ord(c) <= 0x1F1FF for c in base):
        return "flags"
    if base and base[0] in m:
        return m[base[0]]
    return "symbols"


@lru_cache(maxsize=1)
def heart_emojis() -> frozenset[str]:
    """Heart symbols (U+FE0F removed): ❤ 🧡 💛 💚 💙 💜 🖤 🤍 🤎 💕 💖 …"""
    return frozenset(_read_json("hearts.json")["hearts"])
