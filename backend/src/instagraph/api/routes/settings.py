"""Settings endpoints (spec §5.11): ``GET``/``PUT /api/settings`` plus helper lists.

``PUT`` accepts a full or partial document. Changes to derived-field settings
(:data:`DERIVED_FIELDS`) re-derive the dataset via
``instagraph.importer.pipeline.rederive(dataset, settings)`` and install the result;
other stat-affecting changes only bump the dataset version (query-time settings are
part of every cache key anyway); UI-only changes (theme, UI language) bump nothing.

Anonymize mode: name-bearing settings (:data:`REDACTED_FIELDS`) are hidden in
responses; a PUT that sends the hidden placeholder values back leaves them unchanged.
"""

import importlib
import logging
from collections.abc import Callable
from typing import Any, cast

import polars as pl
import regex
from fastapi import APIRouter

from instagraph.api.deps import Context, StoreDep
from instagraph.api.filters import validate_tz
from instagraph.errors import ERROR_RESPONSES, ApiError, ValidationApiError
from instagraph.schemas.settings import (
    SettingsPeople,
    SettingsPerson,
    SettingsResponse,
    SettingsThread,
    SettingsThreads,
    SettingsUpdate,
)
from instagraph.store.app_settings import UI_ONLY_FIELDS, AppSettings, get_settings_store
from instagraph.store.dataset import Dataset, DatasetStore

logger = logging.getLogger("instagraph")

router = APIRouter(prefix="/api/settings", tags=["settings"], responses=ERROR_RESPONSES)

#: Settings whose change requires re-deriving dataset columns.
DERIVED_FIELDS: frozenset[str] = frozenset(
    {
        "owner_person_id",
        "owner_name",
        "people_merges",
        "thread_merges",
        "thread_splits",
        "session_gap_min",
        "reply_window_h",
    }
)
#: Settings that may contain real names; hidden while anonymize is on.
REDACTED_FIELDS: tuple[str, ...] = (
    "owner_name",
    "people_merges",
    "thread_merges",
    "thread_splits",
    "custom_terms",
)
HIDDEN = "•••"

Rederive = Callable[[Dataset, AppSettings], Dataset]


def _load_rederive() -> Rederive | None:
    """The importer's ``rederive`` (imported lazily; ``None`` if unavailable)."""
    try:
        module = importlib.import_module("instagraph.importer.pipeline")
    except ImportError:
        return None
    fn = getattr(module, "rederive", None)
    return cast(Rederive, fn) if callable(fn) else None


def redact(settings: AppSettings) -> dict[str, Any]:
    """JSON dump of ``settings`` with name-bearing fields replaced by placeholders."""
    data = settings.model_dump(mode="json")
    if data["owner_name"] is not None:
        data["owner_name"] = HIDDEN
    data["people_merges"] = [
        {
            "names": [HIDDEN] * len(m["names"]),
            "display_name": None if m["display_name"] is None else HIDDEN,
        }
        for m in data["people_merges"]
    ]
    for key in ("thread_merges", "thread_splits"):
        data[key] = [{"folders": [HIDDEN] * len(m["folders"])} for m in data[key]]
    data["custom_terms"] = {}
    return data


def _response(settings: AppSettings, version: int, *, rederived: bool = False) -> SettingsResponse:
    if settings.anonymize:
        shown = AppSettings.model_validate(redact(settings))
        return SettingsResponse(
            settings=shown, dataset_version=version, rederived=rederived, redacted=True
        )
    return SettingsResponse(settings=settings, dataset_version=version, rederived=rederived)


def _validate(new: AppSettings, store: DatasetStore) -> None:
    if new.timezone is not None:
        validate_tz(new.timezone)
    for pattern in new.laughter_patterns:
        try:
            regex.compile(pattern)
        except regex.error as exc:
            raise ValidationApiError(f"Invalid laughter pattern {pattern!r}: {exc}") from exc
    ds = store.peek()
    if new.owner_person_id is not None and ds is not None:
        ids = ds.people_df()["person_id"]
        if not ids.is_in([new.owner_person_id]).any():
            raise ValidationApiError(f"Unknown owner_person_id {new.owner_person_id}")


