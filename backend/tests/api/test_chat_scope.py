"""Chat scope: an opened chat always shows its data — thread-category filters are
ignored on ``/api/chats/{chat_id}/...``; date/tz/system/people filters still apply."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from instagraph.store.cache import response_cache

CHAT_TABS = [
    "",
    "/summary",
    "/timeline",
    "/when",
    "/dynamics",
    "/words",
    "/emojis",
    "/terms?q=followers",
    "/reactions",
    "/media",
    "/calls",
    "/awards",
]


def _get(client: TestClient, path: str, **params: Any) -> Any:
    response_cache.clear()  # make sure equal responses are not just cache hits
    resp = client.get(path, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.parametrize("tab", CHAT_TABS)
def test_request_chat_ignores_category_filters(client: TestClient, tab: str) -> None:
    on = _get(client, f"/api/chats/2{tab}", include_requests=True)
    off = _get(client, f"/api/chats/2{tab}", include_requests=False)
    assert on == off


@pytest.mark.parametrize("tab", [*CHAT_TABS, "/group"])
def test_group_chat_ignores_include_groups(client: TestClient, tab: str) -> None:
    on = _get(client, f"/api/chats/1{tab}")
    off = _get(client, f"/api/chats/1{tab}", include_groups=False, include_archived=False)
    assert on == off


def test_request_chat_has_data_everywhere(client: TestClient) -> None:
    assert _get(client, "/api/chats/2")["total_messages"] == 1
    words = _get(client, "/api/chats/2/words")
    assert any(w["term"] == "followers" for w in words["top_words"]["overall"])


def test_chat_scope_keeps_date_and_people_filters(client: TestClient) -> None:
    assert _get(client, "/api/chats/2", start="2024-04-04")["total_messages"] == 0
    assert _get(client, "/api/chats/0", people="1")["total_messages"] == 6


def test_chat_list_still_applies_category_filters(client: TestClient) -> None:
    ids = {c["thread_id"] for c in _get(client, "/api/chats")["items"]}
    assert 2 not in ids
    ids = {c["thread_id"] for c in _get(client, "/api/chats", include_requests=True)["items"]}
    assert 2 in ids


@pytest.mark.parametrize("tab", [*CHAT_TABS, "/group"])
def test_unknown_chat_404(client: TestClient, tab: str) -> None:
    resp = client.get(f"/api/chats/999{tab}")
    assert resp.status_code == 404
