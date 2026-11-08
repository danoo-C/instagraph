"""Import pipeline (spec §8.3): discovery → locales → parse → merge → derive → report.

Entry points
------------
* :func:`run_import` / :func:`run_import_full` — import one or more paths (folders or
  zips) into a :class:`Dataset` (not installed; :mod:`instagraph.importer.jobs` does
  that). ``run_import_full`` also returns the :class:`RawIdentity` needed for
  :func:`rederive`.
* :func:`assemble` — the back half (identity, merge, derive, report) over already
  parsed threads; used by the front half and directly by tests.
* :func:`rederive` — re-run only the steps affected by changed settings.

Memory
------
Workers turn each parsed thread into compact Polars frames (:class:`ThreadChunk`, text
metrics already computed) so the main process never holds per-message Python objects
for the whole export; chunks are concatenated column-wise.

Re-identity after settings changes (raw identity sidecar)
---------------------------------------------------------
The dataset tables only keep resolved ids, so the importer also keeps the *raw*
identity data in ``<data_dir>/import_raw/`` (:func:`save_raw` / :func:`load_raw`):

* ``sources.json`` — per parsed thread source: folder, category, title, participant
  names, ``is_still_participant``, per-sender message counts / last timestamps;
* ``messages_raw.parquet`` — ``message_id, source_idx, raw_sender`` for every message;
* ``reactions_raw.parquet`` — ``message_id, actor_name, emoji, ts`` for every reaction.

``message_id`` never changes after import (ids stay ascending by time even if threads
are merged/split), so media rows and the sidecar remain valid across re-derivations.
Duplicates are removed at import time only (within a merged chat).
"""

import json
import os
import shutil
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from multiprocessing import get_context
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl

from instagraph.errors import ApiError
from instagraph.importer.derive import derive_conversation_columns, derive_text_columns
from instagraph.importer.identity import Identity, SourceInfo, resolve_identity
from instagraph.importer.types import ParsedThread
from instagraph.store.app_settings import AppSettings
from instagraph.store.dataset import (
    CALL_KINDS,
    MESSAGE_TYPES,
    SHARE_KINDS,
    SYSTEM_EVENTS,
    TS,
    Category,
    Dataset,
)

if TYPE_CHECKING:
    from instagraph.importer.discovery import ThreadSource

# --------------------------------------------------------------------------------------
# Progress / errors
# --------------------------------------------------------------------------------------

STAGES: tuple[str, ...] = ("discovery", "locales", "parse", "merge", "derive", "persist")


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """One progress update. ``done``/``total`` count threads in ``parse``, else steps."""

    stage: str
    done: int = 0
    total: int = 0
    message: str = ""
    messages: int = 0
    """Running count of messages parsed so far."""


ProgressFn = Callable[[ProgressEvent], None]


