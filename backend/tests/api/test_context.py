"""``AnalysisContext`` foundation helpers: chat scope and O(1) thread titles."""

import polars as pl
import pytest

from instagraph.api.deps import AnalysisContext
from instagraph.api.filters import FilterParams
from instagraph.errors import NotFoundError
from instagraph.store.app_settings import AppSettings
from instagraph.store.cache import response_cache
from instagraph.store.dataset import Dataset


def _ctx(ds: Dataset, **settings: object) -> AnalysisContext:
    return AnalysisContext(dataset=ds, filters=FilterParams(), settings=AppSettings(**settings))


def test_thread_title_cached_and_anonymize_aware(
    small_dataset: Dataset, monkeypatch: pytest.MonkeyPatch
) -> None:
    response_cache.clear()
    ctx = _ctx(small_dataset)
    assert ctx.thread_title(1) == "Partia"
    assert ctx.thread_title(99) == "Chat #99"

    def boom() -> None:
        raise AssertionError("threads_df must not be scanned per call")

    monkeypatch.setattr(small_dataset, "threads_df", boom)
    assert [ctx.thread_title(t) for t in (0, 1, 2)] == ["Jana Nováková", "Partia", "Spammer"]
    anon = _ctx(small_dataset, anonymize=True)
    title = anon.thread_title(1)
    assert "Partia" not in title and "Jana" not in title and title.startswith("Person ")
    assert ctx.thread_title(1) == "Partia"  # separate cache entry per settings


def test_chat_scope_helpers(small_dataset: Dataset) -> None:
    ctx = _ctx(small_dataset)  # include_requests=False by default
    assert ctx.messages().filter(pl.col("thread_id") == 2).collect().height == 0
    assert ctx.chat_messages(2).collect().height == 1
    assert ctx.chat_reactions(0).collect().height == 2
    scoped = ctx.for_chat(2)
    assert scoped.filters.include_requests and scoped.filters.tz == ctx.filters.tz
    with pytest.raises(NotFoundError):
        ctx.chat_messages(99)
    with pytest.raises(NotFoundError):
        ctx.for_chat(99)
