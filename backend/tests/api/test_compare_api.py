"""``GET /api/compare`` on the shared ``small_dataset``."""

from fastapi.testclient import TestClient


def test_compare_chats(client: TestClient) -> None:
    r = client.get("/api/compare?chats=0,1")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "chats"
    assert [i["key"] for i in body["items"]] == ["chat:0", "chat:1"]
    assert body["items"][1]["is_group"] is True
    m = {x["key"]: x["values"] for x in body["metrics"]}
    assert m["messages"] == [12, 5]
    assert m["participants"] == [2, 4]
    assert m["my_share"][0] == 6 / 12
    assert m["calls"] == [1, 0]
    assert m["call_ms"] == [312_000, 0]
    mpd = body["messages_per_day"]
    assert mpd["x"] == ["2024-03-01", "2024-04-01"]
    assert mpd["series"][1]["values"][0] == 0.0
    assert mpd["series"][0]["values"][0] == 6 / 31
    for chart in (body["hours"], body["weekdays"]):
        for s in chart["series"]:
            assert abs(sum(s["values"]) - 1) < 1e-9
    assert len(body["hours"]["categories"]) == 24
    assert body["reply_median"]["unit"] == "ms"


def test_compare_people(client: TestClient) -> None:
    body = client.get("/api/compare?people=0,1").json()
    assert body["mode"] == "people"
    assert [i["person"]["person_id"] for i in body["items"]] == [0, 1]
    m = {x["key"]: x["values"] for x in body["metrics"]}
    assert m["messages"] == [7, 7]
    assert m["unanswered_turns"] == [3.0, 2.0]
    assert m["initiation_rate"] == [0.6, 0.6]
    lower = {x["key"]: x["lower_is_better"] for x in body["metrics"]}
    assert lower["reply_median_ms"] is True


def test_compare_validation(client: TestClient) -> None:
    for q in ("", "chats=1", "chats=0,1&people=1,2", "chats=a,b", "people=0,1,2,3,4", "chats=1,1"):
        r = client.get(f"/api/compare?{q}")
        assert r.status_code == 422, q
        assert r.json()["error"]["code"] == "validation_error"
    assert client.get("/api/compare?chats=0,99").status_code == 404
    assert client.get("/api/compare?people=0,99").status_code == 404
