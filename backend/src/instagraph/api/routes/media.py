"""Media files and thumbnails (spec §8.2, §10, decision D2).

Files are served **only by ``media_id``** from the ``media`` table — the request never
contains a path — so path traversal is impossible. Rows with ``exists = false`` or
whose file can't be found give 404. Plain files are served with ``FileResponse``
(supports HTTP Range for video seeking); zip members are streamed.
"""

from typing import Annotated, Any

import polars as pl
from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, Response, StreamingResponse

from instagraph.api.deps import DatasetDep, StoreDep
from instagraph.errors import ERROR_RESPONSES, NotFoundError
from instagraph.media.files import (
    MediaUnavailableError,
    ResolvedMedia,
    content_type_for,
    iter_chunks,
    media_size,
    resolve_media,
)
from instagraph.media.thumbs import DEFAULT_SIZE, MAX_SIZE, MIN_SIZE, get_thumbnail
from instagraph.store.dataset import Dataset

router = APIRouter(prefix="/api/media", tags=["media"], responses=ERROR_RESPONSES)

#: Sandbox anything opened directly in the browser.
MEDIA_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; img-src 'self'; media-src 'self'; sandbox",
    "Cache-Control": "private, max-age=3600",
    "X-Content-Type-Options": "nosniff",
}

_BINARY_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "content": {"application/octet-stream": {}},
        "description": "The file (image/*, video/*, audio/*)",
    }
}


def _row(ds: Dataset, media_id: int) -> dict[str, Any]:
    df = ds.media.filter(pl.col("media_id") == media_id).collect()
    if df.is_empty():
        raise NotFoundError(f"Media {media_id} does not exist.")
    row: dict[str, Any] = df.row(0, named=True)
    if not row["exists"]:
        raise NotFoundError(f"Media {media_id} is not in the export.")
    return row


def _resolve(row: dict[str, Any]) -> ResolvedMedia:
    try:
        return resolve_media(row["source"], row["uri"])
    except MediaUnavailableError as exc:
        raise NotFoundError(f"Media {row['media_id']} file is not available.") from exc


@router.get(
    "/{media_id}",
    response_class=Response,
    responses=_BINARY_RESPONSES,
    summary="Media file by id",
)
def get_media(media_id: int, ds: DatasetDep) -> Response:
    """The attachment file, read from the export folder or straight from the zip."""
    row = _row(ds, media_id)
    resolved = _resolve(row)
    media_type = content_type_for(resolved.name or row["uri"] or "", row["kind"])
    if resolved.kind == "file":
        return FileResponse(resolved.path, media_type=media_type, headers=MEDIA_HEADERS)
    headers = dict(MEDIA_HEADERS)
    size = media_size(resolved)
    if size is not None:
        headers["Content-Length"] = str(size)
    try:
        chunks = iter_chunks(resolved)
        first = next(chunks, b"")
    except MediaUnavailableError as exc:
        raise NotFoundError(f"Media {media_id} file is not available.") from exc

    def stream() -> Any:
        yield first
        yield from chunks

    return StreamingResponse(stream(), media_type=media_type, headers=headers)


@router.get(
    "/{media_id}/thumb",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}, "image/svg+xml": {}}}},
    summary="Cached thumbnail",
)
def get_thumb(
    media_id: int,
    ds: DatasetDep,
    store: StoreDep,
    size: Annotated[int, Query(ge=MIN_SIZE, le=MAX_SIZE)] = DEFAULT_SIZE,
) -> Response:
    """JPEG thumbnail (cached in ``<data_dir>/thumbs``), or a placeholder SVG for
    videos, voice notes and images that can't be decoded."""
    row = _row(ds, media_id)
    resolved = _resolve(row)
    thumb = get_thumbnail(
        store.data_dir,
        media_id=media_id,
        kind=str(row["kind"]),
        source=row["source"],
        uri=row["uri"],
        resolved=resolved,
        size=size,
    )
    if thumb.path is not None:
        return FileResponse(thumb.path, media_type=thumb.media_type, headers=MEDIA_HEADERS)
    return Response(content=thumb.content, media_type=thumb.media_type, headers=MEDIA_HEADERS)
