"""Overview API on the ``small_dataset`` fixture (see tests/conftest.py).

Hand calculation (UTC, default filters: requests and system messages excluded):

* counted messages: chat 0 (Me ↔ Jana) = 12 (13 rows minus a like),
  chat 1 (group) = 5 (6 rows minus the rename) → 17; the request chat has 1 more.
* sent by Me: 1, 2, 5, 12, 16, 17 (chat 0) + 11 (group) = 7 → received 10.
* active days: 03-30 (4), 03-31 (2), 04-01 (4), 04-02 (1), 04-05 (4), 04-06 (2).
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

SECTIONS = [
    "timeline",
    "sent-received",
    "contacts",
    "top-over-time",
    "calendar",
    "heatmap",
    "hours",
    "weekdays",
    "cumulative",
    "new-conversations",
    "active-chats",
    "types",
    "size-distribution",
    "years",
]


def get(client: TestClient, path: str, **params: Any) -> dict[str, Any]:
    resp = client.get(path, params=params)
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def tiles(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {t["key"]: t for t in body["tiles"]}


def series(chart: dict[str, Any]) -> dict[str, list[Any]]:
    return {s["key"]: s["values"] for s in chart["series"]}


def test_overview_tiles(client: TestClient) -> None:
    body = get(client, "/api/overview")
    t = tiles(body)
    assert t["total_messages"]["value"] == 17
    assert t["sent"]["value"] == 7 and t["sent"]["drilldown"]["sender_id"] == 0
    assert t["received"]["value"] == 10
    assert t["received"]["drilldown"]["sender_ids"] == [1, 2, 3]
    assert t["my_share"]["value"] == pytest.approx(7 / 17)
    assert t["chats"]["value"] == 2
    assert t["chats_one_to_one"]["value"] == 1 and t["chats_group"]["value"] == 1
    assert t["chats_request"]["value"] == 0
    assert t["people"]["value"] == 3
    # reactions: Me reacted to msgs 0, 7, 19; Jana reacted to Me's msg 1
    assert t["reactions_given"]["value"] == 3 and t["reactions_received"]["value"] == 1
    assert t["photos"]["value"] == 2 and t["videos"]["value"] == 0
    assert t["shares"]["value"] == 1 and t["links"]["value"] == 1
    assert t["calls"]["value"] == 1 and t["call_time"]["value"] == 312_000
    assert t["call_time"]["unit"] == "ms"
    assert t["active_days"]["value"] == 6
    assert t["avg_per_active_day"]["value"] == pytest.approx(17 / 6)
    assert t["emojis"]["value"] == 8  # 😂, ❤ 👍🏽, 😂😂, 👋, 🇸🇰 👨‍👩‍👧

    first = body["first_message"]
    assert first["message"]["message_id"] == 0
    assert first["chat"]["thread_id"] == 0 and first["chat"]["person"]["person_id"] == 1
    assert t["first_message"]["value"] == "2024-03-30"
    assert t["first_message"]["person"]["name"] == "Jana Nováková"

    busiest = body["busiest_day"]  # 03-30, 04-01 and 04-05 all have 4 → earliest
    assert busiest["date"] == "2024-03-30" and busiest["count"] == 4
    assert busiest["top_chat"]["thread_id"] == 0 and busiest["top_chat_count"] == 4
    assert busiest["drilldown"]["start"] == busiest["drilldown"]["end"] == "2024-03-30"

    streak = body["longest_streak"]
    assert (streak["length_days"], streak["start"], streak["end"]) == (
        4,
        "2024-03-30",
        "2024-04-02",
    )
    assert body["current_streak"]["length_days"] == 0
    assert body["span"] == {"start": "2024-03-30", "end": "2024-04-06"}
    excluded = body["excluded_requests"]
    assert excluded["threads"] == 1 and excluded["messages"] == 1
    assert "+1 message request" in excluded["note"]


def test_overview_filters_change_results(client: TestClient) -> None:
    t = tiles(body := get(client, "/api/overview", include_requests=True))
    assert t["total_messages"]["value"] == 18 and t["chats_request"]["value"] == 1
    assert body["excluded_requests"] is None
    t = tiles(get(client, "/api/overview", start="2024-04-01"))
    assert t["total_messages"]["value"] == 11
    t = tiles(get(client, "/api/overview", people="0"))
    assert t["total_messages"]["value"] == 7 and t["received"]["value"] == 0
    t = tiles(get(client, "/api/overview", include_groups=False))
    assert t["total_messages"]["value"] == 12 and t["chats_group"]["value"] == 0


def test_timeline(client: TestClient) -> None:
    body = get(client, "/api/overview/timeline", bucket="week")
    tl = body["timeline"]
    assert tl["bucket"] == "week" and tl["x"] == ["2024-03-25", "2024-04-01"]
    s = series(tl)
    assert s == {"0": [6.0, 6.0], "1": [0.0, 5.0]}  # no "others" with 2 chats
    assert body["drilldowns"]["0"]["thread_id"] == 0
    assert [c["kind"] for c in body["chats"]] == ["one_to_one", "group"]
    day = get(client, "/api/overview/timeline", bucket="day")["timeline"]
    assert len(day["x"]) == 8 and sum(sum(v) for v in series(day).values()) == 17
    # with requests included, the request chat is the 3rd series (top 10)
    s = series(get(client, "/api/overview/timeline", include_requests=True)["timeline"])
    assert set(s) == {"0", "1", "2"}


def test_sent_received(client: TestClient) -> None:
    tl = get(client, "/api/overview/sent-received", bucket="month")["timeline"]
    assert tl["x"] == ["2024-03-01", "2024-04-01"]
    assert series(tl) == {"sent": [3.0, 4.0], "received": [3.0, 7.0]}


def test_contacts_metrics(client: TestClient) -> None:
    body = get(client, "/api/overview/contacts")
    assert body["total"] == 17
    assert [(i["chat"]["thread_id"], i["value"]) for i in body["items"]] == [(0, 12), (1, 5)]
    assert body["items"][0]["share"] == pytest.approx(12 / 17)
    calls = get(client, "/api/overview/contacts", metric="calls")
    assert [(i["chat"]["thread_id"], i["value"]) for i in calls["items"]] == [(0, 1)]
    assert calls["items"][0]["drilldown"]["types"] == ["call"]
    reactions = get(client, "/api/overview/contacts", metric="reactions")
    assert [(i["chat"]["thread_id"], i["value"]) for i in reactions["items"]] == [(1, 3), (0, 2)]
    emojis = get(client, "/api/overview/contacts", metric="emojis")
    assert [(i["chat"]["thread_id"], i["value"]) for i in emojis["items"]] == [(0, 5), (1, 3)]
    assert len(get(client, "/api/overview/contacts", limit=1)["items"]) == 1
    assert client.get("/api/overview/contacts", params={"metric": "nope"}).status_code == 422


def test_top_over_time(client: TestClient) -> None:
    body = get(client, "/api/overview/top-over-time")
    assert body["ranks"]["x"] == ["2024-03-01", "2024-04-01"]
    assert series(body["ranks"]) == {"0": [1.0, 1.0], "1": [None, 2.0]}
    assert series(body["stream"]) == {"0": [6.0, 6.0], "1": [0.0, 5.0]}


def test_calendar_heatmap_hours_weekdays(client: TestClient) -> None:
    cal = get(client, "/api/overview/calendar")
    assert cal["active_days"] == 6 and cal["years"] == [2024]
    assert cal["calendar"]["max_value"] == 4
    assert {d["date"]: d["value"] for d in cal["calendar"]["days"]}["2024-04-02"] == 1

    hm = get(client, "/api/overview/heatmap")["heatmap"]
    assert hm["y_labels"][0] == "Mon" and len(hm["x_labels"]) == 24
    assert sum(map(sum, hm["values"])) == 17
    assert hm["values"][5][21] == 4  # Saturday 2024-03-30, 21:xx UTC

    hours = get(client, "/api/overview/hours")["chart"]
    s = series(hours)
    assert sum(s["me"]) == 7 and sum(s["others"]) == 10
    assert s["me"][21] == 2 and s["others"][21] == 2
    assert hours["drilldowns"][21]["hour"] == 21
    # Display tz shifts hours: Bratislava is UTC+1 on 2024-03-30 (21h → 22h) and
    # UTC+2 from 03-31 (Me's call at 19:00Z on 04-02 → 21h)
    s = series(get(client, "/api/overview/hours", tz="Europe/Bratislava")["chart"])
    assert s["me"][21] == 1 and s["me"][22] == 2

    wd = get(client, "/api/overview/weekdays")["chart"]
    assert series(wd)["total"] == [4.0, 1.0, 0.0, 0.0, 4.0, 6.0, 2.0]
    assert wd["drilldowns"][6]["weekday"] == 7


def test_cumulative_new_active(client: TestClient) -> None:
    cum = get(client, "/api/overview/cumulative")["timeline"]
    assert cum["bucket"] == "day" and cum["series"][0]["values"][-1] == 17
    assert cum["series"][0]["values"][0] == 4

    new = get(client, "/api/overview/new-conversations")
    # Jana first seen in March; Peter and Eva in April (group); Spammer excluded.
    assert new["total"] == 3 and new["timeline"]["series"][0]["values"] == [1.0, 2.0]
    new = get(client, "/api/overview/new-conversations", include_requests=True)
    assert new["total"] == 4

    active = get(client, "/api/overview/active-chats")["timeline"]
    assert active["series"][0]["values"] == [1.0, 2.0]


def test_types_and_size_distribution(client: TestClient) -> None:
    body = get(client, "/api/overview/types")
    totals = dict(
        zip(body["totals"]["categories"], body["totals"]["series"][0]["values"], strict=True)
    )
    assert totals == {"text": 12, "link": 1, "photo": 1, "share": 1, "call": 1, "unsent": 1}
    s = series(body["timeline"])
    assert s["photo"] == [1.0, 0.0] and s["call"] == [0.0, 1.0]

    size = get(client, "/api/overview/size-distribution")
    assert [(b["label"], b["chats"]) for b in size["bins"]] == [
        ("1", 0),
        ("2–4", 0),
        ("5–9", 1),
        ("10–19", 1),
    ]
    assert size["bins"][3]["drilldown"]["thread_ids"] == [0]


def test_years(client: TestClient) -> None:
    rows = get(client, "/api/overview/years")["years"]
    assert len(rows) == 1
    y = rows[0]
    assert (y["year"], y["messages"], y["sent"], y["received"]) == (2024, 17, 7, 10)
    assert (y["people"], y["chats"], y["active_days"]) == (3, 2, 6)
    assert y["top_chat"]["thread_id"] == 0 and y["top_chat_messages"] == 12
    assert y["top_emoji"] == "😂" and y["top_emoji_count"] == 3
    assert y["drilldown"] == {**y["drilldown"], "start": "2024-01-01", "end": "2024-12-31"}


@pytest.mark.parametrize("section", ["", *[f"/{s}" for s in SECTIONS]])
def test_409_without_data(empty_client: TestClient, section: str) -> None:
    resp = empty_client.get(f"/api/overview{section}")
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "no_data"


@pytest.mark.parametrize("section", SECTIONS)
def test_sections_have_dataset_version(client: TestClient, section: str) -> None:
    body = get(client, f"/api/overview/{section}", include_requests=True, tz="America/New_York")
    assert body["dataset_version"] >= 1


def test_bucket_validation(client: TestClient) -> None:
    assert client.get("/api/overview/timeline", params={"bucket": "hour"}).status_code == 422
