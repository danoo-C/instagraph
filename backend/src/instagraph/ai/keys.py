"""Anthropic API key storage (docs/ai-assistant.md A8.2, decision AD5).

``ANTHROPIC_API_KEY`` from the environment wins; otherwise ``<data_dir>/ai/api_key``
(file mode 0600), written by ``PUT /api/ai/key``. The key is never returned by any
endpoint (only whether it's set, where from, and its last 4 characters), never written
to ``settings.json``, never logged, and not part of exports. "Clear all data" deletes it
with the rest of the data directory.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ENV_VAR = "ANTHROPIC_API_KEY"
MAX_KEY_LENGTH = 500


@dataclass(frozen=True)
class KeyInfo:
    set: bool
    source: Literal["env", "file"] | None = None
    hint: str = ""
    """Last 4 characters, e.g. ``…a1b2``."""


def key_path(data_dir: Path) -> Path:
    return data_dir / "ai" / "api_key"


def get_api_key(data_dir: Path) -> str | None:
    env = os.environ.get(ENV_VAR, "").strip()
    if env:
        return env
    try:
        stored = key_path(data_dir).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return stored or None


def key_info(data_dir: Path) -> KeyInfo:
    if os.environ.get(ENV_VAR, "").strip():
        key, source = os.environ[ENV_VAR].strip(), "env"
    else:
        stored = get_api_key(data_dir)
        if stored is None:
            return KeyInfo(set=False)
        key, source = stored, "file"
    return KeyInfo(set=True, source=source, hint=f"…{key[-4:]}" if len(key) >= 8 else "…")  # type: ignore[arg-type]


def save_api_key(data_dir: Path, key: str) -> None:
    """Store ``key`` with owner-only permissions (written atomically)."""
    key = key.strip()
    if not key or len(key) > MAX_KEY_LENGTH or any(c.isspace() for c in key):
        raise ValueError("That doesn't look like an API key.")
    path = key_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def delete_api_key(data_dir: Path) -> bool:
    """Remove the stored key. Returns whether a file was removed (an env key stays)."""
    try:
        key_path(data_dir).unlink()
    except FileNotFoundError:
        return False
    return True
