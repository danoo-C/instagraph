from fastapi.testclient import TestClient


def test_export_all(client: TestClient) -> None:
    r = client.get("/api/export/all.json")
    assert r.status_code == 200
    assert r.headers["content-disposition"] == 'attachment; filename="instagraph-all.json"'
    body = r.json()
    assert body["scope"] == "all" and body["messages"] is None and body["chat"] is None
    t = body["totals"]
    assert t["messages"] == 17 and t["likes"] == 1 and t["calls"] == 1
    assert t["call_duration_s"] == 312 and t["photos"] == 2 and t["shares"] == 1
    assert t["reactions"] == 5 and t["first_date"] == "2024-03-30" and t["span_days"] == 8
    assert sum(p["messages"] for p in body["people"]) == 17
    assert [c["thread_id"] for c in body["chats"]] == [0, 1]  # requests excluded by default
    assert sum(body["hours"]) == 17 and len(body["weekdays"]) == 7
    emojis = {e["key"]: e["count"] for e in body["top_emojis"]}
    assert emojis["😂"] == 3 and emojis["👍"] == 1  # skin tone merged
    me = next(p for p in body["people"] if p["person"]["person_id"] == 0)
    assert me["reactions_given"] == 3


def test_export_chat_with_messages(client: TestClient) -> None:
    body = client.get("/api/export/chat-1.json", params={"include_messages": True}).json()
    assert body["chat"]["title"] == "Partia" and body["chat"]["is_group"]
    assert body["totals"]["messages"] == 5 and body["chats"] == []
    assert [m["message_id"] for m in body["messages"]] == [7, 9, 10, 11, 19]
    assert body["messages"][0]["text"] == "Čau všetci 👋"


def test_export_filters_and_errors(client: TestClient) -> None:
    body = client.get("/api/export/all.json", params={"people": "1"}).json()
    assert (
        body["totals"]["messages"] == 7
        and body["totals"]["likes"] == 1
        and body["filters"]["people"] == [1]
    )
    assert client.get("/api/export/chat-99.json").status_code == 404
    assert client.get("/api/export/bogus.json").status_code == 404


def test_export_no_data(empty_client: TestClient) -> None:
    assert empty_client.get("/api/export/all.json").status_code == 409
