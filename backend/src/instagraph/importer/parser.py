"""Parse one thread folder (all its ``message_N.json`` files) into a ParsedThread.

:func:`parse_thread` is a top-level, picklable function: the pipeline runs it in a
``ProcessPoolExecutor``. Per thread it:

1. loads every file with ``orjson`` and fixes mojibake on EVERY string (§3.4), counting
   the fixes;
2. validates with the raw Pydantic models (one message at a time, so a malformed message
   never fails the file) and counts unknown keys (``"messages[].foo"``);
3. maps each raw message to a :class:`ParsedMessage` (type priority §4.1, share kind,
   links, calls, unsent, reactions, media) and classifies system text (§4.3);
4. turns old-style "Reacted 😂 to your message" texts into :class:`ParsedReaction`
   records on the best target message (they are NOT messages);
5. merges all files, sorts with :meth:`ParsedMessage.sort_key` (stable) and removes
   exact duplicates (:meth:`ParsedMessage.dedupe_key`).

Problems that are not unknown keys (invalid JSON, malformed fields, duplicates…) are
counted in :attr:`ParsedThreadWithStats.stats` (see the ``STAT_*`` constants).
"""

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, cast
from urllib.parse import urlsplit

import orjson
from pydantic import ValidationError

from instagraph.importer.discovery import THREAD_FILE_RE, FileRef, ThreadSource
from instagraph.importer.raw_models import (
    RawMedia,
    RawMessage,
    RawShare,
    RawThreadHeader,
    collect_unknown_keys,
)
from instagraph.importer.system_messages import Classification, classify
from instagraph.importer.types import ParsedMedia, ParsedMessage, ParsedReaction, ParsedThread
from instagraph.store.dataset import (
    TYPE_PRIORITY,
    CallKind,
    MediaKind,
    MessageType,
    ShareKind,
    SystemEvent,
)
from instagraph.textproc import extract_urls, fix_mojibake, normalize_emoji

__all__ = [
    "PLACEHOLDER_EVENTS",
    "STAT_DUPLICATES",
    "STAT_INVALID_JSON",
    "STAT_INVALID_MESSAGE",
    "STAT_MISSING_TIMESTAMP",
    "STAT_REACTION_TEXT",
    "STAT_REACTION_TEXT_UNATTACHED",
    "ParsedThreadWithStats",
    "fix_tree",
    "parse_thread",
    "sample_short_texts",
    "share_kind_from_link",
]

STAT_INVALID_JSON = "invalid_json"
"""Files that could not be read / decoded or lack ``participants``/``messages``."""
STAT_INVALID_MESSAGE = "invalid_message"
"""Message entries that are not JSON objects."""
STAT_INVALID_FIELD_PREFIX = "invalid_field:"
"""``invalid_field:<field>`` — a known field with an unusable value (dropped)."""
STAT_MISSING_TIMESTAMP = "missing_timestamp"
"""Messages skipped because they have no usable ``timestamp_ms``."""
STAT_DUPLICATES = "duplicates_removed"
STAT_REACTION_TEXT = "reaction_text"
"""Reactions-as-text converted into reaction records."""
STAT_REACTION_TEXT_UNATTACHED = "reaction_text_unattached"
"""Reactions-as-text dropped because no earlier message from another sender existed."""

#: Events whose text is just a placeholder for an attachment: when the message has a
#: real attachment/share, the attachment decides the type (and the text is dropped).
PLACEHOLDER_EVENTS: frozenset[str] = frozenset({"attachment", "story_reply", "story_mention"})

_NON_TARGET_TYPES = frozenset({"system", "like"})
_PRIORITY_RANK = {t: i for i, t in enumerate(TYPE_PRIORITY)}
_MS_THRESHOLD = 100_000_000_000  # timestamps above this are milliseconds


