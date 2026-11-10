"""Hiding names from the AI (docs/ai-assistant.md A6).

:class:`Pseudonymizer` replaces people, usernames and chat titles in tool results with
ASCII placeholders before anything leaves the computer, and translates Claude's answer
back:

* the owner → ``[ME]``; other people → ``[P1]``, ``[P2]``, … in ascending ``person_id``
  order; chats → ``[C1]``, … in ascending ``thread_id`` order. Both are stable per
  dataset.
* ``@mentions`` and share owners that aren't a known person's username → ``[U1]``, …
  assigned on first sight (stable within one instance).

Free text goes through a word matcher built once per dataset (:meth:`encode_text`):

* Patterns come from display names, their parts (only parts with ≥ 3 letters that
  aren't stop-words), aliases, usernames and chat titles (≥ 3 characters). A whole
  name, alias or username needs ≥ 3 letters to be matched inside text; shorter ones are
  only replaced when they are the *whole* string (e.g. a person called "Me").
* Matching is on whole words, case- and accent-insensitive (``strip_accents`` +
  ``casefold`` on a folded copy whose positions map back to the original string).
* Slovak / Czech inflection: a name token also matches its stem plus an ending from
  ``resources/name_endings.json`` (Daniel → Danielovi, Dan → Danko, Eva → Evou,
  Pavel → Pavla). Inflected forms that are stop-words (``áno`` from ``Ana``) are never
  treated as names. Usernames and chat titles match only exactly (not inflected).
* Emoji / symbols around a name or title belong to it ("Partia 🍻" → ``[C2]``, not
  ``[C2] 🍻``); symbol-only aliases and titles are matched literally.
* The longest match wins (full name before first name). Ties are broken by: an exact
  form before an inflected one, then the kind of pattern (display name < alias <
  username < name part < chat title), then the lower id. So a bare "Eva" belongs to the
  person *called* "Eva" if there is one, else to the "Eva …" with the lowest
  ``person_id``; a chat titled like a person's name is replaced with the person.
* People with ``match_in_text=False`` (names that are common words) are only replaced
  when a string is exactly their display name or username, or in ``@mentions`` of their
  username.
* Existing placeholders are never touched, so encoding is idempotent.
"""

import json
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from itertools import pairwise
from typing import Any

import regex

from instagraph.resources import STOPWORD_LANGUAGES, builtin_stopwords
from instagraph.textproc import MENTION_RE, strip_accents

__all__ = [
    "ChatEntry",
    "PersonEntry",
    "Pseudonymizer",
    "StreamDecoder",
    "name_endings",
]

PLACEHOLDER_RE = re.compile(r"\[(?:ME|[PCU]\d{1,4})\]")
_PARTIAL_PLACEHOLDER_RE = re.compile(r"\[(?:ME?|[PCU]\d{0,4})?\Z")
_HOLD_BACK = 7  # "[" + up to 6 chars; the regex above decides what can still complete

_WORD_RE = regex.compile(r"\w+")
_VOWELS = frozenset("aeiouy")
_MIN_NAME_LETTERS = 3
_MIN_TITLE_CHARS = 3
_MIN_STEM = 2

# Pattern kinds, in tie-break order.
_RANK_DISPLAY = 0
_RANK_ALIAS = 1
_RANK_USERNAME = 2
_RANK_PART = 3
_RANK_CHAT = 4

_BASES_CACHE_MAX = 200_000


@dataclass(frozen=True)
class PersonEntry:
    person_id: int
    display_name: str
    aliases: tuple[str, ...] = ()  # merged names + user-entered nicknames
    usernames: tuple[str, ...] = ()  # instagram handles without '@'
    is_owner: bool = False
    # False = don't match this person's names inside free text (common-word names);
    # a string that is exactly their display name / username is still replaced.
    match_in_text: bool = True


@dataclass(frozen=True)
class ChatEntry:
    thread_id: int
    title: str


@dataclass(frozen=True, slots=True)
class _Phrase:
    tokens: tuple[str, ...]  # folded word tokens
    gaps: tuple[str, ...]  # folded text between tokens, whitespace removed
    prefix: str  # non-word text before the first token ("🍻 Partia"), whitespace removed
    suffix: str  # non-word text after the last token ("Partia 🍻"), whitespace removed
    inflect: bool  # tokens may match inflected forms
    rank: int
    order_id: int
    placeholder: str


# --------------------------------------------------------------------------------------
# Folding helpers
# --------------------------------------------------------------------------------------

_FOLD_CACHE: dict[str, str] = {}


