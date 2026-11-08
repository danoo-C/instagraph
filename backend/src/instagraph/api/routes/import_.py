"""Import endpoints (spec §5.2, §5.4, §8.2).

* ``POST /api/import/upload`` — multipart zips, streamed straight to
  ``<data_dir>/uploads/<job>/`` with a streaming multipart parser (never held in memory,
  no temp-file double copy), then imported. The zips are kept (decision D2).
* ``POST /api/import/path`` — a local folder / zip / folder of zips (no copy).
* ``POST /api/import/sample`` — generate a fake export and import it.
* ``GET  /api/import/jobs/{id}`` / ``.../events`` (SSE) / ``DELETE`` (cancel).
* ``GET  /api/import/report`` — the import report of the loaded dataset.
* ``DELETE /api/import/uploads`` — delete kept zips (stats stay, zip media → missing).

Only one import runs at a time (``409 import_running``).
"""

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import IO, Any

from fastapi import APIRouter, Request
from python_multipart.multipart import MultipartParser, parse_options_header
from sse_starlette import EventSourceResponse
from starlette.requests import ClientDisconnect

from instagraph.api.deps import DatasetDep, SettingsDep
from instagraph.errors import ERROR_RESPONSES, ApiError, ErrorResponse, NotFoundError
from instagraph.importer.jobs import (
    ImportJob,
    delete_uploads,
    get_job_manager,
    sample_sources,
    uploads_dir,
)
from instagraph.schemas.import_ import (
    DeleteUploadsResponse,
    ImportJobRef,
    ImportJobState,
    ImportReport,
    PathImportRequest,
    SampleImportRequest,
)
from instagraph.store.dataset import get_store

router = APIRouter(
    prefix="/api/import",
    tags=["import"],
    responses={**ERROR_RESPONSES, 400: {"model": ErrorResponse, "description": "Bad request"}},
)

#: SSE: poll interval and max silence between events while a job is active.
_POLL_S = 0.1
_HEARTBEAT_S = 0.4


def _job_or_404(job_id: str) -> ImportJob:
    job = get_job_manager().get(job_id)
    if job is None:
        raise NotFoundError(f"Unknown import job: {job_id}")
    return job


def _state(job: ImportJob) -> ImportJobState:
    return ImportJobState.model_validate(job.snapshot())


# --------------------------------------------------------------------------------------
# Upload (streaming multipart)
# --------------------------------------------------------------------------------------


class _UploadWriter:
    """Callbacks for ``MultipartParser`` writing each file part to ``dest``."""

    def __init__(self, dest: Path) -> None:
        self.dest = dest
        self.files: list[Path] = []
        self.bytes = 0
        self._header_field = b""
        self._header_value = b""
        self._headers: dict[bytes, bytes] = {}
        self._fh: IO[bytes] | None = None
        self.error: ApiError | None = None

    # header parsing
    def on_part_begin(self) -> None:
        self._headers = {}
        self._header_field = b""
        self._header_value = b""

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._header_field += data[start:end]

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._header_value += data[start:end]

    def on_header_end(self) -> None:
        self._headers[self._header_field.lower()] = self._header_value
        self._header_field = b""
        self._header_value = b""

    def on_headers_finished(self) -> None:
        _, params = parse_options_header(self._headers.get(b"content-disposition", b""))
        raw_name = params.get(b"filename")
        if raw_name is None:
            return  # a plain form field: ignored
        name = PureWindowsPath(PurePosixPath(raw_name.decode("utf-8", "replace")).name).name
        if not name.lower().endswith(".zip"):
            self.error = ApiError(
                "invalid_upload", f"Only .zip files can be uploaded (got {name!r}).", 400
            )
            raise self.error
        target = self.dest / name
        n = 1
        while target.exists():
            target = self.dest / f"{Path(name).stem}-{n}.zip"
            n += 1
        self._fh = open(target, "wb")  # noqa: SIM115 - closed in on_part_end/close
        self.files.append(target)

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self._fh is not None:
            self._fh.write(data[start:end])
            self.bytes += end - start

    def on_part_end(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def close(self) -> None:
        self.on_part_end()

    def callbacks(self) -> Any:
        return {
            "on_part_begin": self.on_part_begin,
            "on_header_field": self.on_header_field,
            "on_header_value": self.on_header_value,
            "on_header_end": self.on_header_end,
            "on_headers_finished": self.on_headers_finished,
            "on_part_data": self.on_part_data,
            "on_part_end": self.on_part_end,
        }


_UPLOAD_OPENAPI: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "files": {
                            "type": "array",
                            "items": {"type": "string", "format": "binary"},
                        }
                    },
                    "required": ["files"],
                }
            }
        },
    }
}