class ImportFailed(ApiError):
    """Import failed with a stable ``code`` (``html_export``, ``unsafe_zip``,
    ``path_not_found``, ``no_threads``, ``parse_failed``, ...)."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(code, message, status)


class ImportCancelled(Exception):
    """The import was cancelled through its ``cancel`` event."""


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise ImportCancelled()


# --------------------------------------------------------------------------------------
# Worker side: ParsedThread → compact frames
# --------------------------------------------------------------------------------------

_MSG_SCHEMA: dict[str, pl.DataType] = {
    "_idx": pl.UInt32(),
    "raw_sender": pl.String(),
    "ts_ms": pl.Int64(),
    "type": pl.Enum(MESSAGE_TYPES),
    "text": pl.String(),
    "n_photos": pl.UInt16(),
    "n_videos": pl.UInt16(),
    "n_audios": pl.UInt16(),
    "n_gifs": pl.UInt16(),
    "n_stickers": pl.UInt16(),
    "share_link": pl.String(),
    "share_text": pl.String(),
    "share_owner": pl.String(),
    "share_kind": pl.Enum(SHARE_KINDS),
    "links": pl.List(pl.String()),
    "call_duration_s": pl.UInt32(),
    "call_kind": pl.Enum(CALL_KINDS),
    "call_missed": pl.Boolean(),
    "system_event": pl.Enum(SYSTEM_EVENTS),
    "system_payload": pl.String(),
    "file_index": pl.Int32(),
    "position": pl.Int32(),
    "media_key": pl.String(),
}
_REACTION_SCHEMA: dict[str, pl.DataType] = {
    "_idx": pl.UInt32(),
    "emoji": pl.String(),
    "actor_name": pl.String(),
    "ts_s": pl.Int64(),
}
_MEDIA_SCHEMA: dict[str, pl.DataType] = {
    "_idx": pl.UInt32(),
    "kind": pl.String(),
    "uri": pl.String(),
}
_FIELDS_DIRECT = (
    "type",
    "text",
    "n_photos",
    "n_videos",
    "n_audios",
    "n_gifs",
    "n_stickers",
    "share_link",
    "share_text",
    "share_owner",
    "share_kind",
    "links",
    "call_kind",
    "call_missed",
    "system_event",
    "system_payload",
    "file_index",
    "position",
)
#: Cap on distinct system-like candidate texts kept per thread.
_SYSTEM_LIKE_PER_THREAD = 200


@dataclass(slots=True)
class ThreadChunk:
    """Picklable worker output for one parsed thread source."""

    folder: str
    category: Category
    title: str | None
    participants: list[str]
    is_still_participant: bool
    source: str
    file_count: int
    messages: pl.DataFrame
    reactions: pl.DataFrame
    media: pl.DataFrame
    unknown_keys: Counter[str] = field(default_factory=Counter)
    encoding_fixes: int = 0
    empty_messages: int = 0
    system_like: Counter[str] = field(default_factory=Counter)
    type_counts: Counter[str] = field(default_factory=Counter)
    parse_stats: Counter[str] = field(default_factory=Counter)


def _looks_like_system_fn() -> Callable[[str, Sequence[str]], bool] | None:
    try:
        from instagraph.importer import system_messages
    except ImportError:  # pragma: no cover - parser agent's module missing
        return None
    fn: Callable[[str, Sequence[str]], bool] = system_messages.looks_like_system
    return fn


def chunk_from_parsed(pt: ParsedThread) -> ThreadChunk:
    """Convert one :class:`ParsedThread` to columnar frames (+ text metrics)."""
    msgs = pt.messages
    cols: dict[str, list[Any]] = {name: [getattr(m, name) for m in msgs] for name in _FIELDS_DIRECT}
    cols["_idx"] = list(range(len(msgs)))
    cols["raw_sender"] = [m.sender_name or "" for m in msgs]
    cols["ts_ms"] = [m.timestamp_ms for m in msgs]
    cols["call_duration_s"] = [
        None if m.call_duration_s is None else max(0, m.call_duration_s) for m in msgs
    ]
    cols["media_key"] = ["\x1f".join(sorted(md.uri for md in m.media)) for m in msgs]
    messages = pl.DataFrame(cols, schema=_MSG_SCHEMA)
    messages = derive_text_columns(messages)

    r_idx: list[int] = []
    r_emoji: list[str] = []
    r_actor: list[str] = []
    r_ts: list[int | None] = []
    m_idx: list[int] = []
    m_kind: list[str] = []
    m_uri: list[str] = []
    for i, m in enumerate(msgs):
        for r in m.reactions:
            r_idx.append(i)
            r_emoji.append(r.emoji)
            r_actor.append(r.actor_name or "")
            r_ts.append(r.ts_s)
        for md in m.media:
            m_idx.append(i)
            m_kind.append(md.kind)
            m_uri.append(md.uri)
    reactions = pl.DataFrame(
        {"_idx": r_idx, "emoji": r_emoji, "actor_name": r_actor, "ts_s": r_ts},
        schema=_REACTION_SCHEMA,
    )
    media = pl.DataFrame({"_idx": m_idx, "kind": m_kind, "uri": m_uri}, schema=_MEDIA_SCHEMA)

    system_like: Counter[str] = Counter()
    looks_like = _looks_like_system_fn()
    if looks_like is not None:
        for m in msgs:
            if (
                m.type == "text"
                and m.text
                and len(m.text) <= 200
                and looks_like(m.text, pt.participants)
            ):
                system_like[m.text] += 1
        if len(system_like) > _SYSTEM_LIKE_PER_THREAD:
            system_like = Counter(dict(system_like.most_common(_SYSTEM_LIKE_PER_THREAD)))
    type_counts: Counter[str] = Counter(str(m.type) for m in msgs)
    return ThreadChunk(
        folder=pt.folder,
        category=pt.category,
        title=pt.title,
        participants=list(pt.participants),
        is_still_participant=pt.is_still_participant,
        source=pt.source,
        file_count=pt.file_count,
        messages=messages,
        reactions=reactions,
        media=media,
        unknown_keys=Counter(pt.unknown_keys),
        encoding_fixes=pt.encoding_fixes,
        empty_messages=type_counts.get("unknown", 0),
        system_like=system_like,
        type_counts=type_counts,
        parse_stats=Counter(getattr(pt, "stats", None) or {}),
    )


def _parse_worker(src: "ThreadSource", locales: Sequence[str]) -> ThreadChunk:
    """Process-pool task: parse one thread source and convert it to frames."""
    from instagraph.importer.parser import parse_thread

    return chunk_from_parsed(parse_thread(src, locales))


def _sample_worker(src: "ThreadSource", limit: int) -> list[str]:
    """Locale sample from the newest and the oldest file of a thread only (bounded cost
    for huge threads, still covers language changes over the years)."""
    import dataclasses

    from instagraph.importer.parser import sample_short_texts

    if len(src.files) > 2:
        src = dataclasses.replace(src, files=(src.files[0], src.files[-1]))
    return sample_short_texts(src, limit)


def _thread_sizes(threads: Sequence["ThreadSource"]) -> list[int]:
    """Total (uncompressed) size of each thread's files; 0 when unknown."""
    import zipfile

    zips: dict[str, zipfile.ZipFile | None] = {}
    sizes: list[int] = []
    try:
        for src in threads:
            total = 0
            for ref in src.files:
                try:
                    if ref.member is None:
                        total += os.stat(ref.path).st_size
                        continue
                    if ref.path not in zips:
                        try:
                            zips[ref.path] = zipfile.ZipFile(ref.path)
                        except (OSError, zipfile.BadZipFile):
                            zips[ref.path] = None
                    zf = zips[ref.path]
                    if zf is not None:
                        total += zf.getinfo(ref.member).file_size
                except (OSError, KeyError):
                    continue
            sizes.append(total)
    finally:
        for zf in zips.values():
            if zf is not None:
                zf.close()
    return sizes


