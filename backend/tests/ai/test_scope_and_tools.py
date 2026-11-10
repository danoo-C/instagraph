"""AI chat allowlist (docs/ai-assistant.md A5.5) and read-only tools (A5.2-A5.3).

Fixture data (tests/conftest.py): thread 0 = 1:1 Me <-> Jana, thread 1 = group "Partia"
(Me, Jana, Peter, Eva), thread 2 = request from Spammer. Peter, Eva only appear in
thread 1; Spammer only in thread 2.
"""

import json
from typing import Any

import pytest

from instagraph.ai.scope import SCOPED_VERSION_BASE, ai_context, resolve_scope, scoped_dataset
from instagraph.ai.tools import (
    MAX_RESULT_CHARS,
    NO_SCOPE_MESSAGE,
    TOOLS,
    ToolBox,
    compact,
    decode_args,
    fit,
)
from instagraph.api.deps import AnalysisContext
from instagraph.api.filters import FilterParams
from instagraph.api.routes import overview
from instagraph.store.app_settings import AppSettings
from instagraph.store.dataset import Dataset

JANA_CHAT = "jananovakova_1"
GROUP_CHAT = "partia_2"
ALL_CHATS = [JANA_CHAT, GROUP_CHAT, "spammer_3"]


def box(ds: Dataset, folders: list[str], **kw: Any) -> ToolBox:
    ids = resolve_scope(ds, folders)
    ctx = ai_context(ds, AppSettings(), ids) if ids else None
    return ToolBox(ctx, max_calls=kw.pop("max_calls", 100), **kw)


def strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings(v)] + list(map(str, value))
    if isinstance(value, list):
        return [s for v in value for s in strings(v)]
    return []


# ---- scope -----------------------------------------------------------------------------


def test_resolve_scope_ignores_unknown_folders(small_dataset: Dataset) -> None:
    assert resolve_scope(small_dataset, [JANA_CHAT, "nope_9"]) == frozenset({0})
    assert resolve_scope(small_dataset, []) == frozenset()


def test_scoped_dataset_contains_only_allowed_threads_and_people(small_dataset: Dataset) -> None:
    scoped = scoped_dataset(small_dataset, frozenset({0}))
    assert scoped.threads_df()["thread_id"].to_list() == [0]
    assert set(scoped.messages.collect()["thread_id"].to_list()) == {0}
    assert sorted(scoped.people_df()["display_name"].to_list()) == ["Jana Nováková", "Me"]
    reacted = set(scoped.reactions.collect()["message_id"].to_list())
    assert reacted <= set(scoped.messages.collect()["message_id"].to_list())
    assert scoped.version >= SCOPED_VERSION_BASE
    assert scoped_dataset(small_dataset, frozenset({0})) is scoped  # cached


def test_scoped_results_never_share_cache_with_the_app(small_dataset: Dataset) -> None:
    settings = AppSettings()
    full = AnalysisContext(dataset=small_dataset, filters=FilterParams(), settings=settings)
    before = overview.get_overview(full).model_dump()
    scoped = ai_context(small_dataset, settings, frozenset({0}))
    overview.get_overview(scoped)
    assert overview.get_overview(full).model_dump() == before
    assert overview.get_overview(scoped).model_dump() != before


# ---- tools: every tool runs ------------------------------------------------------------

EXAMPLE_ARGS: dict[str, dict[str, Any]] = {
    "list_chats": {},
    "get_overview": {"section": "summary"},
    "get_chat_stats": {"chat_id": 1, "tab": "summary"},
    "track_terms": {"chat_id": 0, "terms": ["dobre"]},
    "list_people": {},
    "get_person": {"person_id": 1},
    "compare": {"chat_ids": [0, 1]},
    "search_messages": {"text": "dobr"},
    "get_message_context": {"message_id": 7, "before": 2, "after": 2},
    "sample_messages": {"chat_id": 0, "strategy": "spread", "n": 3},
    "get_wrapped": {},
}


def test_every_tool_has_an_example() -> None:
    assert {t.name for t in TOOLS} == set(EXAMPLE_ARGS)


@pytest.mark.parametrize("name", sorted(EXAMPLE_ARGS))
def test_tool_runs_and_returns_clean_json(small_dataset: Dataset, name: str) -> None:
    result = box(small_dataset, ALL_CHATS).call(name, EXAMPLE_ARGS[name])
    assert not result.is_error, result.content
    text = result.as_text()
    json.loads(text)
    assert len(text) <= MAX_RESULT_CHARS + 200
    for s in strings(result.content):
        assert not s.startswith("/api/media"), s
    for key in ("color_index", "dataset_version", "drilldown", "url", "uri", "source"):
        assert f'"{key}"' not in text


@pytest.mark.parametrize("section", ["timeline", "hours", "reply_times", "emojis", "awards"])
def test_overview_sections(small_dataset: Dataset, section: str) -> None:
    r = box(small_dataset, ALL_CHATS).call("get_overview", {"section": section})
    assert not r.is_error, r.content


