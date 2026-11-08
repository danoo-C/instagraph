"""User settings persisted at ``<data_dir>/settings.json`` (spec §5.11).

``AppSettings`` is the full settings document (``GET``/``PUT /api/settings`` will use
it directly). :meth:`AppSettings.settings_hash` covers only fields that can change
computed stats (it is part of every cache key); UI-only fields (theme, UI language)
are excluded.

People and threads are referenced by **name / folder** where a reference must survive
a re-import (ids are reassigned on every import), and by ``person_id`` for the
owner override in the current dataset.
"""

import hashlib
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_LAUGHTER_PATTERNS: list[str] = [
    r"\b(?:a?ha){2,}h?\b",
    r"\b(?:e?he){2,}h?\b",
    r"\bx+d+\b",
    r"\blol+\b",
    r"\blmao+\b",
    r"\brofl\b",
    "😂",
    "🤣",
    "💀",
    "😆",
]

#: Fields that do not influence computed stats (excluded from ``settings_hash``).
UI_ONLY_FIELDS: frozenset[str] = frozenset({"theme", "ui_language"})


class PeopleMerge(BaseModel):
    """Treat several display names as one person (e.g. "Jana" + "Jana Nováková")."""

    names: list[str] = Field(min_length=2)
    display_name: str | None = None  # None = use the most recent name


class ThreadMerge(BaseModel):
    """Merge these thread folders into one chat."""

    folders: list[str] = Field(min_length=2)


class ThreadSplit(BaseModel):
    """Keep these thread folders separate even if auto-merge would join them."""

    folders: list[str] = Field(min_length=2)


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    # identity
    owner_person_id: int | None = None
    """Owner override for the current dataset (``None`` = auto-detected owner)."""
    owner_name: str | None = None
    """Owner override by display name; survives re-imports. Used by the importer."""
    people_merges: list[PeopleMerge] = Field(default_factory=list)
    thread_merges: list[ThreadMerge] = Field(default_factory=list)
    thread_splits: list[ThreadSplit] = Field(default_factory=list)

    # time
    timezone: str | None = None
    """IANA zone used when a request has no ``tz`` (``None`` = UTC fallback)."""
    session_gap_min: int = Field(default=60, ge=1, le=7 * 24 * 60)
    reply_window_h: float = Field(default=12.0, gt=0, le=24 * 30)

    # system-message languages
    enabled_locales: list[str] | None = None
    """``None`` = use the auto-detected locales from the import report."""

    # text
    stopwords_added: dict[str, list[str]] = Field(default_factory=dict)
    """Per language code (``en``/``sk``/``cs``): words added to the built-in list."""
    stopwords_removed: dict[str, list[str]] = Field(default_factory=dict)
    """Per language code: built-in stop-words to ignore."""
    ignored_words: list[str] = Field(default_factory=list)
    laughter_patterns: list[str] = Field(default_factory=lambda: list(DEFAULT_LAUGHTER_PATTERNS))
    """Regexes (case-insensitive) or literal emoji counting as laughter (§6.5)."""
    swear_words_enabled: bool = False
    swear_words: list[str] = Field(default_factory=list)
    custom_terms: dict[str, list[str]] = Field(default_factory=dict)
    """Saved term-tracker terms keyed by thread folder (``"*"`` = global/overview)."""

    # emoji
    emoji_merge_skin_tones: bool = True
    emoji_merge_gendered: bool = False

    # privacy / UI
    anonymize: bool = False
    theme: Literal["system", "light", "dark"] = "system"
    ui_language: Literal["en", "sk"] = "en"

    def settings_hash(self) -> str:
        """Short stable hash of all stat-affecting fields (used in cache keys)."""
        payload = self.model_dump(mode="json", exclude=set(UI_ONLY_FIELDS))
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    @property
    def session_gap_ms(self) -> int:
        return self.session_gap_min * 60_000

    @property
    def reply_window_ms(self) -> int:
        return int(self.reply_window_h * 3_600_000)


def load_app_settings(path: Path) -> AppSettings:
    """Read settings from ``path``; missing or unreadable file → defaults."""
    try:
        return AppSettings.model_validate_json(path.read_bytes())
    except (OSError, ValueError):
        return AppSettings()


def save_app_settings(path: Path, settings: AppSettings) -> None:
    """Write settings atomically (temp file + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, path)


class AppSettingsStore:
    """Process-wide holder of the current :class:`AppSettings` (thread-safe)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._settings = load_app_settings(path)

    def get(self) -> AppSettings:
        return self._settings

    def update(self, new_settings: AppSettings) -> AppSettings:
        """Persist and install ``new_settings``.

        Callers changing derived-field settings (owner, merges, session gap, reply
        window) must also re-derive and call ``DatasetStore.bump_version()`` /
        ``replace()``.
        """
        with self._lock:
            save_app_settings(self.path, new_settings)
            self._settings = new_settings
            return new_settings

    def reset(self) -> AppSettings:
        with self._lock:
            self._settings = AppSettings()
            if self.path.exists():
                self.path.unlink()
            return self._settings


_settings_store: AppSettingsStore | None = None
_settings_lock = threading.Lock()


def get_settings_store() -> AppSettingsStore:
    """Process-wide settings store (created on first use under ``get_settings().data_dir``)."""
    global _settings_store
    with _settings_lock:
        if _settings_store is None:
            from instagraph.config import get_settings

            _settings_store = AppSettingsStore(get_settings().data_dir / "settings.json")
        return _settings_store


def set_settings_store(store: AppSettingsStore) -> None:
    global _settings_store
    with _settings_lock:
        _settings_store = store


def get_app_settings() -> AppSettings:
    """Current settings (FastAPI dependency-friendly)."""
    return get_settings_store().get()
