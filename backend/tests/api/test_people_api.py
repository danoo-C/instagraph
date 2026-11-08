"""``GET /api/people`` and ``GET /api/people/{id}`` on the shared ``small_dataset``.

Counted messages (requests excluded by default): Me 7, Jana 7, Peter 2, Eva 1.
"""

from typing import Any

from fastapi.testclient import TestClient

from instagraph.store.app_settings import AppSettings, get_settings_store


def _items(client: TestClient, query: str = "") -> list[dict[str, Any]]:
    r = client.get(f"/api/people{query}")
    assert r.status_code == 200
    items: list[dict[str, Any]] = r.json()["items"]
    return items


def test_list_default(client: TestClient) -> None:
    items = _items(client)
    rows = {
        i["person"]["person_id"]: (
            i["messages_sent"],
            i["messages_exchanged"],
            i["threads_in_common"],
        )
        for i in items
    }
    # Jana: 7 own + 6 of mine in our 1:1; Me: all 17 counted messages in scope
    assert rows == {0: (7, 17, 2), 1: (7, 13, 2), 2: (2, 2, 1), 3: (1, 1, 1)}
    assert [i["person"]["person_id"] for i in items] == [0, 1, 2, 3]
    jana = next(i for i in items if i["person"]["person_id"] == 1)
    assert jana["one_to_one_threads"] == 1 and jana["group_threads"] == 1
    assert jana["first_contact"].startswith("2024-03-30T21:00")
    assert jana["last_contact"].startswith("2024-04-06T08:00")
    assert jana["drilldown"]["sender_id"] == 1
    assert 4 not in rows  # Spammer only in a request thread (excluded by default)


def test_list_requests_included(client: TestClient) -> None:
    ids = [i["person"]["person_id"] for i in _items(client, "?include_requests=true")]
    assert 4 in ids


def test_list_search_sort_paginate(client: TestClient) -> None:
    assert [i["person"]["name"] for i in _items(client, "?q=novak")] == ["Jana Nováková"]
    assert [i["person"]["name"] for i in _items(client, "?sort=name&order=asc")] == [
        "Eva",
        "Jana Nováková",
        "Me",
        "Peter",
    ]
    r = client.get("/api/people?sort=messages&order=asc&limit=2&offset=1").json()
    assert r["total"] == 4 and r["limit"] == 2 and r["offset"] == 1
    assert [i["person"]["person_id"] for i in r["items"]] == [2, 0]
    last = _items(client, "?sort=last_contact")
    assert last[-1]["person"]["person_id"] == 3
    assert client.get("/api/people?sort=bogus").status_code == 422
    assert client.get("/api/people?limit=0").status_code == 422


def test_list_people_filter(client: TestClient) -> None:
    items = _items(client, "?people=1,2")
    assert [i["person"]["person_id"] for i in items] == [1, 2]
    assert items[0]["messages_exchanged"] == 13  # context not reduced by the filter


def test_person_detail(client: TestClient) -> None:
    r = client.get("/api/people/1")
    assert r.status_code == 200
    body = r.json()
    assert body["is_me"] is False and body["person"]["name"] == "Jana Nováková"
    tiles = {t["key"]: t["value"] for t in body["tiles"]}
    assert tiles["messages_sent"] == 7
    assert tiles["my_messages"] == 6
    assert tiles["messages_exchanged"] == 13
    assert tiles["threads_in_common"] == 2
    assert tiles["reactions_to_me"] == 1  # ❤ on my "Dobre!"
    assert tiles["reactions_from_me"] == 1  # 😂 on her first message
    threads = {t["thread_id"]: (t["their_messages"], t["my_messages"]) for t in body["threads"]}
    assert threads == {0: (6, 6), 1: (1, 1)}
    act = body["activity"]
    assert act["x"] == ["2024-03-01", "2024-04-01"]
    assert [s["values"] for s in act["series"]] == [[3.0, 4.0], [3.0, 4.0]]
    assert sum(body["hours"]["series"][0]["values"]) == 1.0
    stats = {s["key"]: (s["person_value"], s["my_value"]) for s in body["stats"]}
    assert stats["messages"] == (7, 7)
    assert body["top_emojis"][0] == {"emoji": "😂", "count": 3}
    assert body["top_contacts"] is None


def test_me_page(client: TestClient) -> None:
    body = client.get("/api/people/0").json()
    assert body["is_me"] is True
    tiles = {t["key"]: t["value"] for t in body["tiles"]}
    assert tiles["messages_sent"] == 7
    assert tiles["messages_received"] == 10
    assert tiles["chats"] == 2
    assert tiles["people"] == 3
    assert [c["person"]["person_id"] for c in body["top_contacts"]] == [1, 2, 3]
    assert all(s["my_value"] is None for s in body["stats"])
    assert [s["key"] for s in body["activity"]["series"]] == ["0", "received"]


def test_unknown_person(client: TestClient) -> None:
    assert client.get("/api/people/999").status_code == 404


def test_anonymized(client: TestClient) -> None:
    get_settings_store().update(AppSettings(anonymize=True))
    items = _items(client)
    names = {i["person"]["name"] for i in items}
    assert "Jana Nováková" not in names and "Me" in names
    assert _items(client, "?q=jana") == []
