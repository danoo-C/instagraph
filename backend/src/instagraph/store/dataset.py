"""Normalized data model (spec §4.1) and the process-wide dataset store.

Tables
------
Five Polars tables; the column names and dtypes below are THE contract between the
importer (producer) and analysis/routes (consumers). Use :func:`conform` to coerce any
DataFrame to its schema (adds missing columns with defaults, casts, orders columns).

Conventions
-----------
* All ids are ``UInt32``. ``message_id`` is stable and ascending by ``ts`` (ties keep
  file order), so sorting by ``message_id`` == chronological order.
* All timestamps are ``Datetime("ms", "UTC")``. Convert to the display zone only via
  :func:`instagraph.api.filters.filtered_messages` (adds ``local_*`` columns).
* ``messages.emojis`` and ``reactions.emoji`` hold emoji grapheme clusters with U+FE0F
  removed but skin tones KEPT (``textproc.normalize_emoji(e, merge_skin_tones=False)``).
  Skin-tone / gender merging is a query-time setting (``textproc.normalize_emoji_expr``).
* ``session_id``, ``turn_id``, ``is_session_start`` and ``reply_latency_ms`` are computed
  only over *counted* messages (``type`` not in ``{system, like}``, §6.1). For the other
  messages ``session_id``/``turn_id``/``reply_latency_ms`` are null and
  ``is_session_start`` is False. ``session_id`` and ``turn_id`` are unique across the
  whole dataset (not per thread). ``reply_latency_ms`` is set on the first message of a
  turn when the previous turn (different sender, same thread) ended ≤ reply window
  earlier; it is the gap to the previous turn's LAST message (§6.4).
* ``text`` is null for pure system messages; ``word_count``/``char_count``/``emojis``
  are 0/0/[] for ``system`` and ``like`` messages.

On disk (``<data_dir>/dataset/``): ``people.parquet``, ``threads.parquet``,
``messages.parquet``, ``reactions.parquet``, ``media.parquet``, ``import_report.json``,
``meta.json``. The dataset version lives in ``<data_dir>/version.json`` so it keeps
increasing across "clear all data".
"""

import contextlib
import json
import shutil
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, get_args

import polars as pl

from instagraph.errors import NoDataError
from instagraph.store.cache import response_cache

# --------------------------------------------------------------------------------------
# Enum values (single source of truth: the Literal types; tuples derived from them)
# --------------------------------------------------------------------------------------

MessageType = Literal[
    "text",
    "photo",
    "video",
    "audio",
    "gif",
    "sticker",
    "share",
    "link",
    "call",
    "like",
    "unsent",
    "system",
    "unknown",
]
Category = Literal["inbox", "request", "archived"]
ShareKind = Literal["reel", "post", "story", "profile", "external", "other"]
CallKind = Literal["audio", "video", "unknown"]
SystemEvent = Literal[
    "rename",  # group renamed; payload = new name
    "join",  # member added / joined; payload = member name
    "leave",  # member left; payload = member name
    "removed",  # member removed; payload = member name
    "photo_change",  # group photo changed
    "theme_change",  # chat theme changed; payload = theme name if known
    "emoji_change",  # quick-reaction emoji changed; payload = emoji
    "nickname",  # nickname set/cleared; payload = "<target>\t<nickname>"
    "admin",  # admin added/removed
    "call_event",  # textual call notice that is not itself a call record
    "attachment",  # "X sent an attachment." placeholder
    "reaction_text",  # old-style "Reacted 😂 to your message" (also emitted as reaction)
    "story_reply",  # "Replied to your story"
    "story_mention",  # "Mentioned you in their story"
    "other",  # recognised system text without a finer class
]
MediaKind = Literal["photo", "video", "audio", "gif", "sticker"]

MESSAGE_TYPES: tuple[str, ...] = get_args(MessageType)
CATEGORIES: tuple[str, ...] = get_args(Category)
SHARE_KINDS: tuple[str, ...] = get_args(ShareKind)
CALL_KINDS: tuple[str, ...] = get_args(CallKind)
SYSTEM_EVENTS: tuple[str, ...] = get_args(SystemEvent)
MEDIA_KINDS: tuple[str, ...] = get_args(MediaKind)

