"""Voice-note durations read with ``mutagen`` (spec §6.8).

Durations are computed in a background thread (first request triggers it) and cached
in ``<data_dir>/audio_durations.json``::

    {"fingerprint": "<hash of the audio rows>", "durations": {"<media_id>": seconds}}

The fingerprint covers every audio row's ``media_id``/``source``/``uri``/``exists``, so
a re-import invalidates the cache automatically.
"""

import hashlib
import io
import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Literal

import mutagen
import polars as pl

from instagraph.media.files import MediaUnavailableError, read_bytes, resolve_media
from instagraph.store.dataset import Dataset

logger = logging.getLogger("instagraph")

CACHE_FILE = "audio_durations.json"
MAX_AUDIO_BYTES = 50 * 1024 * 1024

DurationStatus = Literal["ready", "pending", "unavailable"]

_lock = threading.Lock()
_running: set[str] = set()


def read_duration(data: bytes) -> float | None:
    """Duration in seconds of an audio/video file's bytes, or ``None``."""
    try:
        parsed: Any = mutagen.File(io.BytesIO(data))
    except Exception:
        return None
    if parsed is None or getattr(parsed, "info", None) is None:
        return None
    length = getattr(parsed.info, "length", None)
    if not isinstance(length, int | float) or length <= 0:
        return None
    return float(length)


def _audio_rows(ds: Dataset) -> pl.DataFrame:
    return (
        ds.media.filter(pl.col("kind") == "audio")
        .select("media_id", "source", "uri", "exists")
        .sort("media_id")
        .collect()
    )


def audio_fingerprint(ds: Dataset) -> str:
    h = hashlib.sha1()
    for row in _audio_rows(ds).iter_rows():
        h.update(repr(row).encode("utf-8"))
    return h.hexdigest()


def compute_audio_durations(ds: Dataset, data_dir: Path | None = None) -> dict[int, float]:
    """Read every existing audio file's duration; cache to ``audio_durations.json``
    in ``data_dir`` when given. Files that can't be read are skipped."""
    rows = _audio_rows(ds)
    durations: dict[int, float] = {}
    for media_id, source, uri, exists in rows.iter_rows():
        if not exists:
            continue
        try:
            resolved = resolve_media(source, uri)
            data = read_bytes(resolved, MAX_AUDIO_BYTES + 1)
        except MediaUnavailableError:
            continue
        if len(data) > MAX_AUDIO_BYTES:
            continue
        seconds = read_duration(data)
        if seconds is not None:
            durations[int(media_id)] = seconds
    if data_dir is not None:
        _write_cache(data_dir, audio_fingerprint(ds), durations)
    return durations


def _write_cache(data_dir: Path, fingerprint: str, durations: dict[int, float]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / CACHE_FILE
    tmp = target.with_name(f".{CACHE_FILE}.{uuid.uuid4().hex}.tmp")
    payload = {"fingerprint": fingerprint, "durations": {str(k): v for k, v in durations.items()}}
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, target)


def load_audio_durations(ds: Dataset, data_dir: Path) -> dict[int, float] | None:
    """Cached durations if the cache matches the current audio rows, else ``None``."""
    try:
        raw = json.loads((data_dir / CACHE_FILE).read_text(encoding="utf-8"))
        if raw.get("fingerprint") != audio_fingerprint(ds):
            return None
        return {int(k): float(v) for k, v in raw["durations"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _run(ds: Dataset, data_dir: Path, fingerprint: str) -> None:
    try:
        compute_audio_durations(ds, data_dir)
    except Exception:
        logger.exception("Computing voice-note durations failed")
    finally:
        with _lock:
            _running.discard(fingerprint)


def audio_durations_or_start(
    ds: Dataset, data_dir: Path, *, background: bool = True
) -> tuple[DurationStatus, dict[int, float]]:
    """Return ``("ready", durations)`` from the cache, or start the background job
    and return ``("pending", {})``. ``("unavailable", {})`` when no audio file exists.
    With ``background=False`` the durations are computed synchronously."""
    rows = _audio_rows(ds)
    if rows.is_empty() or not rows["exists"].any():
        return "unavailable", {}
    cached = load_audio_durations(ds, data_dir)
    if cached is not None:
        return "ready", cached
    if not background:
        return "ready", compute_audio_durations(ds, data_dir)
    fingerprint = audio_fingerprint(ds)
    with _lock:
        if fingerprint not in _running:
            _running.add(fingerprint)
            threading.Thread(
                target=_run, args=(ds, data_dir, fingerprint), daemon=True, name="audio-durations"
            ).start()
    return "pending", {}
