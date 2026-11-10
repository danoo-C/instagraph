"""Decide whether the frontend needs npm ci / a build, and run them. Owner: node agent.

See docs/plans/cross-platform-installer.md, "install.py flow" step 4 and case B.
"""

from __future__ import annotations

from dataclasses import dataclass

from installer.node import NodeTool


@dataclass(frozen=True)
class FrontendState:
    needs_install: bool  # run npm ci (node_modules missing, outdated, or from another OS)
    needs_build: bool  # run npm run build
    reason: str  # human-readable, e.g. "src/app/Layout.tsx changed since the last build"


def frontend_state(*, rebuild: bool = False) -> FrontendState:
    """Inspect frontend/ without running anything."""
    raise NotImplementedError


def build_frontend(node: NodeTool, state: FrontendState) -> str:
    """Run npm ci (if state.needs_install) and npm run build. Returns a one-line summary."""
    raise NotImplementedError