def _fold_char(c: str) -> str:
    f = _FOLD_CACHE.get(c)
    if f is None:
        f = strip_accents(c).casefold()
        if len(_FOLD_CACHE) < 100_000:
            _FOLD_CACHE[c] = f
    return f


def _fold(s: str) -> str:
    """Accent- and case-insensitive form, folded char by char (same as the text scan)."""
    if s.isascii():
        return s.lower()
    return "".join(_fold_char(c) for c in s)


def _fold_with_map(text: str) -> tuple[str, list[int] | None]:
    """Folded copy + the original index of every folded char (``None`` = identity)."""
    if text.isascii():
        return text.lower(), None
    parts: list[str] = []
    starts: list[int] = []
    for i, c in enumerate(text):
        f = _fold_char(c)
        if f:
            parts.append(f)
            starts.extend([i] * len(f))
    return "".join(parts), starts


def _squash(s: str) -> str:
    """Remove all whitespace (gaps between name words are compared without it)."""
    return "".join(s.split())


def _norm_key(s: str) -> str:
    """Whole-string lookup key: folded, trimmed, inner whitespace collapsed."""
    return " ".join(_fold(s).split())


@dataclass(frozen=True, slots=True)
class _NameParts:
    tokens: tuple[str, ...]
    gaps: tuple[str, ...]
    prefix: str
    suffix: str


def _split_name(folded: str) -> _NameParts:
    """Word tokens of a folded name, the (whitespace-free) gaps between them and the
    non-word text around them."""
    matches = list(_WORD_RE.finditer(folded))
    tokens = tuple(m.group() for m in matches)
    gaps = tuple(_squash(folded[a.end() : b.start()]) for a, b in pairwise(matches))
    if not matches:
        return _NameParts((), (), "", "")
    return _NameParts(
        tokens, gaps, _squash(folded[: matches[0].start()]), _squash(folded[matches[-1].end() :])
    )


def _consume_forward(s: str, pos: int, target: str) -> int | None:
    """End of ``target`` read from ``s[pos:]`` ignoring whitespace, or None."""
    i = pos
    for ch in target:
        while i < len(s) and s[i].isspace():
            i += 1
        if i >= len(s) or s[i] != ch:
            return None
        i += 1
    return i


def _consume_backward(s: str, pos: int, target: str) -> int | None:
    """Start of ``target`` read backwards from ``s[:pos]`` ignoring whitespace, or None."""
    i = pos
    for ch in reversed(target):
        while i > 0 and s[i - 1].isspace():
            i -= 1
        if i <= 0 or s[i - 1] != ch:
            return None
        i -= 1
    return i


def _letters(tokens: Iterable[str]) -> int:
    return sum(c.isalpha() for t in tokens for c in t)


def _stems(token: str) -> set[str]:
    """Stems a Slovak / Czech ending can attach to (empty = token isn't inflected)."""
    if len(token) < _MIN_NAME_LETTERS or not token.isalpha():
        return set()
    out: set[str] = set()
    if token[-1] in _VOWELS:
        if len(token) - 1 >= _MIN_STEM:
            out.add(token[:-1])  # Eva → Ev-ou, Jana → Jan-ka
    else:
        out.add(token)  # Daniel → Daniel-ovi, Dan → Dan-ko
        if len(token) >= 4 and token[-2] == "e" and token[-1] in "kcl":
            out.add(token[:-2] + token[-1])  # Pavel → Pavl-a, Marek → Mark-a
    return out


@lru_cache(maxsize=1)
def name_endings() -> frozenset[str]:
    """Folded Slovak / Czech name endings from ``resources/name_endings.json``."""
    raw = resources.files("instagraph.resources").joinpath("name_endings.json")
    data = json.loads(raw.read_text(encoding="utf-8"))
    return frozenset(_fold(e) for e in data["endings"] if e.strip())


#: Where the "name" part of a display name ends ("Matej Tóth | Software Developer",
#: "Katarina (official)", "Shop - Bratislava"): only words before it become name parts.
_NAME_CUT_RE = re.compile(r"\s*(?:[|(\[{•·,/@]|\s[-–—]\s)")
#: Words never used as a lone name part (bios, titles and Instagram's placeholder for
#: deleted accounts, "Instagram User").
_GENERIC_PARTS = frozenset(
    {
        "instagram",
        "user",
        "users",
        "official",
        "account",
        "page",
        "profile",
        "real",
        "shop",
        "store",
        "team",
        "club",
        "group",
        "studio",
        "music",
        "art",
        "design",
        "designer",
        "developer",
        "software",
        "engineer",
        "photo",
        "photography",
        "photographer",
        "creator",
        "blog",
        "news",
        "media",
        "fan",
        "fans",
        "admin",
    }
)