#: Type priority when a message has several parts (§4.1), highest first.
TYPE_PRIORITY: tuple[str, ...] = (
    "call",
    "unsent",
    "system",
    "like",
    "share",
    "video",
    "photo",
    "audio",
    "gif",
    "sticker",
    "link",
    "text",
)
#: Messages with these types are NOT "messages" for counting purposes (§6.1).
NON_COUNTED_TYPES: tuple[str, ...] = ("system", "like")

# --------------------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------------------

TS = pl.Datetime("ms", "UTC")

PEOPLE_SCHEMA: dict[str, pl.DataType] = {
    "person_id": pl.UInt32(),
    "display_name": pl.String(),
    "aliases": pl.List(pl.String()),
    "is_owner": pl.Boolean(),
    "is_deleted_account": pl.Boolean(),
}

THREADS_SCHEMA: dict[str, pl.DataType] = {
    "thread_id": pl.UInt32(),
    "title": pl.String(),
    "folder": pl.String(),
    "category": pl.Enum(CATEGORIES),
    "is_group": pl.Boolean(),
    "participants": pl.List(pl.UInt32()),
    "owner_still_participant": pl.Boolean(),
    "first_ts": TS,
    "last_ts": TS,
    "message_count": pl.UInt32(),
}

MESSAGES_SCHEMA: dict[str, pl.DataType] = {
    "message_id": pl.UInt32(),
    "thread_id": pl.UInt32(),
    "sender_id": pl.UInt32(),
    "ts": TS,
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
    # derived at import
    "word_count": pl.UInt32(),
    "char_count": pl.UInt32(),
    "emojis": pl.List(pl.String()),
    "session_id": pl.UInt32(),
    "is_session_start": pl.Boolean(),
    "turn_id": pl.UInt32(),
    "reply_latency_ms": pl.Int64(),
}

REACTIONS_SCHEMA: dict[str, pl.DataType] = {
    "message_id": pl.UInt32(),
    "actor_id": pl.UInt32(),
    "emoji": pl.String(),
    "ts": TS,  # nullable
}

MEDIA_SCHEMA: dict[str, pl.DataType] = {
    "media_id": pl.UInt32(),
    "message_id": pl.UInt32(),
    "kind": pl.Enum(MEDIA_KINDS),
    "uri": pl.String(),
    "source": pl.String(),  # folder path, or "<zip path>!<member name>"
    "exists": pl.Boolean(),
}

TABLE_SCHEMAS: dict[str, dict[str, pl.DataType]] = {
    "people": PEOPLE_SCHEMA,
    "threads": THREADS_SCHEMA,
    "messages": MESSAGES_SCHEMA,
    "reactions": REACTIONS_SCHEMA,
    "media": MEDIA_SCHEMA,
}

#: Defaults used by :func:`conform` for missing non-nullable columns.
_DEFAULTS: dict[str, Any] = {
    "is_owner": False,
    "is_deleted_account": False,
    "is_group": False,
    "owner_still_participant": True,
    "message_count": 0,
    "category": "inbox",
    "type": "unknown",
    "n_photos": 0,
    "n_videos": 0,
    "n_audios": 0,
    "n_gifs": 0,
    "n_stickers": 0,
    "call_missed": False,
    "word_count": 0,
    "char_count": 0,
    "is_session_start": False,
    "exists": False,
}
_LIST_COLUMNS = {"aliases", "participants", "links", "emojis"}