@router.get("", response_model=SettingsResponse)
def get_settings_doc(store: StoreDep) -> SettingsResponse:
    """Current settings (works without data)."""
    return _response(get_settings_store().get(), store.version)


@router.put("", response_model=SettingsResponse)
def put_settings(body: SettingsUpdate, store: StoreDep) -> SettingsResponse:
    """Full or partial update; returns the new settings and whether data was re-derived.

    Runs in the thread pool (plain ``def``), so a re-derivation doesn't block the loop.
    """
    settings_store = get_settings_store()
    current = settings_store.get()
    patch = body.model_dump(mode="json", exclude_unset=True)
    if current.anonymize:
        hidden = redact(current)
        for key in REDACTED_FIELDS:
            if key in patch and patch[key] == hidden[key]:
                del patch[key]
    merged = current.model_dump(mode="json") | patch
    try:
        new = AppSettings.model_validate(merged)
    except ValueError as exc:
        raise ValidationApiError(str(exc)) from exc
    _validate(new, store)

    changed = {k for k in AppSettings.model_fields if getattr(new, k) != getattr(current, k)}
    if not changed:
        return _response(current, store.version)
    settings_store.update(new)

    ds = store.peek()
    rederived = False
    if ds is not None and changed & DERIVED_FIELDS:
        fn = _load_rederive()
        if fn is None:
            store.bump_version()
        else:
            try:
                new_ds = fn(ds, new)
            except ApiError:  # e.g. 409 reimport_required: report it, keep old settings
                settings_store.update(current)
                raise
            except Exception:
                settings_store.update(current)
                logger.exception("Re-derivation failed; settings change reverted")
                raise
            installed = store.replace(new_ds)
            rederived = True
            # Person ids may be reassigned by identity changes: keep the override valid.
            new_owner = installed.owner_id()
            if new.owner_person_id is not None and new_owner not in (None, new.owner_person_id):
                new = settings_store.update(new.model_copy(update={"owner_person_id": new_owner}))
    elif changed - UI_ONLY_FIELDS:
        store.bump_version()
    return _response(new, store.version, rederived=rederived)


@router.get("/people", response_model=SettingsPeople)
def settings_people(ctx: Context) -> SettingsPeople:
    """Everyone with aliases and total message counts (for owner / merge controls)."""
    ds = ctx.dataset
    counts = ds.messages.group_by("sender_id").agg(pl.len().alias("n")).collect()
    n_by = dict(zip(counts["sender_id"].to_list(), counts["n"].to_list(), strict=True))
    anon = ctx.settings.anonymize
    items: list[SettingsPerson] = []
    for row in ds.people_df().sort("person_id").iter_rows(named=True):
        ref = ctx.person_ref(row["person_id"])
        items.append(
            SettingsPerson(
                person=ref,
                display_name=ref.name if anon else row["display_name"],
                aliases=[] if anon else list(row["aliases"] or []),
                message_count=int(n_by.get(row["person_id"], 0)),
                is_deleted_account=bool(row["is_deleted_account"]),
            )
        )
    items.sort(key=lambda p: (-p.message_count, p.person.person_id))
    return SettingsPeople(dataset_version=ctx.dataset_version, items=items)


@router.get("/threads", response_model=SettingsThreads)
def settings_threads(ctx: Context) -> SettingsThreads:
    """All chats with folders and participants (for thread merge / split controls)."""
    anon = ctx.settings.anonymize
    items: list[SettingsThread] = []
    for row in ctx.dataset.threads_df().sort("thread_id").iter_rows(named=True):
        items.append(
            SettingsThread(
                thread_id=row["thread_id"],
                title=ctx.thread_title(row["thread_id"]),
                folder=None if anon else row["folder"],
                category=row["category"],
                is_group=bool(row["is_group"]),
                message_count=int(row["message_count"] or 0),
                participants=[ctx.person_ref(p) for p in row["participants"] or []],
            )
        )
    items.sort(key=lambda t: (-t.message_count, t.thread_id))
    return SettingsThreads(dataset_version=ctx.dataset_version, items=items)