@pytest.mark.parametrize(
    "tab", ["timeline", "when", "dynamics", "words", "emojis", "reactions", "calls", "group"]
)
def test_chat_tabs(small_dataset: Dataset, tab: str) -> None:
    r = box(small_dataset, ALL_CHATS).call("get_chat_stats", {"chat_id": 1, "tab": tab})
    assert not r.is_error, r.content


def test_tool_schemas_are_strict() -> None:
    for t in TOOLS:
        schema = t.input_schema
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert sorted(schema["required"]) == sorted(schema["properties"])


# ---- tools: scope is enforced ----------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("get_chat_stats", {"chat_id": 1, "tab": "summary"}),
        ("track_terms", {"chat_id": 1, "terms": ["ideme"]}),
        ("get_person", {"person_id": 2}),  # Peter: only in the group chat
        ("get_message_context", {"message_id": 7}),  # a group message
        ("sample_messages", {"chat_id": 1}),
        ("compare", {"chat_ids": [0, 1]}),
    ],
)
def test_disallowed_ids_are_not_found(small_dataset: Dataset, name: str, args: Any) -> None:
    r = box(small_dataset, [JANA_CHAT]).call(name, args)
    assert r.is_error
    assert "Peter" not in r.as_text() and "Partia" not in r.as_text()


def test_aggregates_only_include_allowed_chats(small_dataset: Dataset) -> None:
    b = box(small_dataset, [JANA_CHAT])
    chats = b.call("list_chats", {}).content
    assert [c["thread_id"] for c in chats["items"]] == [0]
    people = b.call("list_people", {}).content
    names = {p["person"]["name"] for p in people["items"]}
    assert names == {"Me", "Jana Nováková"}
    found = b.call("search_messages", {"chat_id": 1}).content
    assert found.get("total", 0) == 0
    everything = json.dumps(
        [b.call(n, EXAMPLE_ARGS[n]).content for n in ("list_chats", "list_people", "get_wrapped")],
        ensure_ascii=False,
    )
    for hidden in ("Peter", "Eva", "Spammer", "Partia", "Buy followers"):
        assert hidden not in everything


def test_no_scope_means_every_tool_refuses(small_dataset: Dataset) -> None:
    b = box(small_dataset, [])
    r = b.call("list_chats", {})
    assert r.is_error and r.content == NO_SCOPE_MESSAGE


# ---- tools: errors, limits, size cap, names ----------------------------------------------


def test_bad_arguments_become_error_results(small_dataset: Dataset) -> None:
    b = box(small_dataset, ALL_CHATS)
    assert b.call("nope", {}).is_error
    assert b.call("get_overview", {"section": "nonsense"}).is_error
    assert b.call("search_messages", {"start": "yesterday"}).is_error
    assert b.call("compare", {"chat_ids": [0]}).is_error
    assert b.call("search_messages", {"text": "(", "text_mode": "regex"}).is_error


def test_call_limit(small_dataset: Dataset) -> None:
    b = box(small_dataset, ALL_CHATS, max_calls=2)
    assert not b.call("list_chats", {}).is_error
    assert not b.call("list_chats", {}).is_error
    r = b.call("list_chats", {})
    assert r.is_error and "limit" in r.as_text()


def test_fit_shrinks_long_lists() -> None:
    value = {"items": [{"text": "x" * 100, "n": i} for i in range(2000)], "total": 2000}
    out, truncated = fit(value, max_chars=5_000)
    assert truncated and out["truncated"] is True
    assert len(json.dumps(out)) <= 5_000 + 200
    assert out["total"] == 2000 and 1 <= len(out["items"]) < 2000
    assert len(value["items"]) == 2000  # input untouched


def test_compact_drops_noise_and_nulls() -> None:
    raw = {"a": 1, "color_index": 3, "b": None, "c": [], "d": {"drilldown": {"x": 1}, "e": "y"}}
    assert compact(raw) == {"a": 1, "d": {"e": "y"}}


class FakeEncoder:
    def encode_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return value.replace("Jana Nováková", "[P1]").replace("Jana", "[P1]")
        if isinstance(value, dict):
            return {k: self.encode_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.encode_value(v) for v in value]
        return value

    def decode_text(self, text: str) -> str:
        return text.replace("[P1]", "Jana")


def test_results_are_encoded_and_args_decoded(small_dataset: Dataset) -> None:
    b = box(small_dataset, [JANA_CHAT], encoder=FakeEncoder())
    people = b.call("list_people", {}).as_text()
    assert "Jana" not in people and "[P1]" in people
    # The model searches for the placeholder; the tool searches for the real name.
    assert decode_args(FakeEncoder(), {"text": "[P1]", "n": [1, "[P1]"]}) == {
        "text": "Jana",
        "n": [1, "Jana"],
    }
