from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from instagraph.api.routes import settings as settings_routes
from instagraph.errors import ApiError
from instagraph.store.app_settings import AppSettings
from instagraph.store.dataset import Dataset
from instagraph.testing import build_dataset, make_client


def test_get_without_data(empty_client: TestClient) -> None:
    body = empty_client.get("/api/settings").json()
    assert body["settings"]["session_gap_min"] == 60 and body["rederived"] is False
    r = empty_client.put("/api/settings", json={"theme": "dark"})
    assert r.status_code == 200 and r.json()["settings"]["theme"] == "dark"


def test_partial_update_persists_across_clients(tmp_path: Path, small_dataset: Dataset) -> None:
    data = tmp_path / "data"
    with make_client(data, small_dataset) as c:
        r = c.put("/api/settings", json={"timezone": "Europe/Bratislava", "ignored_words": ["x"]})
        assert r.status_code == 200
        assert r.json()["settings"]["timezone"] == "Europe/Bratislava"
        assert r.json()["settings"]["session_gap_min"] == 60  # untouched
    with make_client(data) as c2:
        s = c2.get("/api/settings").json()["settings"]
        assert s["timezone"] == "Europe/Bratislava" and s["ignored_words"] == ["x"]


def test_full_update(client: TestClient) -> None:
    full = AppSettings(theme="light", stopwords_added={"sk": ["no"]}).model_dump(mode="json")
    r = client.put("/api/settings", json=full)
    assert r.status_code == 200 and r.json()["settings"] == full


def test_validation(client: TestClient) -> None:
    for body in (
        {"timezone": "Mars/Base"},
        {"session_gap_min": 0},
        {"laughter_patterns": ["(unclosed"]},
        {"owner_person_id": 999},
        {"no_such_field": 1},
        {"theme": "pink"},
    ):
        r = client.put("/api/settings", json=body)
        assert r.status_code == 422, body
        assert r.json()["error"]["code"] == "validation_error"
    assert client.get("/api/settings").json()["settings"]["timezone"] is None


def test_query_time_change_bumps_version(client: TestClient) -> None:
    v0 = client.get("/api/status").json()["dataset_version"]
    r = client.put("/api/settings", json={"emoji_merge_skin_tones": False}).json()
    assert r["rederived"] is False and r["dataset_version"] == v0 + 1
    # same value again: no change, no bump
    assert (
        client.put("/api/settings", json={"emoji_merge_skin_tones": False}).json()[
            "dataset_version"
        ]
        == v0 + 1
    )
    # UI-only change: no bump
    assert client.put("/api/settings", json={"theme": "dark"}).json()["dataset_version"] == v0 + 1
    assert client.get("/api/messages").json()["dataset_version"] == v0 + 1


def test_derived_change_rederives(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, small_dataset: Dataset
) -> None:
    calls: list[AppSettings] = []

    def fake_rederive(ds: Dataset, settings: AppSettings) -> Dataset:
        calls.append(settings)
        # drop the last message to prove the new dataset was installed
        msgs = ds.messages.collect().head(19)
        return Dataset.from_frames(
            people=ds.people.collect(),
            threads=ds.threads.collect(),
            messages=msgs,
            reactions=ds.reactions.collect(),
        )

    monkeypatch.setattr(settings_routes, "_load_rederive", lambda: fake_rederive)
    v0 = client.get("/api/status").json()["dataset_version"]
    r = client.put("/api/settings", json={"session_gap_min": 30}).json()
    assert r["rederived"] is True and r["dataset_version"] > v0
    assert len(calls) == 1 and calls[0].session_gap_min == 30
    assert client.get("/api/status").json()["counts"]["messages"] == 19


def test_derived_change_without_pipeline_bumps(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings_routes, "_load_rederive", lambda: None)
    v0 = client.get("/api/status").json()["dataset_version"]
    r = client.put("/api/settings", json={"reply_window_h": 6}).json()
    assert r["rederived"] is False and r["dataset_version"] == v0 + 1


def test_rederive_failure_reverts(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(ds: Dataset, settings: AppSettings) -> Dataset:
        raise RuntimeError("nope")

    monkeypatch.setattr(settings_routes, "_load_rederive", lambda: boom)
    with pytest.raises(RuntimeError):
        client.put("/api/settings", json={"session_gap_min": 5})
    assert client.get("/api/settings").json()["settings"]["session_gap_min"] == 60


def test_people_and_threads_helpers(client: TestClient) -> None:
    people = client.get("/api/settings/people").json()["items"]
    jana = next(p for p in people if p["person"]["person_id"] == 1)
    assert jana["aliases"] == ["Jana", "Jana Nováková"] and jana["message_count"] == 8
    threads = client.get("/api/settings/threads").json()["items"]
    assert threads[0]["folder"] == "jananovakova_1" and len(threads[0]["participants"]) == 2


def test_rederive_api_error_is_reported(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reimport(ds: Dataset, settings: AppSettings) -> Dataset:
        raise ApiError("reimport_required", "Re-import needed.", 409)

    monkeypatch.setattr(settings_routes, "_load_rederive", lambda: reimport)
    r = client.put("/api/settings", json={"people_merges": [{"names": ["Jana", "Eva"]}]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "reimport_required"
    assert client.get("/api/settings").json()["settings"]["people_merges"] == []


def test_anonymize_redacts_and_leaks_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings_routes, "_load_rederive", lambda: None)
    client.put(
        "/api/settings",
        json={
            "owner_name": "Me",
            "people_merges": [{"names": ["Jana", "Jana Nováková"]}],
            "thread_merges": [{"folders": ["jananovakova_1", "partia_2"]}],
            "custom_terms": {"jananovakova_1": ["Jana"]},
        },
    )
    v = client.put("/api/settings", json={"anonymize": True}).json()
    assert v["redacted"] is True
    texts = [
        client.get("/api/settings").text,
        client.get("/api/settings/people").text,
        client.get("/api/settings/threads").text,
        client.get("/api/messages", params={"limit": 500}).text,
        client.get("/api/export/all.json", params={"include_messages": True}).text,
        client.get("/api/status").text,
    ]
    for text in texts:
        for secret in ("Jana", "Peter", "Eva", "Partia", "jananovakova", "Ahoj", "Nováková"):
            assert secret not in text, (secret, text[:200])
    # sending the redacted document back keeps the real values
    shown = client.get("/api/settings").json()["settings"]
    assert client.put("/api/settings", json=shown).status_code == 200
    real = client.put("/api/settings", json={"anonymize": False}).json()["settings"]
    assert real["people_merges"][0]["names"] == ["Jana", "Jana Nováková"]
    assert real["custom_terms"] == {"jananovakova_1": ["Jana"]}
    assert real["thread_merges"][0]["folders"] == ["jananovakova_1", "partia_2"]


def test_owner_override_validated_against_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings_routes, "_load_rederive", lambda: None)
    ds = build_dataset(
        people=[{"display_name": "A", "is_owner": True}, {"display_name": "B"}],
        threads=[{"participants": [0, 1]}],
        messages=[{"thread_id": 0, "sender_id": 1, "ts": "2024-01-01T00:00:00Z", "text": "x"}],
    )
    with make_client(tmp_path / "d", ds) as c:
        assert c.put("/api/settings", json={"owner_person_id": 1}).status_code == 200
        people = c.get("/api/settings/people").json()["items"]
        assert next(p for p in people if p["person"]["is_owner"])["person"]["person_id"] == 1
