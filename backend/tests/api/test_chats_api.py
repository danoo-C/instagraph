"""Chats API on the ``small_dataset`` fixture (see tests/conftest.py and the hand
calculation in test_overview_api.py).

Chat 0 (Me ↔ Jana), counted messages (UTC):
  Me:   1 (03-30 21:02), 2 (21:05 link), 5 (03-31 08:00, 2 photos), 12 (04-02 19:00 call),
        16 (04-05 18:30), 17 (18:31 unsent)
  Jana: 0 (03-30 21:00), 4 (21:15), 6 (03-31 09:00), 14 (04-05 18:00), 15 (18:03),
        18 (04-06 08:00)
Sessions (60 min gap) start at 0 (Jana), 5 (Me), 12 (Me), 14 (Jana), 18 (Jana).
Replies (≤ 12 h): Me 120 000 (1), 38 700 000 (5), 1 620 000 (16) → median 1 620 000;
Jana 600 000 (4), 3 600 000 (6) → median 2 100 000.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

TABS = ["", "/summary", "/timeline", "/when"]


def get(client: TestClient, path: str, **params: Any) -> dict[str, Any]:
    resp = client.get(path, params=params)
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def series(chart: dict[str, Any]) -> dict[str, list[Any]]:
    return {s["key"]: s["values"] for s in chart["series"]}


def tiles(body: dict[str, Any]) -> dict[str, Any]:
    return {t["key"]: t["value"] for t in body["tiles"]}


# --------------------------------------------------------------------------------------
# List
# --------------------------------------------------------------------------------------


def test_list_columns(client: TestClient) -> None:
    body = get(client, "/api/chats")
    assert body["total"] == 2 and body["sort"] == "messages" and body["order"] == "desc"
    assert body["reference_date"] == "2024-04-06"
    assert body["sparkline_months"] == ["2024-03-01", "2024-04-01"]
    jana, group = body["items"]
    assert jana["thread_id"] == 0 and jana["kind"] == "one_to_one"
    assert [p["person_id"] for p in jana["participants"]] == [0, 1]
    assert jana["messages"] == 12 and jana["my_share"] == 0.5
    assert jana["messages_last_30d"] == 12
    assert jana["first_ts"] == "2024-03-30T21:00:00Z"
    assert jana["duration_days"] == 8 and jana["avg_per_day"] == 1.5
    assert jana["median_reply_me_ms"] == 1_620_000
    assert jana["median_reply_them_ms"] == 2_100_000
    assert jana["emojis"] == 5 and jana["calls"] == 1
    assert jana["sparkline"] == [6, 6] and jana["trend"] == "stable"
    assert jana["drilldown"]["thread_id"] == 0
    assert group["kind"] == "group" and group["n_participants"] == 4
    assert group["messages"] == 5 and group["my_share"] == 0.2
    assert group["sparkline"] == [0, 5] and group["trend"] == "rising"


def test_list_sort_search_paginate(client: TestClient) -> None:
    ids = lambda body: [i["thread_id"] for i in body["items"]]  # noqa: E731
    assert ids(get(client, "/api/chats", sort="messages", order="asc")) == [1, 0]
    assert ids(get(client, "/api/chats", sort="title", order="asc")) == [0, 1]
    assert ids(get(client, "/api/chats", sort="last_ts", order="desc")) == [1, 0]
    assert ids(get(client, "/api/chats", sort="participants")) == [1, 0]
    page = get(client, "/api/chats", limit=1, offset=1)
    assert page["total"] == 2 and ids(page) == [1]
    # accent-insensitive; participant names are searched too
    assert ids(get(client, "/api/chats", q="NOVAK")) == [0, 1]
    assert ids(get(client, "/api/chats", q="peter")) == [1]
    assert get(client, "/api/chats", q="zzz")["total"] == 0
    assert client.get("/api/chats", params={"sort": "bogus"}).status_code == 422


def test_list_filters(client: TestClient) -> None:
    body = get(client, "/api/chats", include_requests=True)
    assert body["total"] == 3
    assert {i["kind"] for i in body["items"]} == {"one_to_one", "group", "request"}
    body = get(client, "/api/chats", start="2024-04-03")
    jana = next(i for i in body["items"] if i["thread_id"] == 0)
    assert jana["messages"] == 5 and jana["my_share"] == 0.4
    assert get(client, "/api/chats", include_groups=False)["total"] == 1


# --------------------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------------------


def test_header(client: TestClient) -> None:
    h = get(client, "/api/chats/0")
    assert h["title"] == "Jana Nováková" and h["kind"] == "one_to_one" and not h["is_group"]
    assert [p["name"] for p in h["participants"]] == ["Me", "Jana Nováková"]
    assert h["span"] == {"start": "2024-03-30", "end": "2024-04-06"} and h["span_days"] == 8
    assert (h["total_messages"], h["my_messages"], h["my_share"]) == (12, 6, 0.5)
    g = get(client, "/api/chats/1")
    assert g["is_group"] and g["category"] == "inbox" and g["participants"][0]["is_owner"]
    req = get(client, "/api/chats/2")  # opened chat ignores category filters
    assert req["kind"] == "request" and req["total_messages"] == 1
    assert get(client, "/api/chats/2", include_requests=True)["total_messages"] == 1


@pytest.mark.parametrize("tab", TABS)
def test_unknown_chat_404(client: TestClient, tab: str) -> None:
    resp = client.get(f"/api/chats/99{tab}")
    assert resp.status_code == 404 and resp.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("path", ["/api/chats", *[f"/api/chats/0{t}" for t in TABS]])
def test_409_without_data(empty_client: TestClient, path: str) -> None:
    resp = empty_client.get(path)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "no_data"


# --------------------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------------------


def test_summary(client: TestClient) -> None:
    body = get(client, "/api/chats/0/summary")
    t = tiles(body)
    assert t["total_messages"] == 12 and t["reactions"] == 2 and t["media"] == 2
    assert t["calls"] == 1 and t["active_days"] == 5 and t["span_days"] == 8
    assert t["first_message"] == "2024-03-30" and t["last_message"] == "2024-04-06"
    assert t["avg_per_day"] == 1.5 and t["longest_streak"] == 2
    assert t["longest_silence"] == 71 * 3_600_000  # 04-02 19:00 → 04-05 18:00

    me, jana = body["people"]  # 6 messages each → by person id
    assert me["person"]["person_id"] == 0 and jana["person"]["person_id"] == 1
    assert me["messages"] == 6 and me["share"] == 0.5
    assert me["words"] == 6 and me["avg_words_per_message"] == 2.0  # 3 + 1 + 2 over 3
    assert me["photos"] == 2 and me["calls_started"] == 1 and me["unsent"] == 1
    assert me["links"] == 1 and me["emojis"] == 0
    assert me["reactions_given"] == 1 and me["reactions_received"] == 1
    assert me["median_reply_ms"] == 1_620_000 and jana["median_reply_ms"] == 2_100_000
    assert me["initiation_rate"] == pytest.approx(2 / 5)
    assert jana["initiation_rate"] == pytest.approx(3 / 5)
    assert jana["emojis"] == 5 and jana["emojis_per_message"] == pytest.approx(5 / 6)
    assert jana["drilldown"] == {**jana["drilldown"], "thread_id": 0, "sender_id": 1}

    assert body["messages_by_person"]["series"][0]["values"] == [6.0, 6.0]
    assert body["first_message"]["message_id"] == 0
    assert body["last_message"]["message_id"] == 18
    assert body["first_message"]["text"] == "Ahoj, ako sa máš? 😂"
    assert body["longest_streak"]["start"] == "2024-03-30"
    mutual = body["mutual_streak"]  # both wrote on 03-30, 03-31, 04-05
    assert (mutual["length_days"], mutual["start"], mutual["end"]) == (
        2,
        "2024-03-30",
        "2024-03-31",
    )
    sil = body["longest_silence"]
    assert sil["before"]["message_id"] == 12 and sil["after"]["message_id"] == 14


def test_summary_group_and_filters(client: TestClient) -> None:
    body = get(client, "/api/chats/1/summary")
    assert body["mutual_streak"] is None
    rows = {r["person"]["person_id"]: r for r in body["people"]}
    assert set(rows) == {0, 1, 2, 3}
    assert rows[2]["messages"] == 2 and rows[2]["reels"] == 1
    assert rows[2]["reactions_received"] == 3  # 👍 + ❤ on msg 7, 😂 on msg 19
    body = get(client, "/api/chats/0/summary", people="1")
    assert tiles(body)["total_messages"] == 6


# --------------------------------------------------------------------------------------
# Timeline
# --------------------------------------------------------------------------------------


def test_timeline(client: TestClient) -> None:
    body = get(client, "/api/chats/0/timeline", bucket="day")
    msgs = body["messages"]
    assert msgs["bucket"] == "day" and len(msgs["x"]) == 8
    s = series(msgs)
    assert s["0"] == [2.0, 1.0, 0.0, 1.0, 0.0, 0.0, 2.0, 0.0]
    assert s["1"] == [2.0, 1.0, 0.0, 0.0, 0.0, 0.0, 2.0, 1.0]
    assert series(body["share"]) == {"0": [0.5, 0.5], "1": [0.5, 0.5]}
    assert series(body["balance"])["balance"] == [1.0, 1.0]
    assert body["balance_overall"] == 1.0
    cum = series(body["cumulative"])
    assert cum["0"][-1] == 6 and cum["1"][-1] == 6
    rolling = series(body["rolling"])
    assert rolling["daily"][:2] == [4.0, 2.0] and rolling["avg_7"][1] == 3.0
    wpm = series(body["words_per_message"])
    assert wpm["0"] == [2.0, 2.0]  # March: (3 + 1) / 2 texts; April: 2 / 1
    assert body["calendar"]["max_value"] == 4
    kinds = [e["kind"] for e in body["events"]]
    assert kinds.count("silence") == 2  # 71 h and 58 h gaps; others < 1 day
    assert "first_message" in kinds and "first_call" in kinds
    call = next(e for e in body["events"] if e["kind"] == "first_call")
    assert call["date"] == "2024-04-02" and call["message_id"] == 12
    assert body["trend"] == "stable"
    life = body["lifecycle"]
    assert life["active_months"] == 2 and life["longest_break_days"] == 2
    assert life["break_start"] == "2024-04-02" and life["comeback"] == "2024-04-05"


def test_timeline_group_events(client: TestClient) -> None:
    body = get(client, "/api/chats/1/timeline")
    assert body["balance"] is None and body["balance_overall"] is None
    rename = next(e for e in body["events"] if e["kind"] == "rename")
    assert rename["label"] == "Renamed to “Partia”" and rename["message_id"] == 8
    assert body["requested_bucket"] == "week"


# --------------------------------------------------------------------------------------
# When
# --------------------------------------------------------------------------------------


def test_when(client: TestClient) -> None:
    body = get(client, "/api/chats/0/when")
    assert sum(map(sum, body["heatmap"]["values"])) == 12
    assert [h["person"]["person_id"] for h in body["heatmaps"]] == [0, 1]
    hours = series(body["hours"])
    assert hours["0"][21] == 2 and hours["1"][21] == 2
    assert body["hours"]["drilldowns"][21] == {
        **body["hours"]["drilldowns"][21],
        "thread_id": 0,
        "hour": 21,
    }
    months = series(body["months"])
    assert months["0"][2] == 3 and months["0"][3] == 3
    stats = {s["person"]["person_id"]: s for s in body["person_stats"]}
    assert stats[0]["weekend_ratio"] == 0.5  # Sat ×2, Sun ×1 out of 6
    assert stats[0]["night_owl_ratio"] == 0 and stats[0]["early_bird_ratio"] == 0
    assert series(body["weekday_weekend"])["0"] == [0.5, 0.5]
    assert [(d["date"], d["count"]) for d in body["busiest_days"][:3]] == [
        ("2024-03-30", 4),
        ("2024-04-05", 4),
        ("2024-03-31", 2),
    ]
    bh = body["busiest_hour"]
    assert (bh["date"], bh["hour"], bh["count"]) == ("2024-03-30", 21, 4)
    assert body["hours_by_year"]["y_labels"] == ["2024"]
    assert sum(body["hours_by_year"]["values"][0]) == pytest.approx(1.0)


def test_when_time_zone(client: TestClient) -> None:
    # Tokyo (UTC+9): Me's messages fall at 6, 6, 17, 4, 3, 3 h local.
    body = get(client, "/api/chats/0/when", tz="Asia/Tokyo")
    me = next(s for s in body["person_stats"] if s["person"]["person_id"] == 0)
    assert me["night_owl_ratio"] == 0.5 and me["early_bird_ratio"] == pytest.approx(2 / 6)
    assert me["weekend_ratio"] == pytest.approx(5 / 6)


# --------------------------------------------------------------------------------------
# Invariant (§15.3): overview total == Σ chats == Σ people per chat
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"include_requests": True},
        {"start": "2024-03-31", "end": "2024-04-05"},
        {"people": "1,2"},
        {"tz": "Pacific/Kiritimati", "include_groups": False},
        {"exclude_system": False},
    ],
)
def test_totals_invariant(client: TestClient, params: dict[str, Any]) -> None:
    overview = get(client, "/api/overview", **params)
    total = next(t["value"] for t in overview["tiles"] if t["key"] == "total_messages")
    chats = get(client, "/api/chats", limit=500, **params)["items"]
    assert sum(c["messages"] for c in chats) == total
    for chat in chats:
        summary = get(client, f"/api/chats/{chat['thread_id']}/summary", **params)
        assert sum(p["messages"] for p in summary["people"]) == chat["messages"]
        header = get(client, f"/api/chats/{chat['thread_id']}", **params)
        assert header["total_messages"] == chat["messages"]
