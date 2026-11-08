"""Process configuration (environment variables, spec §8.4).

All variables use the ``INSTAGRAPH_`` prefix and may also come from a ``.env`` file
in the working directory::

    INSTAGRAPH_DATA_DIR      <repo>/data
    INSTAGRAPH_HOST          127.0.0.1   (a loud warning is logged for anything else)
    INSTAGRAPH_PORT          8000
    INSTAGRAPH_WORKERS       CPU count - 1 (parse process pool size)
    INSTAGRAPH_CORS_ORIGINS  http://localhost:5173  (comma-separated; dev only)
    INSTAGRAPH_ALLOWED_HOSTS (empty)     extra accepted ``Host`` names (comma-separated);
                                         localhost/127.0.0.1/::1 are always accepted

User-editable, persisted preferences live in :mod:`instagraph.store.app_settings`,
not here.
"""

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

logger = logging.getLogger("instagraph")

#: ``<repo>`` = the directory containing ``backend/`` and ``frontend/``.
REPO_ROOT: Path = Path(__file__).resolve().parents[3]

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _default_workers() -> int:
    return max(1, (os.cpu_count() or 2) - 1)


class Settings(BaseSettings):
    """Process-level configuration, read once at startup."""

    model_config = SettingsConfigDict(env_prefix="INSTAGRAPH_", env_file=".env", extra="ignore")

    data_dir: Path = Field(default_factory=lambda: REPO_ROOT / "data")
    host: str = "127.0.0.1"
    port: int = 8000
    workers: int = Field(default_factory=_default_workers, ge=1)
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )
    #: Extra hostnames accepted in the ``Host`` header (DNS-rebinding protection).
    allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)
    #: Built frontend served at ``/`` when the directory exists.
    frontend_dist: Path = Field(default_factory=lambda: REPO_ROOT / "frontend" / "dist")

    @field_validator("cors_origins", "allowed_hosts", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [o.strip() for o in value.split(",") if o.strip()]
        return value

    @field_validator("data_dir", "frontend_dist", mode="after")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @property
    def is_local_host(self) -> bool:
        return self.host in LOCAL_HOSTS

    @property
    def trusted_hosts(self) -> frozenset[str]:
        """Lower-cased hostnames (no port, no brackets) accepted in the ``Host`` header."""
        names = set(LOCAL_HOSTS) | {h.strip("[]").lower() for h in self.allowed_hosts}
        if self.host not in ("0.0.0.0", "::", ""):
            names.add(self.host.strip("[]").lower())
        return frozenset(names)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings` (cached)."""
    return Settings()


def ensure_data_dir(data_dir: Path) -> Path:
    """Create the data directory if needed and guarantee its ``.gitignore`` (decision D5).

    The ``.gitignore`` containing ``*`` is (re)written whenever it is missing, so private
    data stays ignored even if the folder is moved or the root ``.gitignore`` changes.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    gitignore = data_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")
    return data_dir


def warn_if_public_host(settings: Settings) -> None:
    """Log a loud warning if the server is configured to bind to a non-local address."""
    if settings.is_local_host:
        return
    banner = "!" * 78
    logger.warning(
        "\n%s\n  WARNING: INSTAGRAPH_HOST=%r is not localhost.\n"
        "  Your private messages may be reachable from other machines on the network.\n"
        "  Use 127.0.0.1 unless you really know what you are doing.\n%s",
        banner,
        settings.host,
        banner,
    )
