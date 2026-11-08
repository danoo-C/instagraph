"""Locale-aware classification of system / action messages (spec §4.3, decision D1).

Patterns live in ``importer/locales/<code>.json`` (format: ``locales/README.md``), so a
new language is a data file, not a code change. Matching is:

* against the WHOLE trimmed text (``fullmatch``), an optional trailing ``.`` is allowed;
* case-insensitive and accent-insensitive (both the pattern and the text are folded
  character by character, so captured groups are returned from the ORIGINAL text with
  their diacritics);
* when a pattern captures ``actor``, the actor must be a thread participant (full name,
  or first name), one of the locale's ``self_actors`` (``You``), or the participants
  list must be empty — this keeps ordinary sentences such as "I left the group" from
  being classified.

One combined regex per locale is tried first (one C-level pass per text); patterns are
only re-tried one by one when the first match fails the actor check.
"""

import functools
import json
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from typing import Any

from instagraph.store.dataset import CALL_KINDS, SYSTEM_EVENTS
from instagraph.textproc import URL_RE, extract_emojis, normalize_emoji

__all__ = [
    "MAX_SYSTEM_TEXT_LEN",
    "Classification",
    "LocaleDef",
    "PatternDef",
    "available_locales",
    "classify",
    "detect_locale_matches",
    "detect_locales",
    "enabled_locales",
    "fold",
    "load_locale",
    "looks_like_system",
]

#: Texts longer than this are never system messages (cheap pre-filter).
MAX_SYSTEM_TEXT_LEN = 200
#: Auto-detection thresholds (§4.3): a locale is on with ≥ 20 matches or ≥ 0.1 %.
MIN_LOCALE_MATCHES = 20
MIN_LOCALE_RATIO = 0.001
DEFAULT_LOCALE = "en"

_PATTERN_TYPES = ("like", "system", "call", "unsent", "reaction")


@dataclass(frozen=True, slots=True)
class Classification:
    """Result of :func:`classify` for one message text."""

    type: str | None = None
    """Message type override (``like``, ``system``, ``call``, ``unsent``) or ``None``
    (reaction-as-text: the message is not a message, see :attr:`reaction`)."""
    system_event: str | None = None
    """One of ``store.dataset.SYSTEM_EVENTS``."""
    payload: str | None = None
    reaction: tuple[str, str | None] | None = None
    """``(emoji, target hint)`` for an old-style "Reacted 😂 to your message" text."""
    call_kind: str | None = None
    call_missed: bool = False
    pattern_id: str | None = None
    """``<locale>:<pattern id>`` that matched (diagnostics)."""
    actor: str | None = None
    """Captured actor text (original casing), if the pattern has one."""


# --------------------------------------------------------------------------------------
# Accent folding (length-preserving)
# --------------------------------------------------------------------------------------


def _build_fold_table() -> dict[int, str]:
    table: dict[int, str] = {}
    for cp in (*range(0x00C0, 0x0250), *range(0x1E00, 0x1F00)):
        ch = chr(cp)
        decomposed = unicodedata.normalize("NFD", ch)
        if len(decomposed) > 1 and all(unicodedata.combining(c) for c in decomposed[1:]):
            base = decomposed[0]
            if len(base) == 1:
                table[cp] = base
    # Letters without a decomposition that people type without diacritics.
    table.update({ord("ł"): "l", ord("Ł"): "L", ord("đ"): "d", ord("Đ"): "D"})
    table.update({ord("’"): "'", ord("\u02bc"): "'"})
    return table


_FOLD_TABLE = _build_fold_table()


def fold(s: str) -> str:
    """Strip diacritics char by char (``len(fold(s)) == len(s)``): ``"Páči"`` → ``"Paci"``."""
    return s.translate(_FOLD_TABLE)


# --------------------------------------------------------------------------------------
# Locale files
# --------------------------------------------------------------------------------------

_GROUP_RE = re.compile(r"\(\?P<([A-Za-z_][A-Za-z0-9_]*)>")


@dataclass(frozen=True, slots=True)
class PatternDef:
    id: str
    type: str
    regexes: tuple[re.Pattern[str], ...]
    sources: tuple[str, ...]
    event: str | None = None
    payload: str | None = None
    call_kind: str | None = None
    call_missed: bool = False
    check_actor: bool = True


@dataclass(frozen=True, slots=True)
class LocaleDef:
    code: str
    name: str
    verified: bool
    self_actors: frozenset[str]
    patterns: tuple[PatternDef, ...]
    combined: re.Pattern[str] | None
    """Alternation of all regexes; group ``_aN`` marks alternative N."""
    alt_index: tuple[tuple[int, int], ...]
    """Alternative N → (pattern index, regex index within the pattern)."""
    gate: frozenset[str]
    """Word prefixes (see :func:`_gate_keys`); a text without any of them can't match."""


