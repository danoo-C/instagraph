"""``GET /api/wrapped`` and ``GET /api/wrapped/years`` on the shared ``small_dataset``."""

from fastapi.testclient import TestClient

from instagraph.store.app_settings import AppSettings, get_settings_store


def test_years(client: TestClient) -> None:
    r = client.get("/api/wrapped/years")
    assert r.status_code == 200
    assert r.json()["years"] == [{"year": 2024, "messages": 17}]
    assert client.get("/api/wrapped/years?chat=1").json()["years"] == [
        {"year": 2024, "messages": 5}
    ]
    assert client.get("/api/wrapped/years?chat=42").status_code == 404


def test_wrapped_all_chats(client: TestClient) -> None:
    # The date filter is overridden by the year.
    body = client.get("/api/wrapped?year=2024&start=2024-04-05").json()
    assert body["year"] == 2024 and body["available_years"] == [2024]
    assert body["total_messages"] == 17
    assert body["sent_by_me"] == 7 and body["received"] == 10
    assert sum(m["messages"] for m in body["months"]) == 17
    assert body["top_people"][0]["person"]["person_id"] == 1
    assert body["top_people"][0]["messages"] == 13
    assert body["top_emoji"]["emoji"] == "😂"
    assert body["busiest_day"]["drilldown"]["start"] == body["busiest_day"]["date"]
    assert body["longest_call"]["duration_ms"] == 312_000
    assert body["most_reacted_message"]["reactions"] == 2
    assert body["most_reacted_message"]["message"]["message_id"] == 7
    assert body["top_word"] is not None
    assert body["total_drilldown"]["start"] == "2024-01-01"
    assert body["personality"]["summary"].startswith("You are")
    keys = {a["key"] for a in body["personality"]["awards"]}
    assert {"caller", "second_thoughts"} <= keys


def test_wrapped_default_year_chat_and_empty_year(client: TestClient) -> None:
    body = client.get("/api/wrapped?chat=1").json()
    assert body["year"] == 2024 and body["chat_id"] == 1 and body["chat_title"] == "Partia"
    assert body["total_messages"] == 5
    assert body["longest_call"] is None
    empty = client.get("/api/wrapped?year=2019").json()
    assert empty["total_messages"] == 0 and empty["top_people"] == []
    assert client.get("/api/wrapped?chat=77").status_code == 404
    assert client.get("/api/wrapped?year=abc").status_code == 422


def test_wrapped_anonymized_hides_text(client: TestClient) -> None:
    get_settings_store().update(AppSettings(anonymize=True))
    body = client.get("/api/wrapped?year=2024").json()
    assert body["top_word"] is None and body["top_words"] == []
    assert body["most_reacted_message"]["message"]["text"] is None
