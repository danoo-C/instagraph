"""Intermediate records emitted by the parser and consumed by the merge/derive pipeline.

Flow (§8.3)::

    raw message_N.json files of one thread folder
        └─ parser (merge files, sort, dedupe) ─► ParsedThread(messages=[...])
                         └─ pipeline (identity, thread merge) ─► Polars tables

Rules for the parser:

* Every string is already passed through ``textproc.fix_mojibake`` (count the fixes in
  ``ParsedThread.encoding_fixes``).
* ``type`` follows the priority rule ``call > unsent > system > like > share > video >
  photo > audio > gif > sticker > link > text`` (``store.dataset.TYPE_PRIORITY``), while
  all part counts (``n_photos`` …) are always filled. Empty → ``"unknown"``.
* The parser merges all files of the thread, sorts the messages ascending by
  :meth:`ParsedMessage.sort_key` (stable) and removes exact duplicates by
  :meth:`ParsedMessage.dedupe_key`; the pipeline sorts and dedupes again after
  concatenating all ParsedThreads (one thread may span several sources / zip parts).
* Names stay as names here; person ids are assigned by the merge step.
"""

from collections import Counter
from dataclasses import dataclass, field
from typing import NamedTuple

from instagraph.store.dataset import (
    CallKind,
    Category,
    MediaKind,
    MessageType,
    ShareKind,
    SystemEvent,
)


class ParsedReaction(NamedTuple):
    """One reaction: ``(emoji, actor_name, ts_s)``.

    ``emoji`` is mojibake-fixed and normalized with
    ``textproc.normalize_emoji(e, merge_skin_tones=False)``. ``ts_s`` is epoch SECONDS
    (Instagram's reaction timestamp) or ``None`` when missing.
    """

    emoji: str
    actor_name: str
    ts_s: int | None = None


class ParsedMedia(NamedTuple):
    """One attachment file: ``(kind, uri)``; ``uri`` exactly as in the JSON."""

    kind: MediaKind
    uri: str


@dataclass(slots=True)
class ParsedMessage:
    """One message from a thread file, normalized but not yet identity-resolved."""

    sender_name: str
    """Fixed-encoding sender display name ("" for deleted accounts; see identity rules)."""
    timestamp_ms: int
    """UTC epoch milliseconds (``timestamp_ms`` in the JSON)."""
    type: MessageType = "unknown"
    text: str | None = None
    """Fixed-encoding ``content``; ``None`` for pure system messages / no content."""

    n_photos: int = 0
    n_videos: int = 0
    n_audios: int = 0
    n_gifs: int = 0
    n_stickers: int = 0

    share_link: str | None = None
    share_text: str | None = None
    share_owner: str | None = None
    share_kind: ShareKind | None = None
    links: list[str] = field(default_factory=list)
    """URLs found in ``text`` (``textproc.extract_urls``)."""

    call_duration_s: int | None = None
    call_kind: CallKind | None = None
    call_missed: bool = False

    system_event: SystemEvent | None = None
    system_payload: str | None = None

    reactions: list[ParsedReaction] = field(default_factory=list)
    media: list[ParsedMedia] = field(default_factory=list)

    file_index: int = 1
    """N of the ``message_N.json`` file the message came from."""
    position: int = 0
    """Index of the message within its file's ``messages`` array."""
    is_unsent: bool = False

    def sort_key(self) -> tuple[int, int, int]:
        """Ascending time; ties by file index then REVERSED position (files are newest
        first, so a higher position within a file is older)."""
        return (self.timestamp_ms, -self.file_index, -self.position)

    def dedupe_key(self) -> tuple[str, int, str | None, tuple[str, ...]]:
        """Exact-duplicate identity (§3.4): sender + timestamp + content + attachment URIs."""
        return (
            self.sender_name,
            self.timestamp_ms,
            self.text,
            tuple(sorted(m.uri for m in self.media)),
        )


@dataclass(slots=True)
class ParsedThread:
    """All messages of one thread folder from one source (folder or zip part)."""

    folder: str
    """Thread folder name, e.g. ``jananovakova_123456789``."""
    category: Category
    """From the parent folder: inbox / message_requests → request / archived_threads."""
    title: str | None
    participants: list[str]
    """Fixed-encoding participant names as listed in the file (incl. the owner)."""
    is_still_participant: bool = True
    messages: list[ParsedMessage] = field(default_factory=list)
    unknown_keys: Counter[str] = field(default_factory=Counter)
    """Unknown JSON keys seen → count. Dotted paths, list levels end with ``[]``:
    ``"foo"`` (top level), ``"messages[].foo"``, ``"messages[].reactions[].bar"``
    (see ``raw_models.collect_unknown_keys``)."""
    encoding_fixes: int = 0
    """Number of strings changed by the mojibake fix."""
    source: str = ""
    """Human-readable origin, e.g. ``/path/inbox/x`` or ``part1.zip!messages/inbox/x``."""
    file_count: int = 0
    """Number of ``message_N.json`` files merged into this record."""
    thread_path: str | None = None
    """``thread_path`` field from the JSON, if present."""
