"""Reference route module: ``GET /api/status`` and ``DELETE /api/data``.

Route-module conventions (copy this file's shape):

* Define a module-level ``router = APIRouter(prefix="/api/<area>", tags=[...])``;
  it is auto-included by ``create_app`` — never edit ``main.py``.
* Every endpoint declares a typed Pydantic ``response_model`` (from
  ``instagraph.schemas.<area>``) so the TS types can be generated.
* Get data through dependencies from ``instagraph.api.deps``. ``Context`` / ``DatasetDep``
  raise ``NoDataError`` (409) automatically when nothing is imported; endpoints that
  must work without data (like status) use ``StoreDep`` and ``peek()``.
* Raise ``ApiError`` subclasses for errors, never return error dicts.
"""

import shutil

import polars as pl
from fastapi import APIRouter

from instagraph.api.deps import SettingsDep, StoreDep
from instagraph.errors import ERROR_RESPONSES
from instagraph.schemas.common import PersonRef
from instagraph.schemas.status import DatasetCounts, StatusResponse
from instagraph.store.app_settings import get_settings_store

router = APIRouter(prefix="/api", tags=["status"], responses=ERROR_RESPONSES)


def _status(store: StoreDep, anonymize: bool) -> StatusResponse:
    ds = store.peek()
    if ds is None:
        return StatusResponse(has_data=False, dataset_version=store.version)
    counts = DatasetCounts(**ds.counts())
    span = ds.messages.select(pl.col("ts").min().alias("a"), pl.col("ts").max().alias("b"))
    first, last = span.collect().row(0)
    owner: PersonRef | None = None
    owner_id = ds.owner_id()
    if owner_id is not None:
        name = "Me" if anonymize else ds.person_names().get(owner_id, f"#{owner_id}")
        owner = PersonRef(person_id=owner_id, name=name, color_index=0, is_owner=True)
    return StatusResponse(
        has_data=True,
        dataset_version=ds.version,
        counts=counts,
        owner=owner,
        first_ts=first,
        last_ts=last,
    )


@router.get("/status", response_model=StatusResponse)
def get_status(store: StoreDep, settings: SettingsDep) -> StatusResponse:
    """Whether data is loaded, the dataset version and table counts."""
    return _status(store, settings.anonymize)


@router.delete("/data", response_model=StatusResponse)
def delete_data(store: StoreDep) -> StatusResponse:
    """Clear all data: dataset, settings, uploads, thumbnails (keeps ``.gitignore``)."""
    store.clear(delete_files=True)
    get_settings_store().reset()
    for child in store.data_dir.iterdir():
        if child.name in {".gitignore", "version.json"}:
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
    return _status(store, anonymize=False)