# --------------------------------------------------------------------------------------
# Raw identity sidecar
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class RawIdentity:
    """Raw names per message/reaction + source metadata (see module docstring)."""

    import_id: str
    sources: list[SourceInfo]
    messages: pl.DataFrame
    """``message_id`` (UInt32), ``source_idx`` (UInt32), ``raw_sender`` (String)."""
    reactions: pl.DataFrame
    """``message_id``, ``actor_name``, ``emoji``, ``ts`` (Datetime ms UTC, nullable)."""
    personal_name: str | None = None


RAW_DIRNAME = "import_raw"


def save_raw(data_dir: Path, raw: RawIdentity) -> Path:
    """Write the sidecar to ``<data_dir>/import_raw`` (atomic directory swap)."""
    target = data_dir / RAW_DIRNAME
    data_dir.mkdir(parents=True, exist_ok=True)
    tmp = data_dir / f".{RAW_DIRNAME}.tmp-{uuid.uuid4().hex}"
    tmp.mkdir()
    try:
        raw.messages.write_parquet(tmp / "messages_raw.parquet")
        raw.reactions.write_parquet(tmp / "reactions_raw.parquet")
        sources = []
        for s in raw.sources:
            d = asdict(s)
            d["other_names"] = sorted(s.other_names)
            d["sender_stats"] = {k: list(v) for k, v in s.sender_stats.items()}
            sources.append(d)
        meta = {
            "import_id": raw.import_id,
            "personal_name": raw.personal_name,
            "sources": sources,
        }
        (tmp / "sources.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        old = data_dir / f".{RAW_DIRNAME}.old-{uuid.uuid4().hex}"
        if target.exists():
            target.rename(old)
        tmp.rename(target)
        shutil.rmtree(old, ignore_errors=True)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    return target


def load_raw(data_dir: Path, import_id: str | None = None) -> RawIdentity | None:
    """Load the sidecar; ``None`` if missing, unreadable or from another import."""
    d = data_dir / RAW_DIRNAME
    try:
        meta = json.loads((d / "sources.json").read_text(encoding="utf-8"))
        if import_id is not None and meta.get("import_id") != import_id:
            return None
        sources = [
            SourceInfo(
                source_idx=int(s["source_idx"]),
                folder=s["folder"],
                category=s["category"],
                title=s["title"],
                participants=list(s["participants"]),
                is_still_participant=bool(s["is_still_participant"]),
                source=s.get("source", ""),
                first_ts_ms=s.get("first_ts_ms"),
                last_ts_ms=s.get("last_ts_ms"),
                sender_stats={k: (int(v[0]), int(v[1])) for k, v in s["sender_stats"].items()},
                other_names=set(s.get("other_names", [])),
            )
            for s in meta["sources"]
        ]
        return RawIdentity(
            import_id=meta["import_id"],
            sources=sources,
            messages=pl.read_parquet(d / "messages_raw.parquet"),
            reactions=pl.read_parquet(d / "reactions_raw.parquet"),
            personal_name=meta.get("personal_name"),
        )
    except (OSError, ValueError, KeyError, TypeError, pl.exceptions.PolarsError):
        return None


# --------------------------------------------------------------------------------------
# Assembly (identity, merge, dedupe, ids, derive)
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class ImportResult:
    dataset: Dataset
    raw: RawIdentity


def derive_settings_snapshot(settings: AppSettings) -> dict[str, Any]:
    """The settings that influence derived data (stored in the import report)."""
    return settings.model_dump(
        mode="json",
        include={
            "owner_person_id",
            "owner_name",
            "people_merges",
            "thread_merges",
            "thread_splits",
            "session_gap_min",
            "reply_window_h",
        },
    )


def _source_infos(chunks: Sequence[ThreadChunk]) -> list[SourceInfo]:
    infos: list[SourceInfo] = []
    for i, ch in enumerate(chunks):
        stats: dict[str, tuple[int, int]] = {}
        first = last = None
        if ch.messages.height:
            agg = ch.messages.group_by("raw_sender").agg(
                pl.len().alias("n"), pl.col("ts_ms").max().alias("last")
            )
            stats = {
                str(name): (int(n), int(lst))
                for name, n, lst in agg.select("raw_sender", "n", "last").iter_rows()
            }
            first = int(ch.messages["ts_ms"].min())  # type: ignore[arg-type]
            last = int(ch.messages["ts_ms"].max())  # type: ignore[arg-type]
        infos.append(
            SourceInfo(
                source_idx=i,
                folder=ch.folder,
                category=ch.category,
                title=ch.title,
                participants=list(ch.participants),
                is_still_participant=ch.is_still_participant,
                source=ch.source,
                first_ts_ms=first,
                last_ts_ms=last,
                sender_stats=stats,
                other_names=set(ch.reactions["actor_name"].unique().to_list()),
            )
        )
    return infos


def _identity_for(
    sources: Sequence[SourceInfo],
    settings: AppSettings,
    personal_name: str | None,
    owner_override: str | None = None,
) -> Identity:
    return resolve_identity(
        sources,
        owner_name=owner_override if owner_override is not None else settings.owner_name,
        personal_name=personal_name,
        people_merges=settings.people_merges,
        thread_merges=settings.thread_merges,
        thread_splits=settings.thread_splits,
    )


def _name_map(identity: Identity, pairs: pl.DataFrame, name_col: str) -> pl.DataFrame:
    """``(thread_id, <name_col>)`` pairs → same frame with a ``pid`` column."""
    pids = [
        identity.person_for(name, int(tid))
        for tid, name in pairs.select("thread_id", name_col).iter_rows()
    ]
    return pairs.with_columns(pl.Series("pid", pids, dtype=pl.UInt32))


def _source_thread_frame(identity: Identity) -> pl.DataFrame:
    items = sorted(identity.source_thread.items())
    return pl.DataFrame(
        {"source_idx": [s for s, _ in items], "thread_id": [t for _, t in items]},
        schema={"source_idx": pl.UInt32, "thread_id": pl.UInt32},
    )


def _people_frame(identity: Identity) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "person_id": [p.person_id for p in identity.people],
            "display_name": [p.display_name for p in identity.people],
            "aliases": [p.aliases for p in identity.people],
            "is_owner": [p.is_owner for p in identity.people],
            "is_deleted_account": [p.is_deleted_account for p in identity.people],
        },
        schema={
            "person_id": pl.UInt32,
            "display_name": pl.String,
            "aliases": pl.List(pl.String),
            "is_owner": pl.Boolean,
            "is_deleted_account": pl.Boolean,
        },
    )


