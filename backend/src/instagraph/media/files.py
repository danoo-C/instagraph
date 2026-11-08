"""Locate and open media files registered in the ``media`` table (spec §10, decision D2).

Files are only ever resolved from a media row's ``source`` + ``uri`` (never from a
request path). Supported ``source`` formats:

* ``"<zip path>!<member>"`` — the format documented in ``store.dataset.MEDIA_SCHEMA``;
  ``"<zip path>::<member>"`` is accepted too. An empty member means "use ``uri``".
  If the member is not in the zip, a member equal to ``uri`` or ending in
  ``"/" + uri`` (or vice versa) is used.
* ``"<file path>"`` — the media file itself.
* ``"<folder path>"`` — a folder (export root or thread folder); the file is
  ``folder / uri``, or ``folder / <trailing part of uri>`` when the uri has a longer
  prefix than the folder. The resolved file must stay inside the folder (symlinks
  pointing outside are refused).

Zip members are streamed (no extraction to disk); member names with ``..`` or an
absolute path are refused.
"""

import mimetypes
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, Literal

CHUNK_SIZE = 64 * 1024
ZIP_SEPARATORS = ("!", "::")

_KIND_FALLBACK_TYPES = {
    "photo": "image/jpeg",
    "video": "video/mp4",
    "audio": "audio/mp4",
    "gif": "image/gif",
    "sticker": "image/png",
}
_EXTRA_TYPES = {
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".opus": "audio/ogg",
    ".ogg": "audio/ogg",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}


class MediaUnavailableError(Exception):
    """The file for a media row cannot be found or read."""


@dataclass(frozen=True, slots=True)
class ResolvedMedia:
    """Where a media file lives: a plain file or a member of a zip archive."""

    kind: Literal["file", "zip"]
    path: Path
    member: str | None = None

    @property
    def name(self) -> str:
        return self.member if self.member is not None else self.path.name


def _safe_member(name: str) -> bool:
    if not name or name.startswith(("/", "\\")) or "\\" in name:
        return False
    p = PurePosixPath(name)
    return ".." not in p.parts and not (len(name) > 1 and name[1] == ":")


def _split_zip(source: str) -> tuple[Path, str] | None:
    """Split ``"<zip>!<member>"`` / ``"<zip>::<member>"`` at the first separator whose
    left part is an existing zip file."""
    for sep in ZIP_SEPARATORS:
        start = 0
        while (idx := source.find(sep, start)) != -1:
            left = Path(source[:idx])
            if left.suffix.lower() == ".zip" and left.is_file():
                return left, source[idx + len(sep) :]
            start = idx + 1
    return None


def _uri_parts(uri: str) -> list[str]:
    parts = [p for p in uri.replace("\\", "/").split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise MediaUnavailableError("unsafe uri")
    return parts


def _resolve_zip_member(zip_path: Path, member: str, uri: str) -> str:
    candidates = [m for m in (member, uri.lstrip("/")) if m]
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
    except (OSError, zipfile.BadZipFile) as exc:
        raise MediaUnavailableError(f"cannot open zip: {exc}") from exc
    name_set = set(names)
    for cand in candidates:
        if cand in name_set and _safe_member(cand):
            return cand
    for cand in candidates:
        suffix = "/" + cand
        for name in names:
            if (name.endswith(suffix) or cand.endswith("/" + name)) and _safe_member(name):
                return name
    raise MediaUnavailableError("member not found in zip")


def _resolve_in_folder(folder: Path, uri: str) -> Path:
    root = folder.resolve()
    parts = _uri_parts(uri)
    for k in range(len(parts)):
        candidate = root.joinpath(*parts[k:])
        if not candidate.is_file():
            continue
        real = candidate.resolve()
        if real.is_relative_to(root) and real.is_file():
            return real
    raise MediaUnavailableError("file not found in folder")


def resolve_media(source: str | None, uri: str | None) -> ResolvedMedia:
    """Resolve a media row's ``source``/``uri`` to a file or zip member.

    Raises :class:`MediaUnavailableError` if nothing readable is found.
    """
    source = (source or "").strip()
    uri = (uri or "").strip()
    if not source:
        raise MediaUnavailableError("no source")
    split = _split_zip(source)
    if split is not None:
        zip_path, member = split
        return ResolvedMedia("zip", zip_path, _resolve_zip_member(zip_path, member, uri))
    path = Path(source)
    if path.is_file():
        if path.suffix.lower() == ".zip" and uri and zipfile.is_zipfile(path):
            return ResolvedMedia("zip", path, _resolve_zip_member(path, "", uri))
        return ResolvedMedia("file", path.resolve())
    if path.is_dir():
        if not uri:
            raise MediaUnavailableError("no uri")
        return ResolvedMedia("file", _resolve_in_folder(path, uri))
    raise MediaUnavailableError("source not found")


def open_media(resolved: ResolvedMedia) -> IO[bytes]:
    """Open the media file for binary reading (caller closes it)."""
    try:
        if resolved.kind == "file":
            return resolved.path.open("rb")
        assert resolved.member is not None
        zf = zipfile.ZipFile(resolved.path)
        try:
            handle = zf.open(resolved.member)
        except Exception:
            zf.close()
            raise
        # ZipExtFile keeps a reference to the file; closing ``zf`` now is safe because
        # the archive file handle is shared and closed when the member is closed.
        zf.close()
        return handle
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise MediaUnavailableError(str(exc)) from exc


def read_bytes(resolved: ResolvedMedia, limit: int | None = None) -> bytes:
    """Read the whole file (or the first ``limit`` bytes)."""
    with open_media(resolved) as fh:
        return fh.read() if limit is None else fh.read(limit)


def iter_chunks(resolved: ResolvedMedia, chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
    """Stream the file in chunks (used for zip members)."""
    with open_media(resolved) as fh:
        while chunk := fh.read(chunk_size):
            yield chunk


def media_size(resolved: ResolvedMedia) -> int | None:
    """Uncompressed size in bytes, if known."""
    try:
        if resolved.kind == "file":
            return resolved.path.stat().st_size
        with zipfile.ZipFile(resolved.path) as zf:
            return zf.getinfo(resolved.member or "").file_size
    except (OSError, KeyError, zipfile.BadZipFile):
        return None


def content_type_for(name: str, kind: str | None = None) -> str:
    """Media type from the file extension, falling back to the media ``kind``.

    Only ``image/*``, ``video/*`` and ``audio/*`` are returned (anything else becomes
    the kind's default or ``application/octet-stream``) so an attachment can never be
    served as HTML/JS. SVG is downgraded to octet-stream for the same reason.
    """
    suffix = PurePosixPath(name).suffix.lower()
    guessed = _EXTRA_TYPES.get(suffix) or mimetypes.guess_type(name)[0]
    if (
        guessed
        and guessed.split("/", 1)[0] in {"image", "video", "audio"}
        and guessed != "image/svg+xml"
    ):
        return guessed
    if kind in _KIND_FALLBACK_TYPES and not suffix:
        return _KIND_FALLBACK_TYPES[kind]
    if kind in _KIND_FALLBACK_TYPES and guessed is None:
        return _KIND_FALLBACK_TYPES[kind]
    return "application/octet-stream"
