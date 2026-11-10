"""Find Node.js, or download a private copy into backend/.venv/node. Owner: node agent.

See docs/plans/cross-platform-installer.md, section "Private Node.js".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: Major version downloaded when no suitable system Node exists (current LTS).
NODE_MAJOR = 24


@dataclass(frozen=True)
class NodeTool:
    node: Path  # the node executable
    npm: Path  # the npm executable (npm.cmd on Windows)
    bin_dir: Path  # folder to put first on PATH when running npm
    version: tuple[int, int, int]
    private: bool  # True if it is the copy in backend/.venv/node


def node_version_ok(version: tuple[int, int, int]) -> bool:
    """True if Vite can use this Node: ^20.19.0 || >=22.12.0 (frontend/node_modules/vite)."""
    raise NotImplementedError


def find_system_node() -> NodeTool | None:
    """Node on PATH, if its version is OK."""
    raise NotImplementedError


def find_private_node() -> NodeTool | None:
    """The private Node in backend/.venv/node, if present, working and of NODE_MAJOR."""
    raise NotImplementedError


def ensure_node(*, force_private: bool = False) -> NodeTool:
    """A usable Node: system (unless force_private), else private, else download (asks first).

    Raises InstallError if the user declines or no official build exists for this system.
    """
    raise NotImplementedError