def _threads_frame(identity: Identity, messages: pl.DataFrame) -> pl.DataFrame:
    base = pl.DataFrame(
        {
            "thread_id": [t.thread_id for t in identity.threads],
            "title": [t.title for t in identity.threads],
            "folder": [t.folder for t in identity.threads],
            "category": [t.category for t in identity.threads],
            "is_group": [t.is_group for t in identity.threads],
            "participants": [t.participants for t in identity.threads],
            "owner_still_participant": [t.owner_still_participant for t in identity.threads],
        },
        schema={
            "thread_id": pl.UInt32,
            "title": pl.String,
            "folder": pl.String,
            "category": pl.String,
            "is_group": pl.Boolean,
            "participants": pl.List(pl.UInt32),
            "owner_still_participant": pl.Boolean,
        },
    )
    stats = messages.group_by("thread_id").agg(
        pl.col("ts").min().alias("first_ts"),
        pl.col("ts").max().alias("last_ts"),
        pl.len().cast(pl.UInt32).alias("message_count"),
    )
    return base.join(stats, on="thread_id", how="left", maintain_order="left").with_columns(
        pl.col("message_count").fill_null(0)
    )


def _timed(durations: dict[str, float], stage: str, start: float) -> None:
    durations[stage] = durations.get(stage, 0.0) + (time.perf_counter() - start) * 1000