_WORD_RE = re.compile(r"[^\W\d_]+")
_GATE_LEN = 5


def _gate_keys(words: Iterable[str]) -> set[str]:
    """Prefix keys of words with ≥ 4 letters: the first 5 letters (or the 4-letter word)."""
    return {w[:_GATE_LEN] for w in words if len(w) >= 4}


def _text_gate_keys(folded: str) -> set[str]:
    keys: set[str] = set()
    for w in _WORD_RE.findall(folded.lower()):
        if len(w) >= 4:
            keys.add(w[:_GATE_LEN])
            keys.add(w[:4])
    return keys


def _pattern_words(src: str) -> list[str]:
    """Literal words of a regex source (group names removed), folded and lower-cased."""
    literal = _GROUP_RE.sub("(", fold(src)).lower()
    return _WORD_RE.findall(re.sub(r"\\.", " ", literal))


def _compile(src: str) -> re.Pattern[str]:
    return re.compile(rf"(?:{fold(src)})\.?", re.IGNORECASE | re.DOTALL)


def _group_renamer(prefix: str) -> Callable[[re.Match[str]], str]:
    def rename(m: re.Match[str]) -> str:
        return f"(?P<{prefix}{m.group(1)}>"

    return rename


def _parse_locale(code: str, data: Mapping[str, Any]) -> LocaleDef:
    patterns: list[PatternDef] = []
    alternatives: list[str] = []
    alt_index: list[tuple[int, int]] = []
    seen_ids: set[str] = set()
    gate: set[str] = set()
    for p_idx, raw in enumerate(data.get("patterns", [])):
        pid = str(raw["id"])
        if pid in seen_ids:
            raise ValueError(f"locale {code}: duplicate pattern id {pid!r}")
        seen_ids.add(pid)
        ptype = str(raw["type"])
        if ptype not in _PATTERN_TYPES:
            raise ValueError(f"locale {code}: pattern {pid}: bad type {ptype!r}")
        event = raw.get("event")
        if event is not None and event not in SYSTEM_EVENTS:
            raise ValueError(f"locale {code}: pattern {pid}: bad event {event!r}")
        if ptype == "system" and event is None:
            raise ValueError(f"locale {code}: pattern {pid}: type system needs an event")
        call_kind = raw.get("call_kind")
        if call_kind is not None and call_kind not in CALL_KINDS:
            raise ValueError(f"locale {code}: pattern {pid}: bad call_kind {call_kind!r}")
        matches = raw.get("match", [])
        if isinstance(matches, str):
            matches = [matches]
        sources = tuple(str(m) for m in matches)
        if not sources:
            raise ValueError(f"locale {code}: pattern {pid}: empty match list")
        regexes = tuple(_compile(s) for s in sources)
        if ptype == "reaction":
            for s in sources:
                if "(?P<emoji>" not in s:
                    raise ValueError(f"locale {code}: pattern {pid}: reaction needs (?P<emoji>)")
        for r_idx, s in enumerate(sources):
            words = _gate_keys(_pattern_words(s))
            if not words:
                raise ValueError(
                    f"locale {code}: pattern {pid}: needs a literal word of ≥ 4 letters: {s!r}"
                )
            gate |= words
            n = len(alternatives)
            renamed = _GROUP_RE.sub(_group_renamer(f"_a{n}_"), fold(s))
            alternatives.append(f"(?P<_a{n}>(?:{renamed})\\.?)")
            alt_index.append((p_idx, r_idx))
        patterns.append(
            PatternDef(
                id=pid,
                type=ptype,
                regexes=regexes,
                sources=sources,
                event=event,
                payload=raw.get("payload"),
                call_kind=call_kind,
                call_missed=bool(raw.get("call_missed", False)),
                check_actor=bool(raw.get("check_actor", True)),
            )
        )
    combined = (
        re.compile("|".join(alternatives), re.IGNORECASE | re.DOTALL) if alternatives else None
    )
    return LocaleDef(
        code=code,
        name=str(data.get("name", code)),
        verified=bool(data.get("verified", False)),
        self_actors=frozenset(fold(a).casefold() for a in data.get("self_actors", [])),
        patterns=tuple(patterns),
        combined=combined,
        alt_index=tuple(alt_index),
        gate=frozenset(gate),
    )


