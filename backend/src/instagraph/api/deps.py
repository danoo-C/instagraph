"""Shared FastAPI dependencies.

Typical analysis route::

    from instagraph.api.deps import Context

    @router.get("/overview", response_model=OverviewResponse)
    def overview(ctx: Context) -> OverviewResponse:
        def compute() -> OverviewResponse:
            msgs = ctx.messages().filter(is_counted_message())
            ...
            return OverviewResponse(dataset_version=ctx.dataset_version, ...)
        return ctx.cached("overview", compute)

``Context`` raises :class:`~instagraph.errors.NoDataError` (409) when nothing is
imported. Use plain ``def`` routes for CPU-heavy work: FastAPI runs them in a thread
pool, keeping the event loop free (§8.3).
"""

import dataclasses
import string
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from datetime import date
from typing import Annotated, Any, TypeVar

import polars as pl
from fastapi import Depends, Query

from instagraph.api.filters import (
    FilterParams,
    filtered_messages,
    filtered_reactions,
    parse_people,
    validate_tz,
)
from instagraph.errors import NotFoundError
from instagraph.schemas.common import PersonRef
from instagraph.store.app_settings import AppSettings, get_app_settings
from instagraph.store.cache import make_key, response_cache
from instagraph.store.dataset import Dataset, DatasetStore, get_store

T = TypeVar("T")


def get_dataset_store() -> DatasetStore:
    return get_store()


def get_dataset(store: Annotated[DatasetStore, Depends(get_dataset_store)]) -> Dataset:
    """The loaded dataset; raises NoDataError (409) if none."""
    return store.get()


def get_current_settings() -> AppSettings:
    return get_app_settings()


def get_filters(
    settings: Annotated[AppSettings, Depends(get_current_settings)],
    start: Annotated[date | None, Query(description="Inclusive local start date")] = None,
    end: Annotated[date | None, Query(description="Inclusive local end date")] = None,
    tz: Annotated[str | None, Query(description="IANA time zone")] = None,
    include_groups: bool = True,
    include_requests: bool = False,
    include_archived: bool = True,
    exclude_system: bool = True,
    people: Annotated[str | None, Query(description="Comma-separated person ids")] = None,
) -> FilterParams:
    """Global filter query parameters (§8.2). ``tz`` falls back to the settings'
    timezone, then UTC."""
    return FilterParams(
        start=start,
        end=end,
        tz=validate_tz(tz or settings.timezone or "UTC"),
        include_groups=include_groups,
        include_requests=include_requests,
        include_archived=include_archived,
        exclude_system=exclude_system,
        people=parse_people(people),
    )


StoreDep = Annotated[DatasetStore, Depends(get_dataset_store)]
DatasetDep = Annotated[Dataset, Depends(get_dataset)]
SettingsDep = Annotated[AppSettings, Depends(get_current_settings)]
FiltersDep = Annotated[FilterParams, Depends(get_filters)]


def _anon_label(i: int) -> str:
    letters = string.ascii_uppercase
    label = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        label = letters[r] + label
    return f"Person {label}"


