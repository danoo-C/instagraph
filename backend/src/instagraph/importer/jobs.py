"""Background import jobs (spec §5.2, §8.3): one import at a time.

A job runs :func:`instagraph.importer.pipeline.run_import_full` in a daemon thread
(parsing itself uses a process pool), publishes progress into the job state and, on
success, installs the dataset with ``get_store().replace(ds)`` and writes the raw
identity sidecar used by :func:`rederive_installed`.

The SSE endpoint polls :meth:`ImportJob.snapshot` (cheap) and sends an update whenever
``seq`` changes, plus a heartbeat at least every 0.5 s while the job is active — so the
client sees ≥ 2 updates/s however long a single pipeline step takes.

Kept uploads (decision D2) live in ``<data_dir>/uploads/<job_id>/``; a later successful
import deletes the previous jobs' upload folders (the old dataset that referenced them
is gone).
"""

import contextlib
import logging
import shutil
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import polars as pl

from instagraph.errors import ApiError
from instagraph.importer.pipeline import (
    ImportCancelled,
    ImportFailed,
    ProgressEvent,
    rederive,
    run_import_full,
    save_raw,
)
from instagraph.store.app_settings import AppSettings, get_app_settings
from instagraph.store.dataset import Dataset, DatasetStore, get_store

logger = logging.getLogger("instagraph")

JobStatus = Literal["uploading", "running", "done", "error", "cancelled"]
JobKind = Literal["upload", "path", "sample"]
ACTIVE_STATUSES: frozenset[str] = frozenset({"uploading", "running"})
UPLOADS_DIRNAME = "uploads"
SAMPLE_DIRNAME = "sample"


@dataclass
class ImportJob:
    job_id: str
    kind: JobKind
    store: DatasetStore
    status: JobStatus = "running"
    stage: str = "queued"
    done: int = 0
    total: int = 0
    message: str = ""
    messages: int = 0
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    dataset_version: int | None = None
    upload_dir: Path | None = None
    seq: int = 0
    cancel_event: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    def update(self, **changes: Any) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(self, key, value)
            self.seq += 1

    def on_progress(self, event: ProgressEvent) -> None:
        self.update(
            stage=event.stage,
            done=event.done,
            total=event.total,
            message=event.message,
            messages=max(self.messages, event.messages),
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "job_id": self.job_id,
                "kind": self.kind,
                "status": self.status,
                "stage": self.stage,
                "done": self.done,
                "total": self.total,
                "message": self.message,
                "messages": self.messages,
                "error": (
                    {"code": self.error_code, "message": self.error_message or ""}
                    if self.error_code
                    else None
                ),
                "created_at": self.created_at,
                "finished_at": self.finished_at,
                "dataset_version": self.dataset_version,
                "seq": self.seq,
            }


SourcesFactory = Callable[[ImportJob], list[Path]]


class JobManager:
    """Process-wide registry of import jobs; at most one active job."""

    def __init__(self, *, keep: int = 20) -> None:
        self._jobs: dict[str, ImportJob] = {}
        self._lock = threading.Lock()
        self._keep = keep
        #: Parse worker processes; ``None`` = ``config.get_settings().workers``.
        self.workers: int | None = None

    # ---- registry ----------------------------------------------------------------------

    def create(self, kind: JobKind, *, status: JobStatus = "running") -> ImportJob:
        """Register a new job; ``409 import_running`` if another one is active."""
        with self._lock:
            running = self.active_job()
            if running is not None:
                raise ApiError(
                    "import_running",
                    f"An import is already running (job {running.job_id}).",
                    409,
                )
            job = ImportJob(job_id=uuid.uuid4().hex[:16], kind=kind, store=get_store())
            job.status = status
            self._jobs[job.job_id] = job
            finished = [j for j in self._jobs.values() if not j.active]
            for old in finished[: max(0, len(self._jobs) - self._keep)]:
                self._jobs.pop(old.job_id, None)
            return job

    def active_job(self) -> ImportJob | None:
        return next((j for j in self._jobs.values() if j.active), None)

    def get(self, job_id: str) -> ImportJob | None:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> ImportJob | None:
        job = self.get(job_id)
        if job is not None and job.active:
            job.cancel_event.set()
            if job.status == "uploading":
                # The upload handler notices the flag on its next chunk.
                job.update(message="Cancelling")
        return job

    def fail(self, job: ImportJob, code: str, message: str) -> None:
        job.update(
            status="error",
            error_code=code,
            error_message=message,
            finished_at=datetime.now(UTC),
        )

    def mark_cancelled(self, job: ImportJob) -> None:
        job.update(status="cancelled", message="Import cancelled", finished_at=datetime.now(UTC))

    # ---- running -------------------------------------------------------------------------

    def start(self, job: ImportJob, sources: SourcesFactory) -> None:
        """Run the import for ``job`` in a background thread."""
        job.update(status="running", stage="queued", message="Starting import")
        thread = threading.Thread(
            target=self._run, args=(job, sources), name=f"import-{job.job_id}", daemon=True
        )
        thread.start()

    def _workers(self) -> int:
        if self.workers is not None:
            return self.workers
        from instagraph.config import get_settings

        return get_settings().workers

    def _run(self, job: ImportJob, sources_factory: SourcesFactory) -> None:
        settings = get_app_settings()
        try:
            sources = sources_factory(job)
            if job.cancel_event.is_set():
                raise ImportCancelled()
            result = run_import_full(
                sources,
                settings=settings,
                progress=job.on_progress,
                cancel=job.cancel_event,
                workers=self._workers(),
            )
            if job.cancel_event.is_set():
                raise ImportCancelled()
            job.update(stage="persist", done=0, total=1, message="Saving dataset")
            store = job.store
            report = result.dataset.import_report
            report["job_id"] = job.job_id
            report["kind"] = job.kind
            report["kept_uploads"] = job.kind == "upload"
            installed = store.replace(result.dataset)
            save_raw(store.data_dir, result.raw)
            _cleanup_old_uploads(store.data_dir, keep=job.upload_dir)
            job.update(
                status="done",
                stage="done",
                done=1,
                total=1,
                message=f"Imported {report.get('messages', 0)} messages",
                dataset_version=installed.version,
                finished_at=datetime.now(UTC),
            )
        except ImportCancelled:
            self._discard_upload(job)
            self.mark_cancelled(job)
        except ImportFailed as exc:
            self._discard_upload(job)
            self.fail(job, exc.code, exc.message)
        except Exception as exc:
            logger.exception("Import job %s failed", job.job_id)
            self._discard_upload(job)
            self.fail(job, "import_failed", f"{type(exc).__name__}: {exc}")

    def _discard_upload(self, job: ImportJob) -> None:
        if job.upload_dir is not None:
            shutil.rmtree(job.upload_dir, ignore_errors=True)


