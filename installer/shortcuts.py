"""Desktop icons on Windows, macOS, Linux and WSL. Owner: shortcuts agent.

See docs/plans/cross-platform-installer.md, "Desktop icon on each OS" and case C.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ShortcutResult:
    created: list[Path] = field(default_factory=list)  # every file written (for the record)
    messages: list[str] = field(default_factory=list)  # summary lines, e.g. "Desktop icon: updated"


def create_shortcuts() -> ShortcutResult:
    """Create or update this copy's shortcuts (asks on conflicts, see case C)."""
    raise NotImplementedError


def remove_shortcuts(paths: Iterable[str | Path]) -> list[str]:
    """Remove shortcuts listed in the install record. Returns summary lines."""
    raise NotImplementedError