def _locale_files() -> dict[str, Any]:
    pkg = resources.files("instagraph.importer.locales")
    return {
        entry.name.removesuffix(".json"): entry
        for entry in pkg.iterdir()
        if entry.name.endswith(".json")
    }


@functools.cache
def _available() -> tuple[str, ...]:
    return tuple(sorted(_locale_files()))


def available_locales() -> list[str]:
    """Locale codes with a pattern file (``locales/*.json``), sorted."""
    return list(_available())


@functools.cache
def load_locale(code: str) -> LocaleDef:
    """Parse and compile ``locales/<code>.json`` (cached). ``KeyError`` if unknown."""
    files = _locale_files()
    if code not in files:
        raise KeyError(f"unknown locale {code!r}")
    data = json.loads(files[code].read_text(encoding="utf-8"))
    return _parse_locale(code, data)


# --------------------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------------------


@functools.lru_cache(maxsize=4096)
def _participant_keys(participants: tuple[str, ...]) -> frozenset[str]:
    keys: set[str] = set()
    for name in participants:
        f = fold(name).casefold().strip()
        if not f:
            continue
        keys.add(f)
        first = f.split()[0]
        keys.add(first)
    return frozenset(keys)


def _actor_ok(actor: str | None, loc: LocaleDef, participants: tuple[str, ...]) -> bool:
    if actor is None or not participants:
        return True
    key = fold(actor).casefold().strip()
    return key in loc.self_actors or key in _participant_keys(participants)


