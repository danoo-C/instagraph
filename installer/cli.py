"""install.py: argument parsing and the install / update / uninstall flow. Owner: core agent.

See docs/plans/cross-platform-installer.md, "install.py flow", "Existing installs",
"--uninstall".
"""

from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    """Entry point of install.py. Returns the process exit code."""
    raise NotImplementedError
