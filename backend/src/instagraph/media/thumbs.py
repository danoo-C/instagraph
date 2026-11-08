"""Cached thumbnails for media files (spec §7.2 Media tab, decision D2).

Images (photo / gif / sticker) are decoded with Pillow, rotated according to EXIF
orientation, shrunk to fit ``size × size`` and saved as JPEG in
``<data_dir>/thumbs/<media_id>_<size>_<key>.jpg``. ``key`` is a short hash of the
row's ``source``/``uri`` so a re-import that reuses a ``media_id`` for a different
file never serves a stale thumbnail. Videos, audio and undecodable images get a
placeholder SVG (there is no ffmpeg for video poster frames).
"""

import hashlib
import io
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from instagraph.media.files import ResolvedMedia, read_bytes

MIN_SIZE = 32
MAX_SIZE = 1024
DEFAULT_SIZE = 256
#: Refuse to decode absurdly large images (decompression bomb guard).
MAX_PIXELS = 80_000_000
#: Refuse to read more than this many bytes for a thumbnail.
MAX_INPUT_BYTES = 100 * 1024 * 1024

IMAGE_KINDS = frozenset({"photo", "gif", "sticker"})

_ICONS = {
    "video": '<polygon points="42,34 42,66 70,50" fill="currentColor"/>',
    "audio": (
        '<rect x="44" y="28" width="12" height="28" rx="6" fill="currentColor"/>'
        '<path d="M36 50a14 14 0 0 0 28 0M50 64v10" stroke="currentColor" '
        'stroke-width="4" fill="none"/>'
    ),
    "image": (
        '<rect x="28" y="32" width="44" height="36" rx="4" stroke="currentColor" '
        'stroke-width="4" fill="none"/><circle cx="40" cy="44" r="4" fill="currentColor"/>'
        '<path d="M30 66l14-14 10 10 6-6 10 10" stroke="currentColor" stroke-width="4" '
        'fill="none"/>'
    ),
}


@dataclass(frozen=True, slots=True)
class Thumbnail:
    content: bytes | None
    path: Path | None
    media_type: str


def clamp_size(size: int) -> int:
    return max(MIN_SIZE, min(MAX_SIZE, int(size)))


def placeholder_svg(kind: str) -> bytes:
    """A neutral square placeholder icon for ``kind`` (video/audio/other)."""
    icon = _ICONS.get(kind, _ICONS["image"])
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
        'width="100" height="100" color="#6b7280">'
        '<rect width="100" height="100" fill="#e5e7eb"/>'
        f"{icon}</svg>"
    )
    return svg.encode("utf-8")


def thumb_key(source: str | None, uri: str | None) -> str:
    return hashlib.sha1(f"{source or ''}\0{uri or ''}".encode()).hexdigest()[:10]


def thumb_path(data_dir: Path, media_id: int, size: int, key: str) -> Path:
    return data_dir / "thumbs" / f"{int(media_id)}_{int(size)}_{key}.jpg"


def render_thumbnail(data: bytes, size: int) -> bytes:
    """JPEG thumbnail bytes for image ``data`` (raises on undecodable input)."""
    with Image.open(io.BytesIO(data)) as img:
        if img.width * img.height > MAX_PIXELS:
            raise ValueError("image too large")
        img.draft("RGB", (size, size))
        oriented = ImageOps.exif_transpose(img)
        if oriented.mode in ("RGBA", "LA", "P"):
            rgba = oriented.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.getchannel("A"))
            oriented = background
        elif oriented.mode != "RGB":
            oriented = oriented.convert("RGB")
        oriented.thumbnail((size, size), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        oriented.save(out, "JPEG", quality=82, optimize=True)
        return out.getvalue()


def get_thumbnail(
    data_dir: Path,
    *,
    media_id: int,
    kind: str,
    source: str | None,
    uri: str | None,
    resolved: ResolvedMedia,
    size: int = DEFAULT_SIZE,
) -> Thumbnail:
    """Return a cached JPEG thumbnail (``path`` set) or a placeholder SVG (``content``)."""
    size = clamp_size(size)
    if kind not in IMAGE_KINDS:
        return Thumbnail(placeholder_svg(kind), None, "image/svg+xml")
    target = thumb_path(data_dir, media_id, size, thumb_key(source, uri))
    if target.is_file():
        return Thumbnail(None, target, "image/jpeg")
    try:
        data = read_bytes(resolved, MAX_INPUT_BYTES + 1)
        if len(data) > MAX_INPUT_BYTES:
            raise ValueError("file too large")
        jpeg = render_thumbnail(data, size)
    except Exception:
        return Thumbnail(placeholder_svg("image"), None, "image/svg+xml")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_bytes(jpeg)
        os.replace(tmp, target)
    except OSError:
        tmp.unlink(missing_ok=True)
        return Thumbnail(jpeg, None, "image/jpeg")
    return Thumbnail(None, target, "image/jpeg")
