from pathlib import Path

from instagraph.store.app_settings import AppSettings, AppSettingsStore, load_app_settings


def test_defaults_and_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    s = load_app_settings(path)
    assert s.session_gap_min == 60 and s.reply_window_h == 12
    assert s.emoji_merge_skin_tones and not s.emoji_merge_gendered and not s.anonymize
    store = AppSettingsStore(path)
    store.update(s.model_copy(update={"session_gap_min": 30}))
    assert AppSettingsStore(path).get().session_gap_min == 30
    store.reset()
    assert not path.exists()


def test_settings_hash_ignores_ui_fields() -> None:
    a = AppSettings()
    assert a.settings_hash() == AppSettings(theme="dark", ui_language="sk").settings_hash()
    assert a.settings_hash() != AppSettings(session_gap_min=30).settings_hash()


def test_corrupt_file_gives_defaults(tmp_path: Path) -> None:
    (tmp_path / "settings.json").write_text("{nope", encoding="utf-8")
    assert load_app_settings(tmp_path / "settings.json") == AppSettings()