@router.post(
    "/upload",
    response_model=ImportJobRef,
    openapi_extra=_UPLOAD_OPENAPI,
    summary="Upload one or more export zips (multipart, streamed to disk)",
)
async def upload(request: Request) -> ImportJobRef:
    content_type, params = parse_options_header(request.headers.get("content-type"))
    boundary = params.get(b"boundary")
    if content_type != b"multipart/form-data" or not boundary:
        raise ApiError("invalid_upload", "Expected a multipart/form-data upload.", 400)
    manager = get_job_manager()
    job = manager.create("upload", status="uploading")
    dest = uploads_dir(get_store().data_dir) / job.job_id
    job.upload_dir = dest
    job.update(stage="upload", message="Receiving files")
    writer = _UploadWriter(dest)
    try:
        dest.mkdir(parents=True, exist_ok=True)
        parser = MultipartParser(boundary, writer.callbacks())
        last_report = 0
        async for chunk in request.stream():
            if job.cancel_event.is_set():
                raise ApiError("cancelled", "The upload was cancelled.", 400)
            parser.write(chunk)
            if writer.bytes - last_report >= 8 * 2**20:
                last_report = writer.bytes
                job.update(message=f"Received {writer.bytes // 2**20} MB", messages=0)
        parser.finalize()
        writer.close()
        if not writer.files:
            raise ApiError("invalid_upload", "No zip file was uploaded.", 400)
    except BaseException as exc:
        writer.close()
        with contextlib.suppress(OSError):
            for f in writer.files:
                f.unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                dest.rmdir()
        if isinstance(exc, ApiError):
            if exc.code == "cancelled":
                manager.mark_cancelled(job)
            else:
                manager.fail(job, exc.code, exc.message)
            raise
        if isinstance(exc, ClientDisconnect):
            manager.fail(job, "upload_interrupted", "The upload was interrupted.")
            raise ApiError("upload_interrupted", "The upload was interrupted.", 400) from exc
        manager.fail(job, "invalid_upload", f"Could not read the upload: {exc}")
        if isinstance(exc, Exception):
            raise ApiError("invalid_upload", f"Could not read the upload: {exc}", 400) from exc
        raise
    files = list(writer.files)
    manager.start(job, lambda _job: files)
    return ImportJobRef(job_id=job.job_id)


# --------------------------------------------------------------------------------------
# Path / sample
# --------------------------------------------------------------------------------------


def _validate_path(raw: str) -> Path:
    candidate = Path(raw.strip()).expanduser()
    if not candidate.is_absolute():
        raise ApiError("path_not_absolute", f"Please enter an absolute path (got {raw!r}).", 400)
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ApiError("path_not_found", f"Path not found: {candidate}", 400) from exc
    if resolved.is_dir():
        if not os.access(resolved, os.R_OK | os.X_OK):
            raise ApiError("path_not_readable", f"Folder is not readable: {resolved}", 400)
    elif resolved.is_file():
        if not os.access(resolved, os.R_OK):
            raise ApiError("path_not_readable", f"File is not readable: {resolved}", 400)
        if resolved.suffix.lower() != ".zip":
            raise ApiError(
                "unsupported_path",
                "Enter a folder (unzipped export or a folder of zips) or a .zip file.",
                400,
            )
    else:
        raise ApiError("unsupported_path", f"Not a folder or a file: {resolved}", 400)
    return resolved