def conform(df: pl.DataFrame, schema: dict[str, pl.DataType]) -> pl.DataFrame:
    """Return ``df`` with exactly the columns of ``schema``, in order and cast.

    Missing columns are filled with defaults (``False``/``0``/``[]``/``"inbox"``/
    ``"unknown"``) or null; extra columns are dropped.
    """
    if df.height == 0:
        return pl.DataFrame(schema=schema)
    exprs: list[pl.Expr] = []
    for name, dtype in schema.items():
        if name in df.columns:
            expr = pl.col(name).cast(dtype)
            if name in _LIST_COLUMNS:
                expr = expr.fill_null(pl.lit([], dtype=dtype))
            elif name in _DEFAULTS:
                expr = expr.fill_null(pl.lit(_DEFAULTS[name]).cast(dtype))
            exprs.append(expr.alias(name))
        elif name in _LIST_COLUMNS:
            exprs.append(pl.lit([], dtype=dtype).alias(name))
        else:
            exprs.append(pl.lit(_DEFAULTS.get(name)).cast(dtype).alias(name))
    return df.select(exprs)


def empty_table(name: str) -> pl.DataFrame:
    return pl.DataFrame(schema=TABLE_SCHEMAS[name])


# --------------------------------------------------------------------------------------
# Dataset
# --------------------------------------------------------------------------------------

_TABLES = ("people", "threads", "messages", "reactions", "media")


def _lazy(frame: pl.LazyFrame | pl.DataFrame) -> pl.LazyFrame:
    return frame.lazy() if isinstance(frame, pl.DataFrame) else frame