@dataclass
class AnalysisContext:
    """Everything an analysis route needs: dataset + resolved filters + settings."""

    dataset: Dataset
    filters: FilterParams
    settings: AppSettings
    _refs: dict[int, PersonRef] | None = field(default=None, repr=False)

    @property
    def dataset_version(self) -> int:
        return self.dataset.version

    @property
    def tz(self) -> str:
        return self.filters.tz

    @property
    def owner_id(self) -> int | None:
        """Owner override from settings, else the dataset's ``is_owner`` person."""
        if self.settings.owner_person_id is not None:
            return self.settings.owner_person_id
        return self.dataset.owner_id()

    def messages(self) -> pl.LazyFrame:
        """:func:`filtered_messages` for this request."""
        return filtered_messages(self.dataset, self.filters)

    def reactions(self) -> pl.LazyFrame:
        """:func:`filtered_reactions` for this request."""
        return filtered_reactions(self.dataset, self.filters)

    # ---- chat scope ------------------------------------------------------------------

    def thread_row(self, thread_id: int) -> dict[str, Any] | None:
        """The ``threads`` row of ``thread_id`` as a dict (O(1)), or ``None``."""
        return self.dataset.thread_rows().get(thread_id)

    def ensure_chat(self, chat_id: int) -> dict[str, Any]:
        """The ``threads`` row of ``chat_id``; raises NotFoundError (404) if unknown."""
        row = self.thread_row(chat_id)
        if row is None:
            raise NotFoundError(f"Chat {chat_id} not found.")
        return row

    def chat_filters(self) -> FilterParams:
        """Request filters for a chat-scoped endpoint: the thread-category filters
        (groups / requests / archived) are opened — a chat the user opened always shows
        its data. Date range, tz, ``exclude_system`` and ``people`` still apply."""
        return self.filters.model_copy(
            update={"include_groups": True, "include_requests": True, "include_archived": True}
        )

    def for_chat(self, chat_id: int) -> "AnalysisContext":
        """A copy of this context scoped to ``chat_id`` (404 if unknown).

        Its ``filters`` are :meth:`chat_filters`, so ``messages()``, ``reactions()``,
        ``cached()`` (cache key) and any helper fed with ``ctx.filters`` behave
        consistently for the chat. Every ``/api/chats/{chat_id}/...`` route starts with
        ``ctx = ctx.for_chat(chat_id)`` (or uses :meth:`chat_messages`)."""
        self.ensure_chat(chat_id)
        return dataclasses.replace(self, filters=self.chat_filters(), _refs=self._refs)

    def chat_messages(self, chat_id: int, *, people: bool = True) -> pl.LazyFrame:
        """:func:`filtered_messages` of one chat with chat-scope filters (404 if unknown).

        ``people=False`` drops the people filter (e.g. conversation dynamics)."""
        self.ensure_chat(chat_id)
        f = self.chat_filters()
        if not people:
            f = f.model_copy(update={"people": None})
        return filtered_messages(self.dataset, f, thread_id=chat_id)

    def chat_reactions(self, chat_id: int) -> pl.LazyFrame:
        """:func:`filtered_reactions` of one chat with chat-scope filters (404 if unknown)."""
        f = self.chat_filters()
        msgs = self.chat_messages(chat_id, people=False)
        return filtered_reactions(self.dataset, f, messages=msgs)

    # ---- people / titles -------------------------------------------------------------

    def person_refs(self) -> dict[int, PersonRef]:
        """``person_id -> PersonRef`` for everyone (anonymized if the setting is on)."""
        if self._refs is None:
            colors = self.dataset.color_indices()
            owner = self.owner_id
            refs: dict[int, PersonRef] = {}
            for pid, name in self.dataset.person_names().items():
                color = colors.get(pid, len(colors))
                if self.settings.anonymize:
                    name = "Me" if pid == owner else _anon_label(color)
                refs[pid] = PersonRef(
                    person_id=pid, name=name, color_index=color, is_owner=pid == owner
                )
            self._refs = refs
        return self._refs

    def person_ref(self, person_id: int) -> PersonRef:
        ref = self.person_refs().get(person_id)
        if ref is None:
            return PersonRef(person_id=person_id, name=f"#{person_id}", color_index=0)
        return ref

    def thread_titles(self) -> dict[int, str]:
        """``thread_id -> title`` for every thread (anonymized: participants'
        anonymized names). Cached per dataset version + settings."""

        def compute() -> dict[int, str]:
            owner = self.owner_id
            out: dict[int, str] = {}
            for tid, row in self.dataset.thread_rows().items():
                if not self.settings.anonymize:
                    out[tid] = str(row["title"] or row["folder"])
                    continue
                others = [p for p in row["participants"] or [] if p != owner]
                out[tid] = ", ".join(self.person_ref(p).name for p in others) or f"Chat #{tid}"
            return out

        key = make_key(self.dataset_version, "__thread_titles__", "", self.settings.settings_hash())
        return response_cache.get_or_compute(key, compute)

    def thread_title(self, thread_id: int) -> str:
        """Chat title (anonymized: participants' anonymized names). O(1)."""
        return self.thread_titles().get(thread_id, f"Chat #{thread_id}")

    def text(self, value: str | None) -> str | None:
        """Message text for the response (``None`` when anonymize is on)."""
        return None if self.settings.anonymize else value

    def cached(self, endpoint: str, compute: Callable[[], T], *, extra: Hashable = None) -> T:
        """Return ``compute()`` through the LRU cache keyed by
        (dataset_version, endpoint, filters, settings hash, extra)."""
        key = make_key(
            self.dataset_version,
            endpoint,
            self.filters.cache_key(),
            self.settings.settings_hash(),
            extra,
        )
        return response_cache.get_or_compute(key, compute)


def get_context(dataset: DatasetDep, filters: FiltersDep, settings: SettingsDep) -> AnalysisContext:
    return AnalysisContext(dataset=dataset, filters=filters, settings=settings)


Context = Annotated[AnalysisContext, Depends(get_context)]