@router.post("/path", response_model=ImportJobRef, summary="Import a local folder or zip(s)")
def import_path(body: PathImportRequest) -> ImportJobRef:
    path = _validate_path(body.path)
    manager = get_job_manager()
    job = manager.create("path")
    manager.start(job, lambda _job: [path])
    return ImportJobRef(job_id=job.job_id)


@router.post("/sample", response_model=ImportJobRef, summary="Generate and import sample data")
def import_sample(body: SampleImportRequest | None = None) -> ImportJobRef:
    body = body or SampleImportRequest()
    manager = get_job_manager()
    job = manager.create("sample")
    manager.start(job, sample_sources(body.messages, body.seed))
    return ImportJobRef(job_id=job.job_id)


# --------------------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------------------


@router.get("/jobs/{job_id}", response_model=ImportJobState, summary="Current job state")
def job_state(job_id: str) -> ImportJobState:
    return _state(_job_or_404(job_id))


@router.delete("/jobs/{job_id}", response_model=ImportJobState, summary="Cancel an import")
def cancel_job(job_id: str) -> ImportJobState:
    _job_or_404(job_id)
    job = get_job_manager().cancel(job_id)
    assert job is not None
    return _state(job)


async def _job_events(request: Request, job: ImportJob) -> AsyncIterator[dict[str, str]]:
    last_seq = -1
    last_sent = 0.0
    loop = asyncio.get_running_loop()
    while True:
        snap = job.snapshot()
        now = loop.time()
        finished = snap["status"] not in ("uploading", "running")
        if finished:
            payload = ImportJobState.model_validate(snap).model_dump_json()
            yield {"event": str(snap["status"]), "data": payload}
            return
        if snap["seq"] != last_seq or now - last_sent >= _HEARTBEAT_S:
            last_seq = snap["seq"]
            last_sent = now
            yield {
                "event": "progress",
                "data": ImportJobState.model_validate(snap).model_dump_json(),
            }
        if await request.is_disconnected():
            return
        await asyncio.sleep(_POLL_S)


@router.get(
    "/jobs/{job_id}/events",
    summary="Server-Sent Events progress stream",
    response_class=EventSourceResponse,
    responses={
        200: {
            "description": "SSE stream: `progress` events, then one final `done`, `error` or "
            "`cancelled` event. Each `data` is an ImportJobState JSON object.",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
async def job_events(job_id: str, request: Request) -> EventSourceResponse:
    job = _job_or_404(job_id)
    return EventSourceResponse(_job_events(request, job), ping=15)


# --------------------------------------------------------------------------------------
# Report / uploads
# --------------------------------------------------------------------------------------


@router.get("/report", response_model=ImportReport, summary="Import report (§5.4)")
def import_report(ds: DatasetDep, settings: SettingsDep) -> ImportReport:
    raw = dict(ds.import_report)
    raw["dataset_version"] = ds.version
    fields = ImportReport.model_fields
    data = {k: v for k, v in raw.items() if k in fields}
    report = ImportReport.model_validate(data)
    if settings.anonymize:
        report.owner.name = "Me" if report.owner.name else None
        report.owner.auto_name = None
        report.owner.personal_information_name = None
        report.unmatched_system_like = []
        report.sources = []
        report.zip_files = []
        report.failed_threads = []
    return report


@router.delete("/uploads", response_model=DeleteUploadsResponse, summary="Delete kept upload zips")
def remove_uploads() -> DeleteUploadsResponse:
    result = delete_uploads()
    return DeleteUploadsResponse(
        deleted_files=result.deleted_files,
        freed_bytes=result.freed_bytes,
        media_marked_missing=result.media_marked_missing,
        dataset_version=result.dataset_version,
    )
