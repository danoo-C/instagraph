"""Discovery of Instagram export files in folders and zip archives (spec §3.2, §10).

Nothing is extracted: a :class:`FileRef` points either at a plain file or at a member of
a zip file and reads it on demand. Thread files are found with the glob semantics of
``**/messages/{inbox,message_requests,archived_threads}/*/message_*.json`` wherever the
``messages`` folder is, so both export layouts (``your_instagram_activity/messages/…``
and ``messages/…`` at the root) work without hard-coded roots.

Safety (§10):

* Folders are resolved with :meth:`Path.resolve`; files or directories reached through
  symlinks that point outside the root are ignored.
* Zip members with absolute paths, drive letters or ``..`` components raise
  :class:`ImportSafetyError` (zip slip), as do archives whose declared total
  uncompressed size or member count exceed the caps (zip bomb). Members are streamed,
  never extracted; reads are bounded by the declared sizes (``zipfile`` enforces them).
"""

import contextlib
import os
import re
import stat
import threading
import zipfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Literal, cast

import orjson

from instagraph.textproc import fix_mojibake

__all__ = [
    "CATEGORY_DIRS",
    "DiscoveryError",
    "DiscoveryResult",
    "FileRef",
    "HtmlExportError",
    "ImportSafetyError",
    "ThreadSource",
    "close_zip_cache",
    "discover",
    "match_media",
    "media_key",
    "read_owner_name",
]

ThreadCategory = Literal["inbox", "request", "archived"]

#: Folder name under ``messages/`` → thread category.
CATEGORY_DIRS: dict[str, ThreadCategory] = {
    "inbox": "inbox",
    "message_requests": "request",
    "archived_threads": "archived",
}

THREAD_FILE_RE = re.compile(r"^message_(\d+)\.json$", re.IGNORECASE)
HTML_FILE_RE = re.compile(r"^message_(\d+)\.html?$", re.IGNORECASE)
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
#: A single member whose declared size is above this AND whose compression ratio is above
#: :data:`_MAX_RATIO` is treated as a zip bomb even if the total is under the cap.
_RATIO_CHECK_MIN_SIZE = 256 * 2**20
_MAX_RATIO = 1000


class DiscoveryError(Exception):
    """Base class for discovery problems the user must fix."""


class ImportSafetyError(DiscoveryError):
    """Zip slip, zip bomb or another unsafe path."""