def assemble(
    chunks: Sequence[ThreadChunk],
    *,
    settings: AppSettings,
    personal_name: str | None = None,
    media_resolver: Callable[[str], str | None] | None = None,
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
    durations: dict[str, float] | None = None,
) -> tuple[Dataset, RawIdentity, dict[str, Any]]:
    """Identity, merge, dedupe, ids, derive. Returns (dataset, raw, partial report).

    ``media_resolver(uri)`` returns the media file's ``source`` description, or ``None``
    when the file is not in the export.
    """
    durations = durations if durations is not None else {}
    emit = progress or (lambda _e: None)
    t0 = time.perf_counter()
    emit(ProgressEvent("merge", 0, 4, "Resolving people and threads"))
    sources = _source_infos(chunks)
    identity = _identity_for(sources, settings, personal_name)
    _check_cancel(cancel)

    # ---- messages: concat, thread ids, sort, dedupe, message ids ---------------------
    msg_frames = [
        ch.messages.with_columns(pl.lit(i, pl.UInt32).alias("source_idx"))
        for i, ch in enumerate(chunks)
        if ch.messages.height
    ]
    if msg_frames:
        all_msgs = pl.concat(msg_frames, how="vertical", rechunk=False)
    else:
        all_msgs = derive_text_columns(pl.DataFrame(schema=_MSG_SCHEMA)).with_columns(
            pl.lit(None, pl.UInt32).alias("source_idx")
        )
    del msg_frames
    for ch in chunks:  # the concatenated frame now owns the buffers
        ch.messages = ch.messages.clear()
    st = sorted(identity.source_thread.items())
    all_msgs = all_msgs.with_columns(
        pl.col("source_idx")
        .replace_strict([k for k, _ in st], [v for _, v in st], return_dtype=pl.UInt32)
        .alias("thread_id")
    )
    # Order + dedupe on a narrow key frame, then gather the wide frame ONCE (memory).
    keys = all_msgs.select(
        pl.int_range(pl.len(), dtype=pl.UInt32).alias("_row"),
        "thread_id",
        "ts_ms",
        "source_idx",
        "file_index",
        "position",
        "raw_sender",
        pl.struct("text", "media_key").hash(seed=7).alias("_content"),
    ).sort(
        ["thread_id", "ts_ms", "source_idx", "file_index", "position"],
        descending=[False, False, False, True, True],
        maintain_order=True,
    )
    before = keys.height
    keys = keys.unique(
        subset=["thread_id", "raw_sender", "ts_ms", "_content"],
        keep="first",
        maintain_order=True,
    )
    duplicates_removed = before - keys.height
    order = keys.with_row_index("_seq").sort(["ts_ms", "_seq"])["_row"]
    del keys
    emit(ProgressEvent("merge", 1, 4, "Mapping people"))
    _check_cancel(cancel)

    sender_pairs = _name_map(
        identity, all_msgs.select("thread_id", "raw_sender").unique(), "raw_sender"
    )
    sender_ids = all_msgs.select("thread_id", "raw_sender").join(
        sender_pairs, on=["thread_id", "raw_sender"], how="left", maintain_order="left"
    )["pid"]
    # Narrow frame (raw identity + chunk keys) and the wide final frame, each gathered
    # once into message_id order.
    narrow = all_msgs.select("source_idx", "_idx", "raw_sender", "thread_id")[order]
    all_msgs = all_msgs.with_columns(sender_ids.alias("sender_id")).drop(
        "_idx", "raw_sender", "file_index", "position", "media_key", "source_idx"
    )
    del sender_ids
    all_msgs = all_msgs[order].with_columns(
        pl.int_range(pl.len(), dtype=pl.UInt32).alias("message_id")
    )
    narrow = narrow.with_columns(pl.int_range(pl.len(), dtype=pl.UInt32).alias("message_id"))
    del order
    key_map = narrow.select("source_idx", "_idx", "message_id", "thread_id")

    # ---- reactions --------------------------------------------------------------------
    emit(ProgressEvent("merge", 2, 4, "Reactions and media"))
    react_frames = [
        ch.reactions.with_columns(pl.lit(i, pl.UInt32).alias("source_idx"))
        for i, ch in enumerate(chunks)
        if ch.reactions.height
    ]
    if react_frames:
        raw_reactions = (
            pl.concat(react_frames, how="vertical")
            .join(key_map, on=["source_idx", "_idx"], how="inner")
            .sort("message_id", maintain_order=True)
            .select(
                "message_id",
                "actor_name",
                "emoji",
                pl.when(pl.col("ts_s").is_not_null())
                .then((pl.col("ts_s") * 1000).cast(pl.Datetime("ms")).dt.replace_time_zone("UTC"))
                .alias("ts"),
            )
        )
    else:
        raw_reactions = pl.DataFrame(
            schema={
                "message_id": pl.UInt32,
                "actor_name": pl.String,
                "emoji": pl.String,
                "ts": TS,
            }
        )
    reactions = _resolve_reactions(
        identity, raw_reactions, all_msgs.select("message_id", "thread_id")
    )

    # ---- media ------------------------------------------------------------------------
    media_frames = [
        ch.media.with_columns(pl.lit(i, pl.UInt32).alias("source_idx"))
        for i, ch in enumerate(chunks)
        if ch.media.height
    ]
    if media_frames:
        media = (
            pl.concat(media_frames, how="vertical")
            .join(key_map, on=["source_idx", "_idx"], how="inner")
            .sort("message_id", maintain_order=True)
            .with_row_index("media_id")
        )
        uris = media["uri"].unique().to_list()
        resolved = {u: (media_resolver(u) if media_resolver else None) for u in uris}
        lookup = pl.DataFrame(
            {"uri": list(resolved), "source": list(resolved.values())},
            schema={"uri": pl.String, "source": pl.String},
        )
        media = media.join(lookup, on="uri", how="left").select(
            pl.col("media_id").cast(pl.UInt32),
            "message_id",
            "kind",
            "uri",
            pl.col("source").fill_null(""),
            pl.col("source").is_not_null().alias("exists"),
        )
        media = media.sort("media_id")
    else:
        media = pl.DataFrame(
            schema={
                "media_id": pl.UInt32,
                "message_id": pl.UInt32,
                "kind": pl.String,
                "uri": pl.String,
                "source": pl.String,
                "exists": pl.Boolean,
            }
        )
    del key_map
    _timed(durations, "merge", t0)

    # ---- derive -----------------------------------------------------------------------
    t1 = time.perf_counter()
    emit(ProgressEvent("derive", 0, 1, "Sessions, turns and replies"))
    _check_cancel(cancel)
    raw_messages = narrow.select("message_id", "source_idx", "raw_sender")
    del narrow
    messages = all_msgs.with_columns(
        pl.col("ts_ms").cast(pl.Datetime("ms")).dt.replace_time_zone("UTC").alias("ts")
    ).drop("ts_ms")
    del all_msgs
    messages = derive_conversation_columns(
        messages,
        session_gap_ms=settings.session_gap_ms,
        reply_window_ms=settings.reply_window_ms,
    )
    threads = _threads_frame(identity, messages)
    people = _people_frame(identity)
    _timed(durations, "derive", t1)
    emit(ProgressEvent("derive", 1, 1, "Derived fields ready"))

    import_id = uuid.uuid4().hex
    raw = RawIdentity(
        import_id=import_id,
        sources=sources,
        messages=raw_messages,
        reactions=raw_reactions,
        personal_name=personal_name,
    )
    ts = messages["ts"]
    unknown: Counter[str] = Counter()
    system_like: Counter[str] = Counter()
    type_counts: Counter[str] = Counter()
    parse_stats: Counter[str] = Counter()
    for ch in chunks:
        parse_stats.update(ch.parse_stats)
        unknown.update(ch.unknown_keys)
        system_like.update(ch.system_like)
        type_counts.update(ch.type_counts)
    report: dict[str, Any] = {
        "import_id": import_id,
        "imported_at": datetime.now(UTC).isoformat(),
        "thread_sources": len(chunks),
        "threads": threads.height,
        "messages": messages.height,
        "people": people.height,
        "reactions": reactions.height,
        "media": media.height,
        "media_found": int(media["exists"].sum()) if media.height else 0,
        "owner": _owner_block(identity, personal_name),
        "encoding_fixes": sum(ch.encoding_fixes for ch in chunks),
        "duplicates_removed": duplicates_removed,
        "threads_merged": identity.threads_merged,
        "unknown_keys": [{"key": k, "count": c} for k, c in unknown.most_common()],
        "unmatched_system_like": [{"text": t, "count": c} for t, c in system_like.most_common(50)],
        "empty_messages": sum(ch.empty_messages for ch in chunks),
        "message_types": dict(type_counts.most_common()),
        "parse_stats": dict(sorted(parse_stats.items())),
        "earliest": ts.min().isoformat() if messages.height else None,  # type: ignore[union-attr]
        "latest": ts.max().isoformat() if messages.height else None,  # type: ignore[union-attr]
        "derive_settings": derive_settings_snapshot(settings),
    }
    dataset = Dataset.from_frames(
        people=people,
        threads=threads,
        messages=messages,
        reactions=reactions,
        media=media,
        import_report=report,
    )
    return dataset, raw, report


