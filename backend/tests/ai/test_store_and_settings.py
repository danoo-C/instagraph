"""Conversation storage (docs/ai-assistant.md A8.7) and AI settings (A8.6)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from instagraph.ai.store import ConversationStore, Turn
from instagraph.errors import NotFoundError
from instagraph.store.app_settings import AI_FIELDS, UI_ONLY_FIELDS, AppSettings


def make(store: ConversationStore, **kw: object):  # type: ignore[no-untyped-def]
    args = {
        "provider": "fake",
        "model": "m",
        "effort": "medium",
        "hide_names": True,
        "scope": ["b_2", "a_1", "a_1"],
    } | kw
    return store.create(**args)  # type: ignore[arg-type]


def test_create_get_list_delete(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    a = make(store)
    assert a.scope == ["a_1", "b_2"]
    assert not a.names_locked
    a.turns.append(Turn(user_text="hi", answer="[P1] says hi", status="done"))
    store.save(a)
    b = make(store, hide_names=False)
    got = store.get(a.id)
    assert got.turns[0].answer == "[P1] says hi" and got.names_locked
    listed = store.list()
    assert [s.id for s in listed] == [b.id, a.id]
    assert listed[1].turns == 1 and listed[1].scope_size == 2
    store.delete(a.id)
    with pytest.raises(NotFoundError):
        store.get(a.id)
    assert store.delete_all() == 1
    assert store.list() == []


def test_ids_are_validated(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    for bad in ("../../etc/passwd", "", "ABC", "x" * 5):
        with pytest.raises(NotFoundError):
            store.get(bad)


def test_files_live_in_the_data_dir(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    conv = make(store)
    assert (tmp_path / "ai" / "conversations" / f"{conv.id}.json").is_file()
    assert not list((tmp_path / "ai" / "conversations").glob("*.tmp-*"))


# ---- settings --------------------------------------------------------------------------


def test_ai_is_off_by_default() -> None:
    s = AppSettings()
    assert s.ai_enabled is False
    assert s.ai_hide_names is True
    assert s.ai_default_scope == []
    assert AI_FIELDS <= UI_ONLY_FIELDS


def test_ai_settings_dont_change_stats_cache_key() -> None:
    a = AppSettings()
    b = AppSettings(ai_hide_names=False, ai_default_scope=["x_1"], ai_model="other")
    assert a.settings_hash() == b.settings_hash()


def test_turning_ai_on_needs_the_notice(client: TestClient) -> None:
    v0 = client.get("/api/status").json()["dataset_version"]
    r = client.put("/api/settings", json={"ai_enabled": True})
    assert r.status_code == 422
    r = client.put("/api/settings", json={"ai_enabled": True, "ai_confirmed_notice": True})
    assert r.status_code == 200
    body = r.json()
    assert body["settings"]["ai_enabled"] is True
    assert body["dataset_version"] == v0  # UI-only: no cache bust


def test_ai_name_fields_are_redacted_in_anonymize_mode(client: TestClient) -> None:
    client.put(
        "/api/settings",
        json={
            "ai_default_scope": ["jananovakova_1"],
            "ai_person_aliases": {"Jana Nováková": ["Janka"]},
            "ai_alias_disabled": ["Eva"],
        },
    )
    shown = client.put("/api/settings", json={"anonymize": True}).json()["settings"]
    assert shown["ai_default_scope"] == ["•••"]
    assert shown["ai_person_aliases"] == {}
    assert shown["ai_alias_disabled"] == ["•••"]
    # Sending the hidden values back keeps the real ones.
    client.put("/api/settings", json=shown | {"anonymize": False})
    real = client.get("/api/settings").json()["settings"]
    assert real["ai_default_scope"] == ["jananovakova_1"]
    assert real["ai_person_aliases"] == {"Jana Nováková": ["Janka"]}