@dataclass(slots=True)
class ParsedThreadWithStats(ParsedThread):
    """A :class:`ParsedThread` with parse diagnostics (still a ParsedThread)."""

    stats: Counter[str] = field(default_factory=Counter)


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def fix_tree(node: Any, counter: list[int]) -> Any:
    """Return ``node`` with :func:`fix_mojibake` applied to every string value (dict
    keys included); ``counter[0]`` is incremented for every changed string."""
    if isinstance(node, str):
        if node.isascii():
            return node
        fixed = fix_mojibake(node)
        if fixed != node:
            counter[0] += 1
        return fixed
    if isinstance(node, dict):
        return {
            (k if k.isascii() else fix_tree(k, counter)): fix_tree(v, counter)
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [fix_tree(v, counter) for v in node]
    return node


_RESERVED_IG_PATHS = frozenset(
    {
        "p",
        "reel",
        "reels",
        "tv",
        "stories",
        "explore",
        "accounts",
        "direct",
        "about",
        "legal",
        "developer",
        "s",
        "ar",
        "music",
    }
)


def share_kind_from_link(link: str | None) -> ShareKind:
    """``reel`` / ``post`` / ``story`` / ``profile`` / ``external`` / ``other``."""
    if not link:
        return "other"
    candidate = link.strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    try:
        parts = urlsplit(candidate)
        host = (parts.hostname or "").lower()
    except ValueError:
        return "other"
    if not host:
        return "other"
    host = host.removeprefix("www.").removeprefix("m.")
    if host not in ("instagram.com", "instagr.am", "ig.me"):
        return "external"
    segs = [s for s in parts.path.split("/") if s]
    if not segs:
        return "other"
    first = segs[0].lower()
    if first in ("reel", "reels") or (len(segs) >= 2 and segs[1].lower() in ("reel", "reels")):
        return "reel"
    if first in ("p", "tv") or (len(segs) >= 2 and segs[1].lower() in ("p", "tv")):
        return "post"
    if first in ("stories", "s"):
        return "story"
    if len(segs) == 1 and first not in _RESERVED_IG_PATHS:
        return "profile"
    return "other"


def _file_index(ref: FileRef, fallback: int) -> int:
    m = THREAD_FILE_RE.match(ref.name)
    return int(m.group(1)) if m else fallback


def _validate_message(raw: dict[str, Any], stats: Counter[str]) -> RawMessage | None:
    try:
        return RawMessage.model_validate(raw)
    except ValidationError as exc:
        bad = {str(err["loc"][0]) for err in exc.errors() if err.get("loc")}
        for name in sorted(bad):
            stats[f"{STAT_INVALID_FIELD_PREFIX}messages[].{name}"] += 1
        cleaned = {k: v for k, v in raw.items() if k not in bad}
        try:
            return RawMessage.model_validate(cleaned)
        except ValidationError:  # pragma: no cover - defensive
            stats[STAT_INVALID_MESSAGE] += 1
            return None


def _validate_header(data: dict[str, Any], stats: Counter[str]) -> RawThreadHeader | None:
    if not isinstance(data.get("participants"), list) or not isinstance(data.get("messages"), list):
        return None
    try:
        return RawThreadHeader.model_validate(data)
    except ValidationError as exc:
        bad = {
            str(err["loc"][0])
            for err in exc.errors()
            if err.get("loc") and err["loc"][0] not in ("participants", "messages")
        }
        for name in sorted(bad):
            stats[f"{STAT_INVALID_FIELD_PREFIX}{name}"] += 1
        cleaned = {k: v for k, v in data.items() if k not in bad}
        # Participants with odd entries: keep only dicts.
        cleaned["participants"] = [p for p in data["participants"] if isinstance(p, dict)]
        try:
            return RawThreadHeader.model_validate(cleaned)
        except ValidationError:
            return None


def _media_items(items: list[RawMedia] | None) -> list[RawMedia]:
    return [m for m in items or [] if m is not None]


def _reaction_ts(ts: int | None) -> int | None:
    if ts is None or ts <= 0:
        return None
    return ts // 1000 if ts > _MS_THRESHOLD else ts


def _share_present(share: RawShare | None) -> bool:
    if share is None:
        return False
    return any(
        (
            share.link,
            share.share_text,
            share.original_content_owner,
            share.profile_share_username,
            share.profile_share_name,
        )
    )


@dataclass(slots=True)
class _PendingReaction:
    actor: str
    emoji: str
    target_hint: str | None
    timestamp_ms: int
    file_index: int
    position: int

    def sort_key(self) -> tuple[int, int, int]:
        return (self.timestamp_ms, -self.file_index, -self.position)


def _pick_type(parts: set[str]) -> MessageType:
    if not parts:
        return "unknown"
    return cast(MessageType, min(parts, key=lambda p: _PRIORITY_RANK[p]))


def _build_message(
    raw: RawMessage,
    *,
    file_index: int,
    position: int,
    locales: Sequence[str],
    participants: Sequence[str],
) -> ParsedMessage | _PendingReaction | None:
    ts = raw.timestamp_ms
    if ts is None:
        return None
    sender = (raw.sender_name or "").strip()
    text = raw.content if raw.content and raw.content.strip() else None

    photos = _media_items(raw.photos)
    videos = _media_items(raw.videos)
    audios = _media_items(raw.audio_files)
    gifs = _media_items(raw.gifs)
    sticker = raw.sticker
    media: list[ParsedMedia] = []
    groups: list[tuple[MediaKind, list[RawMedia]]] = [
        ("photo", photos),
        ("video", videos),
        ("audio", audios),
        ("gif", gifs),
        ("sticker", [sticker] if sticker is not None else []),
    ]
    for kind, items in groups:
        for item in items:
            if item.uri:
                media.append(ParsedMedia(kind, item.uri))
    has_share = _share_present(raw.share)
    has_attachment = bool(photos or videos or audios or gifs or sticker is not None or has_share)

    cls: Classification | None = None
    if text is not None and locales:
        cls = classify(text, locales=locales, participants=participants)

    if cls is not None and cls.reaction is not None and not has_attachment:
        emoji, hint = cls.reaction
        return _PendingReaction(
            actor=sender,
            emoji=emoji,
            target_hint=hint,
            timestamp_ms=ts,
            file_index=file_index,
            position=position,
        )

    msg = ParsedMessage(
        sender_name=sender,
        timestamp_ms=ts,
        n_photos=len(photos),
        n_videos=len(videos),
        n_audios=len(audios),
        n_gifs=len(gifs),
        n_stickers=1 if sticker is not None else 0,
        media=media,
        file_index=file_index,
        position=position,
        is_unsent=bool(raw.is_unsent),
    )
    parts: set[str] = set()

    if has_share and raw.share is not None:
        share = raw.share
        msg.share_link = share.link or None
        msg.share_text = share.share_text or None
        msg.share_owner = share.original_content_owner or share.profile_share_username or None
        s_kind = share_kind_from_link(share.link)
        if s_kind == "other" and share.profile_share_username and not share.link:
            s_kind = "profile"
        msg.share_kind = s_kind
        parts.add("share")
    for name, count in (
        ("video", msg.n_videos),
        ("photo", msg.n_photos),
        ("audio", msg.n_audios),
        ("gif", msg.n_gifs),
        ("sticker", msg.n_stickers),
    ):
        if count:
            parts.add(name)

    keep_text = True
    if cls is not None:
        if cls.reaction is not None:
            # Reaction text with an attachment: keep as attachment, drop the notice.
            keep_text = False
        elif cls.type == "system":
            event = cls.system_event
            msg.system_event = cast(SystemEvent | None, event)
            msg.system_payload = cls.payload
            keep_text = False
            if event in PLACEHOLDER_EVENTS and has_attachment:
                pass  # the attachment decides the type
            else:
                parts.add("system")
            if event == "call_event" and cls.call_kind is not None:
                msg.call_kind = cast(CallKind, cls.call_kind)
        elif cls.type == "like":
            parts.add("like")
            keep_text = False
        elif cls.type == "call":
            parts.add("call")
            msg.call_kind = cast(CallKind, cls.call_kind or "unknown")
            msg.call_missed = cls.call_missed
            keep_text = False
        elif cls.type == "unsent":
            parts.add("unsent")
            msg.is_unsent = True
            keep_text = False

    if raw.call_duration is not None or (raw.type or "").lower() == "call":
        parts.add("call")
        msg.call_duration_s = max(raw.call_duration, 0) if raw.call_duration is not None else None
        if msg.call_kind is None:
            msg.call_kind = "unknown"
        if raw.missed:
            msg.call_missed = True
    if raw.is_unsent:
        parts.add("unsent")

    if keep_text and text is not None:
        msg.text = text
        msg.links = extract_urls(text)
        if msg.links:
            parts.add("link")
        parts.add("text")

    if cls is None and "system" not in parts:
        old_type = (raw.type or "").lower()
        if old_type in ("subscribe", "unsubscribe"):
            names = [u.name for u in raw.users or [] if u is not None and u.name]
            msg.system_event = "join" if old_type == "subscribe" else "leave"
            msg.system_payload = ", ".join(names) or None
            parts.add("system")

    msg.type = _pick_type(parts)

    for r in raw.reactions or []:
        if r is None or not r.reaction:
            continue
        emoji = normalize_emoji(r.reaction.strip(), merge_skin_tones=False)
        if not emoji:
            continue
        msg.reactions.append(
            ParsedReaction(emoji, (r.actor or "").strip(), _reaction_ts(r.timestamp))
        )
    return msg


def _attach_reactions(
    messages: list[ParsedMessage], pending: list[_PendingReaction], stats: Counter[str]
) -> None:
    """Attach reactions-as-text to the most recent earlier message from another sender
    (preferring the hinted person's latest message when the hint names a sender)."""
    if not pending:
        return
    pending.sort(key=_PendingReaction.sort_key)
    last_by_sender: dict[str, int] = {}
    last: int | None = None
    prev_other: int | None = None  # latest index whose sender != messages[last].sender
    mi = 0
    for pr in pending:
        key = pr.sort_key()
        while mi < len(messages) and messages[mi].sort_key() < key:
            m = messages[mi]
            if m.type not in _NON_TARGET_TYPES:
                if last is not None and messages[last].sender_name != m.sender_name:
                    prev_other = last
                last = mi
                last_by_sender[m.sender_name] = mi
            mi += 1
        target: int | None = None
        if pr.target_hint and pr.target_hint != pr.actor:
            target = last_by_sender.get(pr.target_hint)
        if target is None and last is not None:
            target = last if messages[last].sender_name != pr.actor else prev_other
        if target is None:
            stats[STAT_REACTION_TEXT_UNATTACHED] += 1
            continue
        messages[target].reactions.append(
            ParsedReaction(pr.emoji, pr.actor, pr.timestamp_ms // 1000)
        )
        stats[STAT_REACTION_TEXT] += 1


def _load_json(ref: FileRef, stats: Counter[str], fixes: list[int]) -> dict[str, Any] | None:
    try:
        data = orjson.loads(ref.read_bytes())
    except (OSError, KeyError, ValueError):
        stats[STAT_INVALID_JSON] += 1
        return None
    if not isinstance(data, dict):
        stats[STAT_INVALID_JSON] += 1
        return None
    return cast(dict[str, Any], fix_tree(data, fixes))


# --------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------


def parse_thread(src: ThreadSource, locales: Sequence[str]) -> ParsedThread:
    """Parse all files of ``src`` into one sorted, deduplicated thread record.

    Returns a :class:`ParsedThreadWithStats`. ``locales`` are the enabled system-message
    locales (empty → no system-message classification).
    """
    locales = tuple(locales)
    stats: Counter[str] = Counter()
    unknown: Counter[str] = Counter()
    fixes = [0]
    title: str | None = None
    participants: list[str] = []
    is_still: bool | None = None
    thread_path: str | None = None
    messages: list[ParsedMessage] = []
    pending: list[_PendingReaction] = []

    for n, ref in enumerate(src.files, start=1):
        file_index = _file_index(ref, n)
        data = _load_json(ref, stats, fixes)
        if data is None:
            continue
        header = _validate_header(data, stats)
        if header is None:
            stats[STAT_INVALID_JSON] += 1
            continue
        collect_unknown_keys(header, unknown)
        # Thread fields: the first file that has them wins; participants are unioned.
        if title is None and header.title:
            title = header.title
        if thread_path is None and header.thread_path:
            thread_path = header.thread_path
        if header.is_still_participant is not None:
            is_still = (
                header.is_still_participant
                if is_still is None
                else is_still and header.is_still_participant
            )
        for p in header.participants:
            name = (p.name or "").strip()
            if name not in participants:
                participants.append(name)
        participants_t = tuple(participants)

        for position, item in enumerate(header.messages):
            if not isinstance(item, dict):
                stats[STAT_INVALID_MESSAGE] += 1
                continue
            raw = _validate_message(item, stats)
            if raw is None:
                continue
            collect_unknown_keys(raw, unknown, "messages[]")
            built = _build_message(
                raw,
                file_index=file_index,
                position=position,
                locales=locales,
                participants=participants_t,
            )
            if built is None:
                stats[STAT_MISSING_TIMESTAMP] += 1
            elif isinstance(built, _PendingReaction):
                pending.append(built)
            else:
                messages.append(built)

    messages.sort(key=ParsedMessage.sort_key)
    deduped: list[ParsedMessage] = []
    seen: set[tuple[str, int, str | None, tuple[str, ...]]] = set()
    for m in messages:
        key = m.dedupe_key()
        if key in seen:
            stats[STAT_DUPLICATES] += 1
            continue
        seen.add(key)
        deduped.append(m)
    _attach_reactions(deduped, pending, stats)

    return ParsedThreadWithStats(
        folder=src.folder,
        category=src.category,
        title=title,
        participants=participants,
        is_still_participant=True if is_still is None else is_still,
        messages=deduped,
        unknown_keys=unknown,
        encoding_fixes=fixes[0],
        source=src.describe(),
        file_count=len(src.files),
        thread_path=thread_path,
        stats=stats,
    )


_SHORT_TEXT_LEN = 200
_EMPTY_RE = re.compile(r"^\s*$")


def sample_short_texts(src: ThreadSource, limit: int = 2000) -> list[str]:
    """Up to ``limit`` mojibake-fixed ``content`` strings ≤ 200 chars from ``src``, for
    locale auto-detection (no validation, cheap)."""
    out: list[str] = []
    for ref in src.files:
        try:
            data = orjson.loads(ref.read_bytes())
        except (OSError, KeyError, ValueError):
            continue
        msgs = data.get("messages") if isinstance(data, dict) else None
        if not isinstance(msgs, list):
            continue
        for m in msgs:
            if not isinstance(m, dict):
                continue
            content = m.get("content")
            if not isinstance(content, str) or len(content) > _SHORT_TEXT_LEN * 2:
                continue
            fixed = fix_mojibake(content)
            if len(fixed) > _SHORT_TEXT_LEN or _EMPTY_RE.match(fixed):
                continue
            out.append(fixed)
            if len(out) >= limit:
                return out
    return out