def _owner_block(identity: Identity, personal_name: str | None) -> dict[str, Any]:
    auto = identity.auto_owner_id
    return {
        "name": identity.owner_name,
        "person_id": identity.owner_id,
        "method": identity.owner_method,
        "auto_name": identity.people[auto].display_name if auto is not None else None,
        "auto_method": identity.auto_owner_method,
        "personal_information_name": personal_name,
    }


def _resolve_reactions(
    identity: Identity, raw_reactions: pl.DataFrame, msg_threads: pl.DataFrame
) -> pl.DataFrame:
    """Raw reactions (``message_id, actor_name, emoji, ts``) → reactions table."""
    if raw_reactions.height == 0:
        return pl.DataFrame(
            schema={"message_id": pl.UInt32, "actor_id": pl.UInt32, "emoji": pl.String, "ts": TS}
        )
    with_thread = raw_reactions.join(
        msg_threads, on="message_id", how="left", maintain_order="left"
    )
    actor_pairs = _name_map(
        identity, with_thread.select("thread_id", "actor_name").unique(), "actor_name"
    )
    return (
        with_thread.join(
            actor_pairs, on=["thread_id", "actor_name"], how="left", maintain_order="left"
        )
        .filter(pl.col("pid").is_not_null())
        .select("message_id", pl.col("pid").alias("actor_id"), "emoji", "ts")
    )


# --------------------------------------------------------------------------------------
# Front half: discovery, locales, parse
# --------------------------------------------------------------------------------------


def _run_tasks[T](
    fn: Callable[..., T],
    items: Sequence[Any],
    extra: tuple[Any, ...],
    *,
    executor: ProcessPoolExecutor | None,
    cancel: threading.Event | None,
    on_done: Callable[[int, T], None],
    on_error: Callable[[int, BaseException], None],
) -> None:
    """Run ``fn(item, *extra)`` for every item (in the pool if given), in any order."""
    if executor is None:
        for i, item in enumerate(items):
            _check_cancel(cancel)
            try:
                result = fn(item, *extra)
            except Exception as exc:
                on_error(i, exc)
                continue
            on_done(i, result)
        return
    futures: dict[Future[T], int] = {
        executor.submit(fn, item, *extra): i for i, item in enumerate(items)
    }
    pending: set[Future[T]] = set(futures)
    try:
        while pending:
            _check_cancel(cancel)
            done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
            for fut in done:
                error = fut.exception()
                if error is not None:
                    on_error(futures[fut], error)
                else:
                    on_done(futures[fut], fut.result())
    except BaseException:
        for fut in pending:
            fut.cancel()
        raise


def _make_executor(workers: int, n_items: int) -> ProcessPoolExecutor | None:
    if workers <= 1 or n_items < 4:
        return None
    return ProcessPoolExecutor(max_workers=min(workers, n_items), mp_context=get_context("spawn"))


LOCALE_SAMPLE_PER_THREAD = 500


def run_import(
    sources: list[Path],
    *,
    settings: AppSettings,
    progress: ProgressFn,
    cancel: threading.Event,
    workers: int,
) -> Dataset:
    """Import ``sources`` (folders and/or zips) and return the (uninstalled) dataset."""
    return run_import_full(
        sources, settings=settings, progress=progress, cancel=cancel, workers=workers
    ).dataset