class HtmlExportError(DiscoveryError):
    """The export was made in HTML format (``message_1.html``) instead of JSON."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or "This export is in HTML format. Instagram → Download your information → "
            "choose Format: JSON (not HTML) and import the new export."
        )


# --------------------------------------------------------------------------------------
# File references
# --------------------------------------------------------------------------------------

_zip_cache: dict[str, zipfile.ZipFile] = {}
_zip_cache_pid: int = os.getpid()
_zip_lock = threading.Lock()


def _get_zip(path: str) -> zipfile.ZipFile:
    """Process-local cache of open ``ZipFile`` objects (reads are thread-safe)."""
    global _zip_cache_pid
    with _zip_lock:
        if _zip_cache_pid != os.getpid():  # forked child: don't share file handles
            _zip_cache.clear()
            _zip_cache_pid = os.getpid()
        zf = _zip_cache.get(path)
        if zf is None:
            zf = zipfile.ZipFile(path)
            _zip_cache[path] = zf
        return zf


def close_zip_cache() -> None:
    """Close all cached zip handles (e.g. before deleting uploaded zips)."""
    with _zip_lock:
        for zf in _zip_cache.values():
            with contextlib.suppress(Exception):
                zf.close()
        _zip_cache.clear()


@dataclass(frozen=True, slots=True)
class FileRef:
    """A file in a folder (``member is None``) or a member of a zip file. Picklable."""

    path: str
    """Absolute filesystem path of the file, or of the zip file."""
    member: str | None = None
    """Zip member name (exactly as stored in the archive); ``None`` for plain files."""

    def read_bytes(self) -> bytes:
        if self.member is None:
            return Path(self.path).read_bytes()
        return _get_zip(self.path).read(self.member)

    def open(self) -> BinaryIO:
        """Open for streaming reads; the caller closes the handle."""
        if self.member is None:
            return open(self.path, "rb")
        return cast(BinaryIO, _get_zip(self.path).open(self.member))

    def describe(self) -> str:
        """``/abs/path`` or ``/abs/part1.zip!member/name`` (the ``media.source`` form)."""
        return self.path if self.member is None else f"{self.path}!{self.member}"

    @property
    def name(self) -> str:
        """Base file name."""
        return PurePosixPath(self.member).name if self.member is not None else Path(self.path).name


@dataclass(frozen=True, slots=True)
class ThreadSource:
    """All ``message_N.json`` files of one thread folder (merged across zip parts)."""

    folder: str
    category: ThreadCategory
    files: tuple[FileRef, ...]
    """Sorted by file index (``message_1.json`` first)."""

    def describe(self) -> str:
        """Human-readable origin of the thread folder (first file's directory)."""
        if not self.files:
            return self.folder
        first = self.files[0]
        if first.member is None:
            return str(Path(first.path).parent)
        return f"{first.path}!{PurePosixPath(first.member).parent}"


@dataclass
class DiscoveryResult:
    threads: list[ThreadSource] = field(default_factory=list)
    personal_info: FileRef | None = None
    media_files: dict[str, FileRef] = field(default_factory=dict)
    """Normalized uri suffix starting at ``messages/…`` → file (see :func:`media_key`)."""
    layouts: set[str] = field(default_factory=set)
    """``your_instagram_activity`` and/or ``root_messages``."""
    file_count: int = 0
    """Number of files / zip members looked at."""
    zip_files: list[str] = field(default_factory=list)
    html_files: int = 0
    """Number of ``message_N.html`` files seen (HTML export)."""


# --------------------------------------------------------------------------------------
# Path helpers
# --------------------------------------------------------------------------------------


def _norm_parts(path: str) -> list[str]:
    """Split a relative path (``/`` or ``\\``) into components, dropping ``.``/empty."""
    return [p for p in path.replace("\\", "/").split("/") if p not in ("", ".")]


def _messages_index(parts: Sequence[str]) -> int | None:
    """Index of the ``messages`` component, preferring one followed by a category dir."""
    first: int | None = None
    for i in range(len(parts) - 1, -1, -1):
        if parts[i].lower() != "messages":
            continue
        if i + 1 < len(parts) and parts[i + 1].lower() in CATEGORY_DIRS:
            return i
        first = i
    return first


def media_key(path: str) -> str | None:
    """Normalized key of a media path or JSON ``uri``: the suffix from ``messages/``.

    ``"your_instagram_activity/messages/inbox/x_1/photos/a.jpg"`` →
    ``"messages/inbox/x_1/photos/a.jpg"``. ``None`` if there is no ``messages`` part.
    """
    parts = _norm_parts(path)
    idx = _messages_index(parts)
    if idx is None or idx == len(parts) - 1:
        return None
    return "/".join(["messages", *parts[idx + 1 :]])


def match_media(uri: str, media_files: dict[str, FileRef]) -> FileRef | None:
    """Find the file for a JSON ``uri`` (tries the mojibake-fixed form too)."""
    for candidate in dict.fromkeys((uri, fix_mojibake(uri))):
        key = media_key(candidate)
        if key is not None:
            ref = media_files.get(key)
            if ref is not None:
                return ref
    return None


@dataclass
class _Collector:
    """Accumulates classified files from all sources."""

    result: DiscoveryResult = field(default_factory=DiscoveryResult)
    thread_files: dict[tuple[ThreadCategory, str], list[tuple[int, FileRef]]] = field(
        default_factory=dict
    )
    personal_candidates: list[tuple[int, str, FileRef]] = field(default_factory=list)

    def add(self, rel: str, ref: FileRef) -> None:
        self.result.file_count += 1
        parts = _norm_parts(rel)
        if not parts:
            return
        name = parts[-1]
        lname = name.lower()
        if lname == "personal_information.json":
            # Prefer .../personal_information/personal_information.json.
            rank = 0 if len(parts) >= 2 and parts[-2].lower() == "personal_information" else 1
            self.personal_candidates.append((rank, rel, ref))
            return
        idx = _messages_index(parts)
        if idx is None:
            return
        tail = parts[idx + 1 :]
        if len(tail) == 3 and tail[0].lower() in CATEGORY_DIRS:
            m = THREAD_FILE_RE.match(name)
            if m:
                category = CATEGORY_DIRS[tail[0].lower()]
                key = (category, tail[1])
                self.thread_files.setdefault(key, []).append((int(m.group(1)), ref))
                prev = parts[idx - 1].lower() if idx > 0 else ""
                self.result.layouts.add(
                    "your_instagram_activity"
                    if prev == "your_instagram_activity"
                    else "root_messages"
                )
                return
            if HTML_FILE_RE.match(name):
                self.result.html_files += 1
                return
        if lname.endswith((".json", ".html", ".htm")):
            return
        key_media = "/".join(["messages", *tail])
        self.result.media_files.setdefault(key_media, ref)

    def finish(self) -> DiscoveryResult:
        res = self.result
        for (category, folder), files in sorted(self.thread_files.items()):
            files.sort(key=lambda t: (t[0], t[1].describe()))
            res.threads.append(
                ThreadSource(folder=folder, category=category, files=tuple(f for _, f in files))
            )
        if self.personal_candidates:
            self.personal_candidates.sort(key=lambda t: (t[0], t[1]))
            res.personal_info = self.personal_candidates[0][2]
        return res


# --------------------------------------------------------------------------------------
# Folders
# --------------------------------------------------------------------------------------


def _walk_folder(root: Path, collector: _Collector, zips: list[Path]) -> None:
    """Walk ``root`` (already resolved). Top-level ``*.zip`` files are added to ``zips``."""
    base = root.parent
    root_s = str(root)

    def inside(p: str) -> bool:
        return p == root_s or p.startswith(root_s + os.sep)

    # (lexical path, real path, real paths of the ancestors incl. itself) — the ancestor
    # chain stops symlink cycles without hiding directories reachable twice.
    stack: list[tuple[Path, str, frozenset[str]]] = [(root, root_s, frozenset({root_s}))]
    while stack:
        current, real_dir, chain = stack.pop()
        try:
            entries = sorted(os.scandir(current), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            entry_path = Path(entry.path)
            real = os.path.join(real_dir, entry.name)
            try:
                if entry.is_symlink():
                    real = os.path.realpath(entry.path)
                    if not inside(real):
                        continue  # symlink escaping the root: ignore (§10)
                    st = os.stat(real)
                    is_dir = stat.S_ISDIR(st.st_mode)
                    is_file = stat.S_ISREG(st.st_mode)
                    if is_dir and real in chain:
                        continue  # cycle
                else:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    is_file = entry.is_file(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                stack.append((entry_path, real, chain | {real}))
            elif is_file:
                if current == root and entry.name.lower().endswith(".zip"):
                    zips.append(Path(real))
                    continue
                rel = entry_path.relative_to(base).as_posix()
                collector.add(rel, FileRef(path=real))


# --------------------------------------------------------------------------------------
# Zips
# --------------------------------------------------------------------------------------


def _check_member_name(name: str) -> None:
    norm = name.replace("\\", "/")
    if (
        "\x00" in name
        or norm.startswith("/")
        or _DRIVE_RE.match(norm)
        or any(p == ".." for p in norm.split("/"))
    ):
        raise ImportSafetyError(f"Unsafe path in zip archive: {name!r}")


def _is_symlink_member(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def _scan_zip(
    path: Path,
    collector: _Collector,
    budget: dict[str, int],
    max_total_uncompressed: int,
    max_members: int,
) -> None:
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise DiscoveryError(f"Not a readable zip file: {path} ({exc})") from exc
    with zf:
        infos = zf.infolist()
        budget["members"] += len(infos)
        if budget["members"] > max_members:
            raise ImportSafetyError(
                f"Too many files in the zip archive(s) (> {max_members}); refusing to import."
            )
        for info in infos:
            _check_member_name(info.filename)
            if info.is_dir() or _is_symlink_member(info):
                continue
            budget["size"] += info.file_size
            if budget["size"] > max_total_uncompressed:
                raise ImportSafetyError(
                    "The zip archive(s) would expand to more than "
                    f"{max_total_uncompressed} bytes; refusing to import (zip bomb protection)."
                )
            if info.file_size > _RATIO_CHECK_MIN_SIZE and info.file_size > _MAX_RATIO * max(
                info.compress_size, 1
            ):
                raise ImportSafetyError(
                    f"Suspicious compression ratio for {info.filename!r} (zip bomb protection)."
                )
            collector.add(info.filename, FileRef(path=str(path), member=info.filename))


def _is_zip(path: Path) -> bool:
    try:
        return zipfile.is_zipfile(path)
    except OSError:
        return False


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def discover(
    sources: Sequence[Path],
    *,
    max_total_uncompressed: int = 64 * 2**30,
    max_members: int = 2_000_000,
) -> DiscoveryResult:
    """Find thread files, ``personal_information.json`` and media in ``sources``.

    ``sources`` are folders (unzipped exports, or folders containing several zips) and/or
    zip files. Multi-part zips are merged: the same thread folder appearing in several
    parts becomes ONE :class:`ThreadSource` with the files combined.

    Raises :class:`FileNotFoundError` for a missing source, :class:`ImportSafetyError` for
    unsafe zips, :class:`HtmlExportError` when only ``message_N.html`` files were found and
    :class:`DiscoveryError` for unreadable zips / unsupported files.
    """
    collector = _Collector()
    budget = {"members": 0, "size": 0}
    zips: list[Path] = []
    for src in sources:
        p = Path(src).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"Path does not exist: {src}")
        p = p.resolve()
        if p.is_dir():
            _walk_folder(p, collector, zips)
        elif _is_zip(p):
            zips.append(p)
        else:
            raise DiscoveryError(f"Not a folder or a zip file: {src}")
    for z in dict.fromkeys(zips):
        _scan_zip(z, collector, budget, max_total_uncompressed, max_members)
        collector.result.zip_files.append(str(z))
    result = collector.finish()
    if not result.threads and result.html_files:
        raise HtmlExportError()
    return result


def iter_thread_files(result: DiscoveryResult) -> Iterator[FileRef]:
    for t in result.threads:
        yield from t.files


# --------------------------------------------------------------------------------------
# personal_information.json
# --------------------------------------------------------------------------------------

#: Localized labels of the display-name entry in ``string_map_data``.
_NAME_LABELS = ("Name", "Meno", "Jméno", "Jmeno", "Nombre", "Nom", "Nome")


def _find_string_maps(node: Any, out: list[dict[str, Any]], depth: int = 0) -> None:
    if depth > 8:
        return
    if isinstance(node, dict):
        smd = node.get("string_map_data")
        if isinstance(smd, dict):
            out.append(smd)
        for v in node.values():
            _find_string_maps(v, out, depth + 1)
    elif isinstance(node, list):
        for v in node:
            _find_string_maps(v, out, depth + 1)


def _entry_value(entry: Any) -> str | None:
    if isinstance(entry, dict):
        entry = entry.get("value")
    if isinstance(entry, str) and entry.strip():
        return fix_mojibake(entry.strip())
    return None


def read_owner_name(ref: FileRef) -> str | None:
    """Owner's display name from ``personal_information.json`` (mojibake fixed).

    Handles ``{"profile_user": [{"string_map_data": {"Name": {"value": …}}}]}`` (and any
    nesting of ``string_map_data``), localized labels and a plain ``{"name": …}``.
    Returns ``None`` if the file can't be read or has no name.
    """
    try:
        data = orjson.loads(ref.read_bytes())
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None
    maps: list[dict[str, Any]] = []
    _find_string_maps(data, maps)
    for smd in maps:
        fixed = {fix_mojibake(k).strip().rstrip(":"): v for k, v in smd.items()}
        for label in _NAME_LABELS:
            value = _entry_value(fixed.get(label))
            if value:
                return value
        for k, v in fixed.items():
            if k.lower() in ("name", "full name", "display name"):
                value = _entry_value(v)
                if value:
                    return value
    if isinstance(data, dict):
        for k in ("name", "full_name"):
            value = _entry_value(data.get(k))
            if value:
                return value
    return None