class _Defaults(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""


def _build(loc: LocaleDef, pat: PatternDef, groups: dict[str, str | None]) -> Classification | None:
    def g(name: str) -> str | None:
        v = groups.get(name)
        return v.strip() if v is not None and v.strip() else None

    payload: str | None
    if pat.payload is not None:
        payload = pat.payload.format_map(_Defaults({k: (g(k) or "") for k in groups}))
        payload = payload if payload.strip() else None
    else:
        payload = g("payload") or g("target")
    reaction: tuple[str, str | None] | None = None
    if pat.type == "reaction":
        raw_emoji = g("emoji")
        emojis = extract_emojis(raw_emoji) if raw_emoji else []
        if not emojis or raw_emoji is None or len(raw_emoji) > 24:
            return None
        # Only emoji (and spaces) are allowed in the emoji slot.
        if URL_RE.search(raw_emoji) or any(ch.isalnum() for ch in raw_emoji):
            return None
        reaction = (normalize_emoji(emojis[0], merge_skin_tones=False), g("target"))
        payload = None
    return Classification(
        type=None if pat.type == "reaction" else pat.type,
        system_event=pat.event,
        payload=payload,
        reaction=reaction,
        call_kind=pat.call_kind,
        call_missed=pat.call_missed,
        pattern_id=f"{loc.code}:{pat.id}",
        actor=g("actor"),
    )


def _groups_from(m: re.Match[str], original: str, prefix: str = "") -> dict[str, str | None]:
    """Named groups of ``m`` (matched on the folded text) sliced from ``original``."""
    out: dict[str, str | None] = {}
    for name in m.re.groupindex:
        if prefix:
            if not name.startswith(prefix):
                continue
            key = name[len(prefix) :]
        else:
            key = name
        start, end = m.span(name)
        out[key] = original[start:end] if start >= 0 else None
    return out


def _classify_locale(
    loc: LocaleDef, text: str, folded: str, participants: tuple[str, ...], keys: set[str]
) -> Classification | None:
    if loc.combined is None or loc.gate.isdisjoint(keys):
        return None
    m = loc.combined.fullmatch(folded)
    if m is None:
        return None
    alt = m.lastgroup
    if alt is None:  # pragma: no cover - every alternative is a named group
        return None
    n = int(alt[2:])
    p_idx, _ = loc.alt_index[n]
    pat = loc.patterns[p_idx]
    groups = _groups_from(m, text, prefix=f"_a{n}_")
    if not pat.check_actor or _actor_ok(groups.get("actor"), loc, participants):
        result = _build(loc, pat, groups)
        if result is not None:
            return result
    # Slow path: try the remaining alternatives one by one.
    for n2 in range(n + 1, len(loc.alt_index)):
        p2, r2 = loc.alt_index[n2]
        pat2 = loc.patterns[p2]
        m2 = pat2.regexes[r2].fullmatch(folded)
        if m2 is None:
            continue
        groups2 = _groups_from(m2, text)
        if pat2.check_actor and not _actor_ok(groups2.get("actor"), loc, participants):
            continue
        result = _build(loc, pat2, groups2)
        if result is not None:
            return result
    return None


def classify(
    text: str, *, locales: Sequence[str], participants: Sequence[str] = ()
) -> Classification | None:
    """Classify a message ``content`` with the enabled ``locales`` (in order).

    Returns ``None`` for ordinary messages. ``participants`` (thread participant names)
    are used to validate a captured actor; pass ``()`` to skip that check.
    """
    stripped = text.strip()
    if not stripped or len(stripped) > MAX_SYSTEM_TEXT_LEN or not locales:
        return None
    folded = fold(stripped)
    keys = _text_gate_keys(folded)
    if not keys:
        return None
    parts = tuple(participants)
    for code in locales:
        try:
            loc = load_locale(code)
        except KeyError:
            continue
        result = _classify_locale(loc, stripped, folded, parts, keys)
        if result is not None:
            return result
    return None


# --------------------------------------------------------------------------------------
# Locale auto-detection
# --------------------------------------------------------------------------------------


def detect_locale_matches(
    texts: Iterable[str | tuple[str, Sequence[str]]],
    *,
    participants: Sequence[str] = (),
    locales: Sequence[str] | None = None,
) -> dict[str, set[int]]:
    """Per locale, the indices of the ``texts`` its patterns classify (see
    :func:`detect_locales` for the item format)."""
    codes = list(locales) if locales is not None else available_locales()
    matches: dict[str, set[int]] = {c: set() for c in codes}
    for i, item in enumerate(texts):
        if isinstance(item, tuple):
            text, parts = item
        else:
            text, parts = item, participants
        for code in codes:
            if classify(text, locales=(code,), participants=parts) is not None:
                matches[code].add(i)
    return matches


def detect_locales(
    texts: Iterable[str | tuple[str, Sequence[str]]],
    *,
    participants: Sequence[str] = (),
    locales: Sequence[str] | None = None,
) -> dict[str, int]:
    """Count, per locale, how many ``texts`` its patterns classify (§4.3).

    Each item is a text, or ``(text, participants)`` to enable the actor check per text
    (otherwise ``participants`` applies to all). Every locale is tried independently, so
    a text can count for several locales. Returns ``{locale: matches}`` for all locales.
    """
    matches = detect_locale_matches(texts, participants=participants, locales=locales)
    return {code: len(ids) for code, ids in matches.items()}


def enabled_locales(
    match_counts: Mapping[str, int],
    total_messages: int,
    matches: Mapping[str, set[int]] | None = None,
) -> list[str]:
    """Locales with ≥ 20 matches or ≥ 0.1 % of ``total_messages`` (most matches first).

    With ``matches`` (from :func:`detect_locale_matches`), locales are considered in
    that order and only matches NOT already explained by an enabled locale count — so a
    close relative (Czech vs Slovak "opustil skupinu") is not switched on by texts that
    the stronger locale already classifies.

    Falls back to ``["en"]`` when no locale qualifies (and none has any match), so plain
    English placeholders are still recognised in tiny exports.
    """

    def qualifies(n: int) -> bool:
        return n > 0 and (
            n >= MIN_LOCALE_MATCHES
            or (total_messages > 0 and n / total_messages >= MIN_LOCALE_RATIO)
        )

    ordered = sorted(match_counts, key=lambda c: (-match_counts[c], c))
    enabled: list[str] = []
    covered: set[int] = set()
    for code in ordered:
        n = match_counts[code] if matches is None else len(matches.get(code, set()) - covered)
        if qualifies(n):
            enabled.append(code)
            if matches is not None:
                covered |= matches.get(code, set())
    if not enabled:
        return [DEFAULT_LOCALE] if DEFAULT_LOCALE in available_locales() else []
    return enabled


# --------------------------------------------------------------------------------------
# Unmatched system-like text (import report)
# --------------------------------------------------------------------------------------

_LOWER_WORD_RE = re.compile(r"^[^\W\d_][\w'’()/-]*$")


def looks_like_system(text: str, participants: Sequence[str]) -> bool:
    """Heuristic for "unmatched system-like" messages (§4.3): a short text without emoji
    or URL that starts with a participant name (or "You") followed by a lower-case word.
    """
    t = text.strip()
    if not t or len(t) > 120 or "\n" in t or "?" in t or URL_RE.search(t):
        return False
    if extract_emojis(t):
        return False
    candidates = sorted({p for p in participants if p} | {"You"}, key=len, reverse=True)
    for name in candidates:
        for prefix in dict.fromkeys((name, name.split()[0])):
            if len(t) > len(prefix) + 1 and t.startswith(prefix + " "):
                rest = t[len(prefix) + 1 :].split()
                if rest and rest[0][:1].islower() and _LOWER_WORD_RE.match(rest[0]):
                    return True
    return False
