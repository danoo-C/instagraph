"""Chat allowlist for the AI assistant (docs/ai-assistant.md A5.5).

The AI never sees the real dataset. It gets a *scoped* :class:`Dataset` that only
contains the allowed threads, their messages, reactions and media, and the people who
appear in them (plus the owner). Every existing analysis then works unchanged and can't
reach anything else: aggregates (overview totals, people, social graph) only include
allowed chats, and a person who appears only in other chats doesn't exist.

The scoped dataset gets its own ``version`` (far above any real version), so its
results never share response-cache entries with the main app.
"""

from collections import OrderedDict
from collections.abc import Iterable
from datetime import date
from threading import Lock

import polars as pl

from instagraph.api.deps import AnalysisContext
from instagraph.api.filters import FilterParams, validate_tz
from instagraph.store.app_settings import AppSettings
from instagraph.store.dataset import Dataset

#: Scoped versions live above this value; real dataset versions are small counters.
SCOPED_VERSION_BASE = 1 << 62
_MAX_CACHED = 8

_cache: "OrderedDict[tuple[int, frozenset[int]], Dataset]" = OrderedDict()
_cache_lock = Lock()


def resolve_scope(ds: Dataset, folders: Iterable[str]) -> frozenset[int]:
    """Thread ids for the given thread folder keys (unknown folders are ignored).

    Folder keys survive re-imports, unlike thread ids. A folder that was split into
    several chats resolves to all of them.
    """
    wanted = set(folders)
    if not wanted:
        return frozenset()
    df = ds.threads_df().filter(pl.col("folder").is_in(list(wanted)))
    return frozenset(int(t) for t in df["thread_id"].to_list())


def folders_for(ds: Dataset, thread_ids: Iterable[int]) -> list[str]:
    """Folder keys for thread ids (the stored form of a scope), sorted."""
    rows = ds.thread_rows()
    return sorted({str(rows[t]["folder"]) for t in thread_ids if t in rows})


def scoped_version(ds: Dataset, thread_ids: frozenset[int]) -> int:
    """Deterministic version for (dataset version, scope); never equals a real one."""
    return SCOPED_VERSION_BASE + (hash((ds.version, thread_ids)) & ((1 << 61) - 1))


def scoped_dataset(ds: Dataset, thread_ids: frozenset[int]) -> Dataset:
    """A dataset restricted to ``thread_ids`` (cached per dataset version + scope)."""
    key = (ds.version, thread_ids)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit

    ids = sorted(thread_ids)
    threads = ds.threads.filter(pl.col("thread_id").is_in(ids))
    messages = ds.messages.filter(pl.col("thread_id").is_in(ids))
    msg_ids = messages.select("message_id")
    reactions = ds.reactions.join(msg_ids, on="message_id", how="semi")
    media = ds.media.join(msg_ids, on="message_id", how="semi")

    people_ids: set[int] = set()
    for row in threads.collect().iter_rows(named=True):
        people_ids.update(int(p) for p in row["participants"] or [])
    people_ids.update(
        int(p) for p in messages.select("sender_id").unique().collect()["sender_id"].to_list()
    )
    people_ids.update(
        int(p) for p in reactions.select("actor_id").unique().collect()["actor_id"].to_list()
    )
    owner = ds.owner_id()
    if owner is not None:
        people_ids.add(owner)
    people = ds.people.filter(pl.col("person_id").is_in(sorted(people_ids)))

    scoped = Dataset(
        people=people.collect(),
        threads=threads.collect(),
        messages=messages,
        reactions=reactions,
        media=media,
        import_report={},
        version=scoped_version(ds, thread_ids),
    )
    with _cache_lock:
        _cache[key] = scoped
        _cache.move_to_end(key)
        while len(_cache) > _MAX_CACHED:
            _cache.popitem(last=False)
    return scoped


def ai_context(
    ds: Dataset,
    settings: AppSettings,
    thread_ids: frozenset[int],
    *,
    start: date | None = None,
    end: date | None = None,
) -> AnalysisContext:
    """An :class:`AnalysisContext` over the scoped dataset.

    The allowlist *is* the chat selection, so the thread-category filters (groups,
    requests, archived) are all open. System messages stay excluded, like in the app.
    """
    filters = FilterParams(
        start=start,
        end=end,
        tz=validate_tz(settings.timezone or "UTC"),
        include_groups=True,
        include_requests=True,
        include_archived=True,
        exclude_system=True,
        people=None,
    )
    return AnalysisContext(
        dataset=scoped_dataset(ds, thread_ids), filters=filters, settings=settings
    )