_manager: JobManager | None = None
_manager_lock = threading.Lock()


def get_job_manager() -> JobManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = JobManager()
        return _manager


# --------------------------------------------------------------------------------------
# Uploads and sample data
# --------------------------------------------------------------------------------------


def uploads_dir(data_dir: Path) -> Path:
    return data_dir / UPLOADS_DIRNAME


def _close_zip_handles() -> None:
    with contextlib.suppress(ImportError):
        from instagraph.importer.discovery import close_zip_cache

        close_zip_cache()


def _cleanup_old_uploads(data_dir: Path, keep: Path | None) -> None:
    """Delete upload folders of earlier jobs (and the old sample export)."""
    root = uploads_dir(data_dir)
    if root.is_dir():
        for child in root.iterdir():
            if keep is not None and keep.resolve().is_relative_to(child.resolve()):
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)


@dataclass(slots=True)
class UploadsDeleted:
    deleted_files: int
    freed_bytes: int
    media_marked_missing: int
    dataset_version: int


def delete_uploads(store: DatasetStore | None = None) -> UploadsDeleted:
    """Delete kept upload zips; media rows that pointed into them get ``exists=False``
    (stats are kept) and the dataset is persisted again."""
    manager = get_job_manager()
    if manager.active_job() is not None:
        raise ApiError("import_running", "An import is running; try again when it is done.", 409)
    store = store or get_store()
    root = uploads_dir(store.data_dir)
    files = 0
    freed = 0
    _close_zip_handles()
    if root.is_dir():
        for path in root.rglob("*"):
            if path.is_file():
                files += 1
                with contextlib.suppress(OSError):
                    freed += path.stat().st_size
        shutil.rmtree(root, ignore_errors=True)
    marked = 0
    ds = store.peek()
    if ds is not None:
        prefix = str(root.resolve())
        media = ds.media.collect()
        in_uploads = pl.col("source").str.starts_with(prefix + "/") | pl.col(
            "source"
        ).str.starts_with(prefix + "\\")
        marked = int(media.filter(in_uploads & pl.col("exists")).height)
        if marked:
            media = media.with_columns(
                pl.when(in_uploads).then(False).otherwise(pl.col("exists")).alias("exists")
            )
            report = dict(ds.import_report)
            report["kept_uploads"] = False
            report["media_found"] = int(media["exists"].sum())
            new = Dataset.from_frames(
                people=ds.people.collect(),
                threads=ds.threads.collect(),
                messages=ds.messages.collect(),
                reactions=ds.reactions.collect(),
                media=media,
                import_report=report,
            )
            ds = store.replace(new)
    return UploadsDeleted(
        deleted_files=files,
        freed_bytes=freed,
        media_marked_missing=marked,
        dataset_version=store.version,
    )


def sample_sources(messages: int, seed: int) -> SourcesFactory:
    """Sources factory that writes a fake export to ``<data_dir>/uploads/<job>/sample``."""

    def factory(job: ImportJob) -> list[Path]:
        from instagraph.sample.generator import generate_export

        job.update(stage="sample", message=f"Generating {messages} sample messages")
        out = uploads_dir(job.store.data_dir) / job.job_id / SAMPLE_DIRNAME
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True, exist_ok=True)
        job.upload_dir = out
        try:
            return [generate_export(out, messages=messages, seed=seed, with_media_files=True)]
        except Exception as exc:
            logger.exception("Sample generation failed")
            raise ImportFailed(
                "sample_failed", f"Could not generate sample data: {type(exc).__name__}: {exc}"
            ) from exc

    return factory


# --------------------------------------------------------------------------------------
# Settings changes
# --------------------------------------------------------------------------------------


def rederive_installed(settings: AppSettings | None = None) -> Dataset | None:
    """Re-derive the installed dataset after a settings change and install it.

    Returns the new dataset (``None`` when nothing is imported). Raises
    ``ApiError("import_running", 409)`` during an import and
    ``ImportFailed("reimport_required", 409)`` when merges changed but the raw sidecar
    is missing.
    """
    if get_job_manager().active_job() is not None:
        raise ApiError("import_running", "An import is running; try again when it is done.", 409)
    store = get_store()
    ds = store.peek()
    if ds is None:
        return None
    new = rederive(ds, settings or get_app_settings(), data_dir=store.data_dir)
    return store.replace(new)
