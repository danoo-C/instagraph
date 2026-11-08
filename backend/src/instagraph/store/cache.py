"""LRU cache for computed API responses (spec §8.3).

Keys are ``(dataset_version, endpoint, filters_key, settings_hash, extra)``. Because the
dataset version and settings hash are part of every key, stale entries are never
returned; :meth:`ResponseCache.clear` (called by ``DatasetStore.replace``) just frees
memory.

Routes normally use ``AnalysisContext.cached`` from :mod:`instagraph.api.deps`::

    return ctx.cached("chats.summary", lambda: compute_summary(ctx, chat_id), extra=chat_id)

Lower-level use::

    key = make_key(ds.version, "overview", filters.cache_key(), settings.settings_hash())
    value = response_cache.get_or_compute(key, compute)

Cached values are shared between requests: treat them as immutable.
"""

import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Any, TypeVar, cast

T = TypeVar("T")

CacheKey = tuple[int, str, str, str, Hashable]


def make_key(
    dataset_version: int,
    endpoint: str,
    filters_key: str = "",
    settings_hash: str = "",
    extra: Hashable = None,
) -> CacheKey:
    """Build a cache key. ``extra`` holds endpoint-specific params (ids, sort, query…)."""
    return (dataset_version, endpoint, filters_key, settings_hash, extra)


class ResponseCache:
    """A small thread-safe LRU mapping ``CacheKey -> value``."""

    def __init__(self, maxsize: int = 512) -> None:
        self.maxsize = maxsize
        self._data: OrderedDict[CacheKey, Any] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: CacheKey) -> Any | None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self.hits += 1
                return self._data[key]
            self.misses += 1
            return None

    def set(self, key: CacheKey, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def get_or_compute(self, key: CacheKey, compute: Callable[[], T]) -> T:
        """Return the cached value for ``key`` or compute, store and return it.

        ``compute`` runs outside the lock (concurrent misses may compute twice; the
        result is identical, so that is harmless).
        """
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self.hits += 1
                return cast(T, self._data[key])
            self.misses += 1
        value = compute()
        self.set(key, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


#: Process-wide response cache.
response_cache = ResponseCache()