def run_import_full(
    sources: Sequence[Path],
    *,
    settings: AppSettings,
    progress: ProgressFn,
    cancel: threading.Event | None = None,
    workers: int = 1,
) -> ImportResult:
    """Like :func:`run_import` but also returns the raw identity sidecar data."""
    from instagraph.importer import discovery, system_messages

    durations: dict[str, float] = {}
    started = time.perf_counter()

    # ---- discovery ----------------------------------------------------------------------
    t = time.perf_counter()
    progress(ProgressEvent("discovery", 0, 0, "Looking for message files"))
    missing = [str(p) for p in sources if not Path(p).exists()]
    if missing:
        raise ImportFailed("path_not_found", f"Path not found: {missing[0]}")
    try:
        disc = discovery.discover([Path(p) for p in sources])
    except discovery.HtmlExportError as exc:
        raise ImportFailed("html_export", str(exc)) from exc
    except discovery.ImportSafetyError as exc:
        raise ImportFailed("unsafe_zip", str(exc)) from exc
    except FileNotFoundError as exc:
        raise ImportFailed("path_not_found", str(exc)) from exc
    except PermissionError as exc:
        raise ImportFailed("path_not_readable", str(exc)) from exc
    except discovery.DiscoveryError as exc:
        raise ImportFailed("invalid_source", str(exc)) from exc
    threads = list(disc.threads)
    # Submit the biggest threads first (a huge chat parsed last would leave the pool
    # idle); results keep discovery order.
    sizes = _thread_sizes(threads)
    order = sorted(range(len(threads)), key=lambda i: -sizes[i])
    by_size = [threads[i] for i in order]
    if not threads:
        raise ImportFailed(
            "no_threads",
            "No Instagram messages were found. Expected files like "
            "messages/inbox/<chat>/message_1.json inside the folder or zip "
            "(make sure the export was requested in JSON format).",
        )
    personal_name = (
        discovery.read_owner_name(disc.personal_info) if disc.personal_info is not None else None
    )
    progress(
        ProgressEvent(
            "discovery",
            len(threads),
            len(threads),
            f"Found {disc.file_count} files, {len(threads)} chat folders",
        )
    )
    _timed(durations, "discovery", t)
    _check_cancel(cancel)

    executor = _make_executor(workers, len(threads))
    try:
        # ---- locales ------------------------------------------------------------------
        t = time.perf_counter()
        progress(ProgressEvent("locales", 0, len(threads), "Detecting system-message languages"))
        samples: list[str] = []

        def sample_done(_i: int, texts: list[str]) -> None:
            samples.extend(texts)

        _run_tasks(
            _sample_worker,
            by_size,
            (LOCALE_SAMPLE_PER_THREAD,),
            executor=executor,
            cancel=cancel,
            on_done=sample_done,
            on_error=lambda _i, _e: None,
        )
        locale_matches = system_messages.detect_locale_matches(samples)
        counts = {code: len(ids) for code, ids in locale_matches.items()}
        detected = system_messages.enabled_locales(counts, len(samples), locale_matches)
        if settings.enabled_locales is not None:
            locales = list(settings.enabled_locales)
            mode = "settings"
        else:
            locales = list(detected) or ["en"]
            mode = "auto"
        _timed(durations, "locales", t)
        progress(ProgressEvent("locales", len(threads), len(threads), ", ".join(locales)))

        # ---- parse ------------------------------------------------------------------------
        t = time.perf_counter()
        results: list[ThreadChunk | None] = [None] * len(threads)
        failures: list[dict[str, str]] = []
        state = {"done": 0, "messages": 0}

        def parse_done(k: int, chunk: ThreadChunk) -> None:
            results[order[k]] = chunk
            state["done"] += 1
            state["messages"] += chunk.messages.height
            progress(
                ProgressEvent(
                    "parse",
                    state["done"],
                    len(threads),
                    f"{state['done']} of {len(threads)} chats parsed",
                    state["messages"],
                )
            )

        def parse_error(k: int, exc: BaseException) -> None:
            if isinstance(exc, discovery.ImportSafetyError):
                raise ImportFailed("unsafe_zip", str(exc)) from exc
            state["done"] += 1
            failures.append({"folder": by_size[k].folder, "error": f"{type(exc).__name__}: {exc}"})

        progress(ProgressEvent("parse", 0, len(threads), "Parsing chats"))
        _run_tasks(
            _parse_worker,
            by_size,
            (tuple(locales),),
            executor=executor,
            cancel=cancel,
            on_done=parse_done,
            on_error=parse_error,
        )
        _timed(durations, "parse", t)
    finally:
        if executor is not None:
            executor.shutdown(wait=cancel is None or not cancel.is_set(), cancel_futures=True)

    chunks = [c for c in results if c is not None]
    if not chunks:
        detail = failures[0]["error"] if failures else "no chats"
        raise ImportFailed("parse_failed", f"No chat could be parsed ({detail}).")
    _check_cancel(cancel)

    def resolve_media(uri: str) -> str | None:
        ref = discovery.match_media(uri, disc.media_files)
        return ref.describe() if ref is not None else None

    dataset, raw, report = assemble(
        chunks,
        settings=settings,
        personal_name=personal_name,
        media_resolver=resolve_media,
        progress=progress,
        cancel=cancel,
        durations=durations,
    )
    report.update(
        {
            "sources": [str(p) for p in sources],
            "files": disc.file_count,
            "zip_files": list(disc.zip_files),
            "layouts": sorted(disc.layouts),
            "locales": {
                "detected": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
                "auto_enabled": list(detected),
                "enabled": locales,
                "mode": mode,
                "sample_size": len(samples),
            },
            "failed_threads": failures,
            "durations_ms": {k: round(v, 1) for k, v in durations.items()},
            "total_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    )
    dataset.import_report = report
    return ImportResult(dataset=dataset, raw=raw)


# --------------------------------------------------------------------------------------
# Re-derivation after settings changes
# --------------------------------------------------------------------------------------

_IDENTITY_KEYS = ("people_merges", "thread_merges", "thread_splits")
_CONVERSATION_KEYS = ("session_gap_min", "reply_window_h")
_OWNER_KEYS = ("owner_name", "owner_person_id")


def affected_steps(dataset: Dataset, settings: AppSettings) -> set[str]:
    """Which of ``identity`` / ``conversation`` / ``owner`` must be recomputed."""
    prev: dict[str, Any] = dataset.import_report.get("derive_settings") or {}
    new = derive_settings_snapshot(settings)
    steps: set[str] = set()
    if any(prev.get(k) != new[k] for k in _IDENTITY_KEYS):
        steps |= {"identity", "conversation", "owner"}
    if any(prev.get(k) != new[k] for k in _CONVERSATION_KEYS):
        steps.add("conversation")
    if any(prev.get(k) != new[k] for k in _OWNER_KEYS):
        steps.add("owner")
    return steps


def _owner_override_name(dataset: Dataset, settings: AppSettings) -> str | None:
    """Owner override as a NAME (``owner_person_id`` refers to the current ids)."""
    if settings.owner_person_id is not None:
        names = dataset.person_names()
        if settings.owner_person_id in names:
            return names[settings.owner_person_id]
    return settings.owner_name


def rederive(
    dataset: Dataset,
    settings: AppSettings,
    raw: RawIdentity | None = None,
    *,
    data_dir: Path | None = None,
) -> Dataset:
    """Return a new dataset reflecting ``settings`` (owner, merges, session gap,
    reply window), recomputing only the affected steps.

    Identity changes (people / thread merges & splits) need the raw sidecar: ``raw``
    or, when omitted, :func:`load_raw` from ``data_dir`` (default: the store's data
    dir). Without it an ``ImportFailed("reimport_required", 409)`` is raised.
    ``message_id`` and media rows never change. After an identity change person ids are
    reassigned; the owner override is carried over by name (``owner_person_id`` is
    translated to the name it pointed to), so callers should store the returned
    dataset's owner (``Dataset.owner_id()``) back into settings if they keep ids.
    """
    steps = affected_steps(dataset, settings)
    report = dict(dataset.import_report)
    report["derive_settings"] = derive_settings_snapshot(settings)
    people = dataset.people.collect()
    threads = dataset.threads.collect()
    messages = dataset.messages.collect()
    reactions = dataset.reactions.collect()
    media = dataset.media.collect()
    override = _owner_override_name(dataset, settings)

    if "identity" in steps:
        if raw is None:
            if data_dir is None:
                from instagraph.store.dataset import get_store

                data_dir = get_store().data_dir
            raw = load_raw(data_dir, report.get("import_id"))
        if raw is None:
            raise ImportFailed(
                "reimport_required",
                "The raw import data needed to change merges is missing; re-import the export.",
                409,
            )
        identity = _identity_for(raw.sources, settings, raw.personal_name, override or "")
        mapping = raw.messages.join(_source_thread_frame(identity), on="source_idx", how="left")
        sender_pairs = _name_map(
            identity, mapping.select("thread_id", "raw_sender").unique(), "raw_sender"
        )
        mapping = mapping.join(sender_pairs, on=["thread_id", "raw_sender"], how="left").select(
            "message_id", pl.col("thread_id").alias("_tid"), pl.col("pid").alias("_sid")
        )
        aligned = messages.select("message_id", "thread_id", "sender_id").join(
            mapping, on="message_id", how="left", maintain_order="left"
        )
        messages = messages.with_columns(
            pl.coalesce(aligned["_tid"], aligned["thread_id"]).alias("thread_id"),
            pl.coalesce(aligned["_sid"], aligned["sender_id"]).alias("sender_id"),
        )
        del aligned
        reactions = _resolve_reactions(
            identity, raw.reactions, messages.select("message_id", "thread_id")
        )
        people = _people_frame(identity)
        threads = _threads_frame(identity, messages)
        report.update(
            {
                "threads": threads.height,
                "people": people.height,
                "reactions": reactions.height,
                "threads_merged": identity.threads_merged,
                "owner": _owner_block(identity, raw.personal_name),
            }
        )
    elif "owner" in steps:
        people, owner_block = _apply_owner(people, override, report.get("owner") or {})
        report["owner"] = owner_block

    if "conversation" in steps:
        messages = derive_conversation_columns(
            messages,
            session_gap_ms=settings.session_gap_ms,
            reply_window_ms=settings.reply_window_ms,
        )
    report["rederived_steps"] = sorted(steps)
    return Dataset.from_frames(
        people=people,
        threads=threads,
        messages=messages,
        reactions=reactions,
        media=media,
        import_report=report,
        version=dataset.version,
    )


def _find_person(people: pl.DataFrame, name: str | None) -> int | None:
    if not name:
        return None
    key = name.strip().casefold()
    exact: int | None = None
    loose: int | None = None
    for pid, display, aliases, deleted in people.select(
        "person_id", "display_name", "aliases", "is_deleted_account"
    ).iter_rows():
        if deleted:
            continue
        names = [display, *(aliases or [])]
        if name in names and exact is None:
            exact = int(pid)
        if loose is None and any(n.strip().casefold() == key for n in names):
            loose = int(pid)
    return exact if exact is not None else loose


def _apply_owner(
    people: pl.DataFrame, override: str | None, prev_block: dict[str, Any]
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Owner-only change: flip ``is_owner`` without re-running identity."""
    owner = _find_person(people, override)
    method = "settings"
    if owner is None:
        owner = _find_person(people, prev_block.get("auto_name"))
        method = str(prev_block.get("auto_method") or "none")
    block = dict(prev_block)
    block["person_id"] = owner
    block["method"] = method if owner is not None else "none"
    names = dict(zip(people["person_id"].to_list(), people["display_name"].to_list(), strict=True))
    block["name"] = names.get(owner) if owner is not None else None
    people = people.with_columns(
        (pl.col("person_id") == owner).fill_null(False).alias("is_owner")
        if owner is not None
        else pl.lit(False).alias("is_owner")
    )
    return people, block


__all__ = [
    "STAGES",
    "ImportCancelled",
    "ImportFailed",
    "ImportResult",
    "ProgressEvent",
    "RawIdentity",
    "ThreadChunk",
    "affected_steps",
    "assemble",
    "chunk_from_parsed",
    "derive_settings_snapshot",
    "load_raw",
    "rederive",
    "run_import",
    "run_import_full",
    "save_raw",
]