def _name_head(folded: str) -> str:
    return _NAME_CUT_RE.split(folded, maxsplit=1)[0]


@lru_cache(maxsize=1)
def _stopwords() -> frozenset[str]:
    words: set[str] = set()
    for lang in STOPWORD_LANGUAGES:
        words |= {_fold(w) for w in builtin_stopwords(lang)}
    return frozenset(words)


# --------------------------------------------------------------------------------------
# Pseudonymizer
# --------------------------------------------------------------------------------------


class Pseudonymizer:
    """Name ↔ placeholder translation for one dataset (build once, reuse)."""

    def __init__(self, people: Iterable[PersonEntry], chats: Iterable[ChatEntry]) -> None:
        people_list = sorted(people, key=lambda p: p.person_id)
        chat_list = sorted(chats, key=lambda c: c.thread_id)

        self._person_ph: dict[int, str] = {}
        self._chat_ph: dict[int, str] = {}
        self._decode: dict[str, str] = {}
        n = 0
        for p in people_list:
            if p.is_owner:
                ph = "[ME]"
            else:
                n += 1
                ph = f"[P{n}]"
            self._person_ph[p.person_id] = ph
            self._decode.setdefault(ph, p.display_name.strip() or p.display_name)
        for i, c in enumerate(chat_list, start=1):
            ph = f"[C{i}]"
            self._chat_ph[c.thread_id] = ph
            self._decode[ph] = c.title.strip() or c.title

        self._lock = threading.Lock()
        self._unknown_users: dict[str, str] = {}  # folded handle → [Un]

        self._stop = _stopwords()
        self._endings = name_endings()
        self._ending_lengths = sorted({len(e) for e in self._endings})

        # Whole-string matches: key → ((rank, id), placeholder).
        exact: dict[str, tuple[tuple[int, int], str]] = {}
        # Known usernames (folded, no '@') → placeholder; used for @mentions.
        self._usernames: dict[str, str] = {}
        phrases: dict[tuple[_NameParts, bool], _Phrase] = {}
        literals: dict[str, tuple[tuple[int, int], str]] = {}  # names without word chars
        self._base_tokens: set[str] = set()
        self._stem_map: dict[str, set[str]] = {}

        def add_exact(key: str, prio: tuple[int, int], ph: str) -> None:
            if key and (key not in exact or prio < exact[key][0]):
                exact[key] = (prio, ph)

        def add_phrase(name: _NameParts, inflect: bool, rank: int, order_id: int, ph: str) -> None:
            k = (name, inflect)
            cur = phrases.get(k)
            if cur is None or (rank, order_id) < (cur.rank, cur.order_id):
                phrases[k] = _Phrase(
                    name.tokens, name.gaps, name.prefix, name.suffix, inflect, rank, order_id, ph
                )
            self._base_tokens.update(name.tokens)
            if inflect:
                for t in name.tokens:
                    for stem in _stems(t):
                        self._stem_map.setdefault(stem, set()).add(t)

        for p in people_list:
            ph = self._person_ph[p.person_id]
            pid = p.person_id
            add_exact(_norm_key(p.display_name), (_RANK_DISPLAY, pid), ph)
            for u in p.usernames:
                handle = _fold(u).strip().lstrip("@")
                if not handle:
                    continue
                self._usernames.setdefault(handle, ph)
                add_exact(handle, (_RANK_USERNAME, pid), ph)
                add_exact("@" + handle, (_RANK_USERNAME, pid), ph)
                if p.match_in_text and len(handle) >= _MIN_NAME_LETTERS:
                    parts = _split_name(handle)
                    if parts.tokens:
                        add_phrase(parts, False, _RANK_USERNAME, pid, ph)
            if not p.match_in_text:
                continue
            names = [(p.display_name, _RANK_DISPLAY)] + [(a, _RANK_ALIAS) for a in p.aliases]
            for name, rank in names:
                folded = _fold(name).strip()
                if not folded:
                    continue
                add_exact(_norm_key(name), (rank, pid), ph)
                parts = _split_name(folded)
                if not parts.tokens:
                    # emoji / symbol-only alias: matched literally
                    prio = (rank, pid)
                    if folded not in literals or prio < literals[folded][0]:
                        literals[folded] = (prio, ph)
                    continue
                if _letters(parts.tokens) >= _MIN_NAME_LETTERS:
                    add_phrase(parts, True, rank, pid, ph)
                if len(parts.tokens) > 1:
                    for t in _split_name(_name_head(folded)).tokens:
                        if (
                            _letters((t,)) >= _MIN_NAME_LETTERS
                            and t not in self._stop
                            and t not in _GENERIC_PARTS
                        ):
                            add_phrase(_NameParts((t,), (), "", ""), True, _RANK_PART, pid, ph)

        for c in chat_list:
            ph = self._chat_ph[c.thread_id]
            tid = c.thread_id
            add_exact(_norm_key(c.title), (_RANK_CHAT, tid), ph)
            folded = _fold(c.title).strip()
            if len(folded) < _MIN_TITLE_CHARS:
                continue
            parts = _split_name(folded)
            if parts.tokens:
                add_phrase(parts, False, _RANK_CHAT, tid, ph)
            elif folded not in literals:
                literals[folded] = ((_RANK_CHAT, tid), ph)

        self._exact = {k: v[1] for k, v in exact.items()}
        self._literals = {k: v[1] for k, v in literals.items()}
        self._first: dict[str, list[_Phrase]] = {}
        for phrase in phrases.values():
            self._first.setdefault(phrase.tokens[0], []).append(phrase)
        self._bases_cache: dict[str, dict[str, bool]] = {}

        alternatives = [
            r"(?P<ph>\[(?:me|[pcu]\d{1,4})\])",
            # not right after a placeholder: "[U1]@x" must stay as it is (idempotency)
            f"(?<!\\])(?P<mention>{MENTION_RE.pattern})",
        ]
        if self._literals:
            lits = sorted(self._literals, key=len, reverse=True)
            alternatives.append("(?P<lit>" + "|".join(regex.escape(s) for s in lits) + ")")
        alternatives.append(r"(?P<word>\w+)")
        self._scan = regex.compile("|".join(alternatives))

    # ---------------------------------------------------------------- placeholders

    def person_placeholder(self, person_id: int) -> str:
        """``[ME]`` / ``[Pn]`` for a known person; ``KeyError`` for an unknown id."""
        return self._person_ph[person_id]

    def chat_placeholder(self, thread_id: int) -> str:
        """``[Cn]`` for a known chat; ``KeyError`` for an unknown id."""
        return self._chat_ph[thread_id]

    def username_placeholder(self, handle: str) -> str:
        """Placeholder for an Instagram handle (with or without '@'), e.g. a share
        owner: the person's placeholder if it's a known username, else ``[Un]``."""
        h = handle.strip()
        if h.startswith("@"):
            h = h[1:]
        if not h:
            return handle
        return self._username(_fold(h), "@" + h)

    def _username(self, folded_handle: str, original: str) -> str:
        known = self._usernames.get(folded_handle)
        if known is not None:
            return known
        with self._lock:
            ph = self._unknown_users.get(folded_handle)
            if ph is None:
                ph = f"[U{len(self._unknown_users) + 1}]"
                self._unknown_users[folded_handle] = ph
                self._decode[ph] = original
            return ph

    # ---------------------------------------------------------------- encoding

    def encode_value(self, value: Any) -> Any:
        """Encode every string inside JSON-like data; dict keys and non-strings are
        left untouched."""
        if isinstance(value, str):
            return self.encode_text(value)
        if isinstance(value, dict):
            return {k: self.encode_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.encode_value(v) for v in value]
        if isinstance(value, tuple):
            return tuple(self.encode_value(v) for v in value)
        return value

    def encode_text(self, text: str) -> str:
        """Replace names, usernames, @mentions and chat titles in free text."""
        if not text:
            return text
        whole = self._encode_whole(text)
        if whole is not None:
            return whole

        folded, starts = _fold_with_map(text)

        def orig(k: int) -> int:
            if starts is None:
                return k
            return starts[k] if k < len(starts) else len(text)

        # word tokens; None = something a name can't span (placeholder, mention, literal)
        words: list[tuple[int, int, str] | None] = []
        reps: list[tuple[int, int, str]] = []  # folded start, folded end, placeholder
        for m in self._scan.finditer(folded):
            kind = m.lastgroup
            if kind == "word":
                words.append((m.start(), m.end(), m.group()))
                continue
            words.append(None)
            if kind == "mention":
                a, b = m.span()
                reps.append((a, b, self._username(m.group()[1:], text[orig(a) : orig(b)])))
            elif kind == "lit":
                reps.append((m.start(), m.end(), self._literals[m.group()]))

        i = 0
        while i < len(words):
            w = words[i]
            if w is None:
                i += 1
                continue
            best = self._match_at(words, i, folded)
            if best is None:
                i += 1
                continue
            n, start, end, ph = best
            reps.append((start, end, ph))
            i += n

        if not reps:
            return text
        reps.sort()
        out: list[str] = []
        pos = 0
        for a, b, ph in reps:
            oa = orig(a)
            if oa < pos:  # overlaps the previous replacement (e.g. a name's emoji prefix)
                continue
            out.append(text[pos:oa])
            out.append(ph)
            pos = orig(b)
        out.append(text[pos:])
        return "".join(out)

    def _encode_whole(self, text: str) -> str | None:
        stripped = text.strip()
        if not stripped:
            return None
        key = _norm_key(stripped)
        ph = self._exact.get(key)
        if ph is None and key.startswith("@") and key[1:] in self._usernames:
            ph = self._usernames[key[1:]]
        if ph is None:
            return None
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()) :]
        return lead + ph + trail

    def _bases(self, token: str) -> dict[str, bool]:
        """Name tokens ``token`` can be a form of → True if exact, False if inflected."""
        cached = self._bases_cache.get(token)
        if cached is not None:
            return cached
        out: dict[str, bool] = {}
        if token in self._base_tokens:
            out[token] = True
        if token not in self._stop:
            n = len(token)
            for k in self._ending_lengths:
                if n - k < _MIN_STEM:
                    break
                if token[n - k :] in self._endings:
                    for base in self._stem_map.get(token[: n - k], ()):
                        out.setdefault(base, False)
        if len(self._bases_cache) >= _BASES_CACHE_MAX:
            self._bases_cache.clear()
        self._bases_cache[token] = out
        return out

    def _match_at(
        self, words: list[tuple[int, int, str] | None], i: int, folded: str
    ) -> tuple[int, int, int, str] | None:
        """Best phrase starting at word ``i``: (word count, folded start, folded end,
        placeholder)."""
        w = words[i]
        assert w is not None
        best_key: tuple[int, int, int, int] | None = None
        best: tuple[int, int, int, str] | None = None
        for base, exact in self._bases(w[2]).items():
            for ph in self._first.get(base, ()):
                if not exact and not ph.inflect:
                    continue
                inflected = 0 if exact else 1
                n = len(ph.tokens)
                ok = True
                for j in range(1, n):
                    if i + j >= len(words):
                        ok = False
                        break
                    prev, cur = words[i + j - 1], words[i + j]
                    if prev is None or cur is None:
                        ok = False
                        break
                    if _squash(folded[prev[1] : cur[0]]) != ph.gaps[j - 1]:
                        ok = False
                        break
                    e = self._bases(cur[2]).get(ph.tokens[j])
                    if e is None or (not e and not ph.inflect):
                        ok = False
                        break
                    if not e:
                        inflected += 1
                if not ok:
                    continue
                last = words[i + n - 1]
                assert last is not None
                start, end = w[0], last[1]
                if ph.prefix:
                    s0 = _consume_backward(folded, start, ph.prefix)
                    start = start if s0 is None else s0
                if ph.suffix:
                    e0 = _consume_forward(folded, end, ph.suffix)
                    end = end if e0 is None else e0
                key = (start - end, inflected, ph.rank, ph.order_id)
                if best_key is None or key < best_key:
                    best_key = key
                    best = (n, start, end, ph.placeholder)
        return best

    # ---------------------------------------------------------------- decoding

    def decode_text(self, text: str) -> str:
        """Placeholders → display names / chat titles / '@handle'; unknown ones stay."""
        if "[" not in text:
            return text
        return PLACEHOLDER_RE.sub(lambda m: self._decode.get(m.group(), m.group()), text)

    def stream_decoder(self) -> "StreamDecoder":
        return StreamDecoder(self)


class StreamDecoder:
    """Decodes streamed text; a placeholder may be split across chunks anywhere."""

    def __init__(self, pseudonymizer: Pseudonymizer) -> None:
        self._p = pseudonymizer
        self._buf = ""

    def feed(self, chunk: str) -> str:
        """Decoded text safe to emit now; a trailing possible placeholder start is held."""
        buf = self._buf + chunk
        idx = buf.rfind("[")
        if (
            idx != -1
            and len(buf) - idx <= _HOLD_BACK
            and _PARTIAL_PLACEHOLDER_RE.match(buf, idx) is not None
        ):
            self._buf = buf[idx:]
            buf = buf[:idx]
        else:
            self._buf = ""
        return self._p.decode_text(buf)

    def flush(self) -> str:
        """End of stream: whatever is held back, decoded."""
        buf, self._buf = self._buf, ""
        return self._p.decode_text(buf)