class Dataset:
    """One imported dataset: five tables as LazyFrames plus the import report.

    Instances are treated as immutable; build a new one and call
    :meth:`DatasetStore.replace` to change data. The small tables (``people``,
    ``threads``) are also available eagerly (cached) via :meth:`people_df` and
    :meth:`threads_df`.
    """

    def __init__(
        self,
        *,
        people: pl.LazyFrame | pl.DataFrame,
        threads: pl.LazyFrame | pl.DataFrame,
        messages: pl.LazyFrame | pl.DataFrame,
        reactions: pl.LazyFrame | pl.DataFrame | None = None,
        media: pl.LazyFrame | pl.DataFrame | None = None,
        import_report: dict[str, Any] | None = None,
        version: int = 0,
    ) -> None:
        self.people: pl.LazyFrame = _lazy(people)
        self.threads: pl.LazyFrame = _lazy(threads)
        self.messages: pl.LazyFrame = _lazy(messages)
        self.reactions: pl.LazyFrame = _lazy(
            reactions if reactions is not None else empty_table("reactions")
        )
        self.media: pl.LazyFrame = _lazy(media if media is not None else empty_table("media"))
        self.import_report: dict[str, Any] = import_report or {}
        #: Bumped by :class:`DatasetStore` on every replace/settings re-derivation.
        self.version: int = version
        self._people_df: pl.DataFrame | None = None
        self._threads_df: pl.DataFrame | None = None
        self._color_indices: dict[int, int] | None = None
        self._thread_rows: dict[int, dict[str, Any]] | None = None
        self._lock = threading.RLock()

    # ---- eager helpers -------------------------------------------------------------

    @property
    def dataset_version(self) -> int:
        return self.version

    def people_df(self) -> pl.DataFrame:
        """Eager ``people`` table (cached)."""
        with self._lock:
            if self._people_df is None:
                self._people_df = self.people.collect()
            return self._people_df

    def threads_df(self) -> pl.DataFrame:
        """Eager ``threads`` table (cached)."""
        with self._lock:
            if self._threads_df is None:
                self._threads_df = self.threads.collect()
            return self._threads_df

    def thread_rows(self) -> dict[int, dict[str, Any]]:
        """``thread_id -> threads row (dict)`` (cached; treat as read-only)."""
        with self._lock:
            if self._thread_rows is None:
                self._thread_rows = {
                    int(r["thread_id"]): r for r in self.threads_df().iter_rows(named=True)
                }
            return self._thread_rows

    def table(self, name: str) -> pl.LazyFrame:
        if name not in _TABLES:
            raise KeyError(name)
        frame: pl.LazyFrame = getattr(self, name)
        return frame

    def person_names(self) -> dict[int, str]:
        """``person_id -> display_name`` (real names; see AnalysisContext for anonymize)."""
        df = self.people_df()
        return dict(zip(df["person_id"].to_list(), df["display_name"].to_list(), strict=True))

    def owner_id(self) -> int | None:
        """``person_id`` flagged ``is_owner`` in the people table, if any."""
        owners = self.people_df().filter(pl.col("is_owner"))["person_id"]
        return int(owners[0]) if owners.len() else None

    def color_indices(self) -> dict[int, int]:
        """Stable color slot per person: owner = 0, others 1.. by total messages desc.

        Ties are broken by ``person_id``. Same for every endpoint, so a person keeps
        the same color across the whole app.
        """
        with self._lock:
            if self._color_indices is None:
                counts = self.messages.group_by("sender_id").agg(pl.len().alias("n")).collect()
                n_by_person = dict(
                    zip(counts["sender_id"].to_list(), counts["n"].to_list(), strict=True)
                )
                people = self.people_df()
                owner = people.filter(pl.col("is_owner"))["person_id"].to_list()
                others = sorted(
                    (int(p) for p in people["person_id"].to_list() if p not in owner),
                    key=lambda p: (-n_by_person.get(p, 0), p),
                )
                ordered = [int(o) for o in owner] + others
                self._color_indices = {p: i for i, p in enumerate(ordered)}
            return self._color_indices

    def counts(self) -> dict[str, int]:
        """Row counts per table: ``{"people", "threads", "messages", "reactions", "media"}``."""
        return {name: int(self.table(name).select(pl.len()).collect().item()) for name in _TABLES}

    # ---- persistence ---------------------------------------------------------------

    def save(self, directory: Path) -> None:
        """Write the dataset to ``directory`` atomically.

        Files are written to a sibling temp dir, then swapped in with renames, so a
        crash never leaves a half-written dataset behind.
        """
        directory = directory.resolve()
        directory.parent.mkdir(parents=True, exist_ok=True)
        tmp = directory.with_name(f".{directory.name}.tmp-{uuid.uuid4().hex}")
        tmp.mkdir()
        try:
            for name in _TABLES:
                conform(self.table(name).collect(), TABLE_SCHEMAS[name]).write_parquet(
                    tmp / f"{name}.parquet"
                )
            (tmp / "import_report.json").write_text(
                json.dumps(self.import_report, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            meta = {"version": self.version, "saved_at": datetime.now(UTC).isoformat()}
            (tmp / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
            old = directory.with_name(f".{directory.name}.old-{uuid.uuid4().hex}")
            if directory.exists():
                directory.rename(old)
            tmp.rename(directory)
            shutil.rmtree(old, ignore_errors=True)
        finally:
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)

    @classmethod
    def exists_at(cls, directory: Path) -> bool:
        return all((directory / f"{name}.parquet").is_file() for name in _TABLES)

    @classmethod
    def load(cls, directory: Path) -> "Dataset":
        """Open a saved dataset lazily (``pl.scan_parquet``)."""
        if not cls.exists_at(directory):
            raise FileNotFoundError(f"No dataset in {directory}")
        frames = {name: pl.scan_parquet(directory / f"{name}.parquet") for name in _TABLES}
        report_path = directory / "import_report.json"
        report: dict[str, Any] = (
            json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
        )
        version = 0
        meta_path = directory / "meta.json"
        if meta_path.is_file():
            version = int(json.loads(meta_path.read_text(encoding="utf-8")).get("version", 0))
        return cls(**frames, import_report=report, version=version)

    @classmethod
    def from_frames(
        cls,
        *,
        people: pl.DataFrame,
        threads: pl.DataFrame,
        messages: pl.DataFrame,
        reactions: pl.DataFrame | None = None,
        media: pl.DataFrame | None = None,
        import_report: dict[str, Any] | None = None,
        version: int = 0,
    ) -> "Dataset":
        """Build an in-memory dataset, coercing every frame with :func:`conform`."""
        return cls(
            people=conform(people, PEOPLE_SCHEMA),
            threads=conform(threads, THREADS_SCHEMA),
            messages=conform(messages, MESSAGES_SCHEMA),
            reactions=conform(
                reactions if reactions is not None else empty_table("reactions"),
                REACTIONS_SCHEMA,
            ),
            media=conform(media if media is not None else empty_table("media"), MEDIA_SCHEMA),
            import_report=import_report,
            version=version,
        )


# --------------------------------------------------------------------------------------
# Process-wide store
# --------------------------------------------------------------------------------------


class DatasetStore:
    """Holds the currently loaded :class:`Dataset` for the whole process.

    * :meth:`get` returns it or raises :class:`NoDataError` (→ HTTP 409).
    * :meth:`replace` installs a new dataset (persisting it by default), bumps the
      version and clears the response cache.
    * :meth:`bump_version` is for in-place re-derivation after settings changes.
    * :meth:`clear` forgets the dataset and deletes it from disk.

    All methods are thread-safe.
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._dataset: Dataset | None = None
        self._version = self._read_version()
        self._lock = threading.RLock()

    @property
    def dataset_dir(self) -> Path:
        return self.data_dir / "dataset"

    @property
    def version(self) -> int:
        """Current dataset version (0 = never imported)."""
        return self._version

    def get(self) -> Dataset:
        ds = self._dataset
        if ds is None:
            raise NoDataError()
        return ds

    def peek(self) -> Dataset | None:
        """The current dataset or ``None`` (never raises)."""
        return self._dataset

    def is_loaded(self) -> bool:
        return self._dataset is not None

    def load_from_disk(self) -> Dataset | None:
        """Load ``<data_dir>/dataset`` if present (called at startup)."""
        with self._lock:
            if not Dataset.exists_at(self.dataset_dir):
                self._dataset = None
                return None
            ds = Dataset.load(self.dataset_dir)
            self._version = max(self._version, ds.version)
            ds.version = self._version
            self._dataset = ds
            response_cache.clear()
            return ds

    def replace(self, new_dataset: Dataset, *, persist: bool = True) -> Dataset:
        """Install ``new_dataset`` as the current one and return the installed instance.

        With ``persist=True`` the dataset is saved atomically to ``<data_dir>/dataset``
        and re-opened lazily from Parquet (so memory is released). Tests may pass
        ``persist=False`` to keep it in memory only.
        """
        with self._lock:
            self._version += 1
            new_dataset.version = self._version
            if persist:
                new_dataset.save(self.dataset_dir)
                installed = Dataset.load(self.dataset_dir)
                installed.version = self._version
            else:
                installed = new_dataset
            self._write_version()
            self._dataset = installed
            response_cache.clear()
            return installed

    def bump_version(self) -> int:
        """Increase the version (e.g. after a settings change) and clear the cache."""
        with self._lock:
            self._version += 1
            if self._dataset is not None:
                self._dataset.version = self._version
            self._write_version()
            response_cache.clear()
            return self._version

    def clear(self, *, delete_files: bool = True) -> None:
        """Forget the dataset; with ``delete_files`` also remove it from disk."""
        with self._lock:
            self._dataset = None
            self._version += 1
            if delete_files and self.dataset_dir.exists():
                shutil.rmtree(self.dataset_dir, ignore_errors=True)
            with contextlib.suppress(OSError):
                self._write_version()
            response_cache.clear()

    # ---- version file ----------------------------------------------------------------

    def _version_path(self) -> Path:
        return self.data_dir / "version.json"

    def _read_version(self) -> int:
        try:
            return int(json.loads(self._version_path().read_text(encoding="utf-8"))["version"])
        except (OSError, ValueError, KeyError, TypeError):
            return 0

    def _write_version(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._version_path().write_text(json.dumps({"version": self._version}), encoding="utf-8")


_store: DatasetStore | None = None
_store_lock = threading.Lock()


def get_store() -> DatasetStore:
    """Process-wide store (created on first use from ``get_settings().data_dir``)."""
    global _store
    with _store_lock:
        if _store is None:
            from instagraph.config import get_settings

            _store = DatasetStore(get_settings().data_dir)
        return _store


def set_store(store: DatasetStore) -> None:
    """Install a store (done by ``create_app``; tests use it with a temp dir)."""
    global _store
    with _store_lock:
        _store = store
