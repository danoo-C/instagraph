"""Case A: make sure Instagraph isn't running before install.py changes anything. Owner: core.

See docs/plans/cross-platform-installer.md, "Existing installs", case A. The installer never
stops the server itself: the user might be in the middle of an import.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from installer import common, ui

#: Shown when the server must be closed (also after a Windows "file in use" pip error).
CLOSE_HINT = "Close the Instagraph window (or press Ctrl+C in it), then run install.py again."


class Cancelled(Exception):
    """The user chose to stop. Nothing was changed."""


@dataclass(frozen=True)
class RunningServer:
    port: int
    #: True if found through run.py's run file (so it is this copy); False if only a port
    #: answered, which may be another copy started with ./launch or make dev.
    this_copy: bool

    def message(self) -> str:
        if self.this_copy:
            return f"Instagraph is running (port {self.port})."
        return f"An Instagraph server is running on port {self.port}."


def find_running(run_file: Path, ports: Iterable[int]) -> RunningServer | None:
    """The running Instagraph server, if any.

    A run file counts only if its process is alive and its port answers as Instagraph;
    otherwise it is stale and deleted. Then ``ports`` are probed, which catches servers
    started by ./launch or make dev (they write no run file).
    """
    data = common.read_run_file(run_file)
    if (
        data is not None
        and common.pid_alive(data["pid"])
        and common.probe_instagraph(data["port"]) == "instagraph"
    ):
        return RunningServer(port=data["port"], this_copy=True)
    if run_file.exists():
        ui.detail(f"Removing a stale run file: {run_file}")
        common.remove_run_file(run_file)
    for port in dict.fromkeys(ports):
        if common.probe_instagraph(port) == "instagraph":
            return RunningServer(port=port, this_copy=False)
    return None


def ensure_not_running(
    run_file: Path,
    ports: Iterable[int],
    *,
    wait: Callable[[str], bool] | None = None,
) -> None:
    """Return once Instagraph isn't running.

    Interactive: ask the user to close it and press Enter, then check again (Ctrl+C or
    Ctrl+D raises :class:`Cancelled`). With ``--yes`` or without a terminal: raise
    :class:`~installer.common.InstallError`.
    """
    port_list = list(ports)
    server = find_running(run_file, port_list)
    while server is not None:
        if not ui.interactive():
            raise common.InstallError(
                f"{server.message()} It has to be closed before installing, updating or "
                "uninstalling.",
                CLOSE_HINT,
            )
        ui.warn(server.message())
        if not (wait or ui.wait_for_enter)(
            "Close it, then press Enter to continue (Ctrl+C to cancel)."
        ):
            raise Cancelled
        server = find_running(run_file, port_list)
