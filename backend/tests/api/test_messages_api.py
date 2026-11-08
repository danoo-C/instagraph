import json
from typing import Any

from fastapi.testclient import TestClient

from instagraph.api.deps import AnalysisContext
from instagraph.api.filters import FilterParams
from instagraph.api.routes.messages import thread_titles
from instagraph.store.app_settings import AppSettings
from instagraph.store.dataset import Dataset


def query_params(drill: dict[str, Any]) -> dict[str, Any]:
    """MessageQuery JSON → query params (lists comma-joined), like the frontend does."""
    out: dict[str, Any] = {}
    for k, v in drill.items():
        if v is None:
            continue
        out[k] = ",".join(str(x) for x in v) if isinstance(v, list) else v
    return out


def test_no_data(empty_client: TestClient) -> None:
    assert empty_client.get("/api/messages").status_code == 409
    assert empty_client.get("/api/messages/random").status_code == 409


def test_search_page_shape(client: TestClient) -> None:
    body = client.get("/api/messages", params={"limit": 3}).json()
    assert body["total"] == 18 and body["order"] == "desc"
    assert [i["message_id"] for i in body["items"]] == [19, 18, 17]
    assert body["has_next"] and not body["has_prev"] and body["prev_cursor"] is None
    share = body["items"][0]
    assert share["thread_title"] == "Partia" and share["share"]["kind"] == "reel"
    assert share["reactions"][0]["actor"]["name"] == "Me"
    assert body["stats"] is None


def test_item_details(client: TestClient) -> None:
    items = client.get("/api/messages", params={"message_ids": "5,12", "order": "asc"}).json()[
        "items"
    ]
    photo, call = items
    assert photo["attachments"]["photos"] == 2 and photo["type"] == "photo"
    assert call["call"] == {"kind": "audio", "duration_s": 312, "missed": False}
    # display tz offset
    item = client.get(
        "/api/messages", params={"message_ids": "0", "tz": "Europe/Bratislava"}
    ).json()["items"][0]
    assert item["ts"] == "2024-03-30T22:00:00+01:00"


def test_cursor_pagination_both_directions(client: TestClient) -> None:
    for order in ("asc", "desc"):
        seen: list[int] = []
        pages = []
        params: dict[str, Any] = {"limit": 4, "order": order}
        body = client.get("/api/messages", params=params).json()
        pages.append(body)
        while body["has_next"]:
            body = client.get(
                "/api/messages", params={**params, "cursor": body["next_cursor"]}
            ).json()
            pages.append(body)
        seen = [i["message_id"] for p in pages for i in p["items"]]
        assert len(seen) == 18 and len(set(seen)) == 18
        assert seen == sorted(seen, reverse=order == "desc")
        back = [pages[-1]]
        while back[-1]["has_prev"]:
            back.append(
                client.get(
                    "/api/messages",
                    params={**params, "cursor": back[-1]["prev_cursor"], "direction": "before"},
                ).json()
            )
        assert [i["message_id"] for p in reversed(back) for i in p["items"]] == seen


def test_filters_and_validation(client: TestClient) -> None:
    get = lambda **p: client.get("/api/messages", params=p)  # noqa: E731
    assert get(text="dakujem", accent_insensitive=True).json()["total"] == 1
    assert get(types="photo,call").json()["total"] == 2
    assert get(thread_ids=["0", "1"], sender_ids="2").json()["total"] == 2
    r = get(text="(a", text_mode="regex")
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert get(types="bogus").status_code == 422
    assert get(hour=24).status_code == 422
    assert get(cursor="garbage!").status_code == 422
    assert get(thread_ids="x").status_code == 422
    # global filters apply
    assert get(include_requests=True).json()["total"] == 19
    assert get(people="1").json()["total"] == 8
    assert get(start="2024-04-05", end="2024-04-05").json()["total"] == 4
    # asking for system messages lifts exclude_system
    assert [i["message_id"] for i in get(system_event="rename").json()["items"]] == [8]


def test_stats_and_drilldowns(client: TestClient) -> None:
    body = client.get("/api/messages", params={"include_stats": True, "text": "a"}).json()
    stats = body["stats"]
    assert stats["timeline"]["bucket"] == "day"
    assert sum(stats["timeline"]["series"][0]["values"]) == body["total"]
    assert sum(p["count"] for p in stats["per_person"]) == body["total"]
    top = stats["per_person"][0]
    again = client.get("/api/messages", params=query_params(top["drilldown"])).json()
    assert again["total"] == top["count"]
    assert {i["sender"]["person_id"] for i in again["items"]} == {top["person"]["person_id"]}


def test_context(client: TestClient) -> None:
    body = client.get("/api/messages/4/context", params={"before": 2, "after": 2}).json()
    assert [i["message_id"] for i in body["items"]] == [2, 3, 4, 5, 6]
    assert body["target_index"] == 2 and body["thread_title"] == "Jana Nováková"
    # requests are reachable through context even though filtered out of search
    assert client.get("/api/messages/13/context").json()["items"][0]["message_id"] == 13
    r = client.get("/api/messages/999/context")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_random(client: TestClient) -> None:
    a = client.get("/api/messages/random", params={"seed": 3, "thread_id": 1}).json()
    b = client.get("/api/messages/random", params={"seed": 3, "thread_id": 1}).json()
    assert a == b and a["message"]["thread_id"] == 1
    none = client.get("/api/messages/random", params={"text": "zzzz"}).json()
    assert none["message"] is None


def test_on_this_day(client: TestClient) -> None:
    body = client.get("/api/messages/on-this-day", params={"date": "2026-04-05"}).json()
    assert body["date"] == "2026-04-05"
    (year,) = body["years"]
    assert year["year"] == 2024 and year["years_ago"] == 2 and year["count"] == 4
    again = client.get("/api/messages", params=query_params(year["drilldown"])).json()
    assert again["total"] == 4
    assert client.get("/api/messages/on-this-day").status_code == 200  # today default


def test_anonymize_leaks_nothing(client: TestClient, small_dataset: Dataset) -> None:
    assert client.put("/api/settings", json={"anonymize": True}).status_code == 200
    payloads = [
        client.get("/api/messages", params={"limit": 500, "include_stats": True}).text,
        client.get("/api/messages/4/context").text,
        client.get("/api/messages/random").text,
        client.get("/api/messages/on-this-day", params={"date": "2025-04-01"}).text,
        client.get("/api/messages", params={"system_event": "rename"}).text,
    ]
    secrets = ["Jana", "Peter", "Eva", "Partia", "Ahoj", "example.com", "funny", "reel/abc"]
    for text in payloads:
        for secret in secrets:
            assert secret not in text, secret
    item = json.loads(payloads[0])["items"][0]
    assert item["text"] is None and item["sender"]["name"].startswith("Person ")


def test_thread_titles_match_context(small_dataset: Dataset) -> None:
    for anon in (False, True):
        ctx = AnalysisContext(small_dataset, FilterParams(), AppSettings(anonymize=anon))
        titles = thread_titles(ctx, [0, 1, 2, 77])
        assert titles == {t: ctx.thread_title(t) for t in (0, 1, 2, 77)}
