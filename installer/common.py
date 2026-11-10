"""Shared paths, OS detection, processes, HTTP probes and state files.

Used by ``install.py`` and ``run.py``. Standard library only; works on Windows, macOS,
Linux and WSL. See ``docs/plans/cross-platform-installer.md``.
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import shlex
import socket
import subprocess
import sys
import tempfile
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from installer import ui

# --- Paths ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
FRONTEND_DIR = REPO_ROOT / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"
DIST_INDEX = DIST_DIR / "index.html"
NODE_MODULES_DIR = FRONTEND_DIR / "node_modules"
VENV_DIR = BACKEND_DIR / ".venv"
DATA_DIR = REPO_ROOT / "data"
ASSETS_DIR = REPO_ROOT / "assets"
RUN_PY = REPO_ROOT / "run.py"

#: Written by run.py while the server runs: {"pid", "port", "started"}.
RUN_FILE = VENV_DIR / "instagraph-run.json"
#: Written by install.py: what it created (shortcuts etc.), for re-runs and --uninstall.
INSTALL_RECORD = VENV_DIR / "instagraph-install.json"
#: Node.js downloaded by install.py when no suitable system Node exists.
PRIVATE_NODE_DIR = VENV_DIR / "node"

APP_NAME = "Instagraph"
DEFAULT_PORT = 8000
MIN_PYTHON = (3, 12)

# --- OS detection --------------------------------------------------------------------

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def is_wsl() -> bool:
    """True when running inside Windows Subsystem for Linux."""
    if not IS_LINUX:
        return False
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        return False


def os_label() -> str:
    """Human-readable OS name for messages: Windows, macOS, WSL or Linux."""
    if IS_WINDOWS:
        return "Windows"
    if IS_MACOS:
        return "macOS"
    return "WSL" if is_wsl() else "Linux"


def cpu_arch() -> Literal["x64", "arm64", "other"]:
    """The real CPU architecture, also when Python runs emulated.

    Windows on ARM running x64 Python reports AMD64, and x64 Python under Rosetta on
    Apple Silicon reports x86_64, so both are checked further.
    """
    machine = platform.machine().lower()
    if IS_WINDOWS:
        for var in ("PROCESSOR_ARCHITEW6432", "PROCESSOR_ARCHITECTURE"):
            value = os.environ.get(var, "").lower()
            if value == "arm64":
                return "arm64"
    if IS_MACOS and machine == "x86_64":
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            out = subprocess.run(
                ["sysctl", "-n", "hw.optional.arm64"], capture_output=True, text=True, timeout=5
            )
            if out.stdout.strip() == "1":
                return "arm64"
    if machine in ("x86_64", "amd64", "x64"):
        return "x64"
    # armv8l is a 32-bit ARM system on a 64-bit kernel, so it is not arm64.
    if machine in ("arm64", "aarch64"):
        return "arm64"
    return "other"


# --- Virtual environment -------------------------------------------------------------


def venv_bin_dir(venv: Path = VENV_DIR, *, windows: bool = IS_WINDOWS) -> Path:
    return venv / ("Scripts" if windows else "bin")


def venv_python(venv: Path = VENV_DIR, *, windows: bool = IS_WINDOWS) -> Path:
    """The Python interpreter inside a venv (``Scripts\\python.exe`` or ``bin/python``)."""
    return venv_bin_dir(venv, windows=windows) / ("python.exe" if windows else "python")


# --- Errors and processes ------------------------------------------------------------


class InstallError(Exception):
    """A problem the user can act on. Printed as one clear message plus an optional hint."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class CommandError(InstallError):
    """A command exited with a non-zero code. ``output`` holds captured stdout+stderr."""

    def __init__(
        self, message: str, *, returncode: int, output: str = "", hint: str | None = None
    ) -> None:
        super().__init__(message, hint)
        self.returncode = returncode
        self.output = output


def format_cmd(cmd: Sequence[str | Path]) -> str:
    parts = [str(c) for c in cmd]
    return subprocess.list2cmdline(parts) if IS_WINDOWS else shlex.join(parts)


def run(
    cmd: Sequence[str | Path],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a command.

    ``capture=False`` streams output to the console. ``capture=True`` collects stdout and
    stderr together in ``.stdout``. With ``check`` a non-zero exit raises
    :class:`CommandError`; a missing program raises :class:`InstallError`.
    """
    ui.detail(f"$ {format_cmd(cmd)}" + (f"   (in {cwd})" if cwd else ""))
    try:
        result = subprocess.run(
            [str(c) for c in cmd],
            cwd=cwd,
            env=dict(env) if env is not None else None,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
        )
    except FileNotFoundError as exc:
        raise InstallError(f"Could not run {cmd[0]}: program not found.") from exc
    except OSError as exc:
        raise InstallError(f"Could not run {cmd[0]}: {exc}") from exc
    if check and result.returncode != 0:
        raise CommandError(
            f"This command failed (exit code {result.returncode}): {format_cmd(cmd)}",
            returncode=result.returncode,
            output=result.stdout or "",
        )
    return result


def run_tee(
    cmd: Sequence[str | Path],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    show: Callable[[str], bool] = lambda line: True,
) -> str:
    """Run a command, print only the output lines ``show`` accepts, and return all output.

    With ``--verbose`` every line is printed. A non-zero exit raises :class:`CommandError`
    with the full output, so the caller can show its tail.
    """
    ui.detail(f"$ {format_cmd(cmd)}" + (f"   (in {cwd})" if cwd else ""))
    lines: list[str] = []
    try:
        proc = subprocess.Popen(
            [str(c) for c in cmd],
            cwd=cwd,
            env=dict(env) if env is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise InstallError(f"Could not run {cmd[0]}: program not found.") from exc
    except OSError as exc:
        raise InstallError(f"Could not run {cmd[0]}: {exc}") from exc
    assert proc.stdout is not None
    with proc:
        for raw in proc.stdout:
            line = raw.rstrip()
            lines.append(line)
            if ui.verbose() or show(line):
                ui.info(line)
    output = "\n".join(lines)
    if proc.returncode != 0:
        raise CommandError(
            f"This command failed (exit code {proc.returncode}): {format_cmd(cmd)}",
            returncode=proc.returncode,
            output=output,
        )
    return output


def env_with_path(prepend: Path, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """A copy of the environment with ``prepend`` first on PATH.

    On Windows the variable may be spelled ``Path``; the existing spelling is kept.
    """
    env = dict(os.environ if base is None else base)
    key = next((k for k in env if k.upper() == "PATH"), "PATH")
    env[key] = str(prepend) + os.pathsep + env.get(key, "")
    return env


def pid_alive(pid: int) -> bool:
    """True if a process with this ID is running."""
    if pid <= 0:
        return False
    if IS_WINDOWS:
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


# --- Network probes ------------------------------------------------------------------

#: An opener that never uses HTTP(S)_PROXY: our probes only go to this computer.
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

ProbeResult = Literal["instagraph", "other", "none"]


def port_in_use(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    """True if something accepts TCP connections on ``host:port``."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def probe_instagraph(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> ProbeResult:
    """What is listening on ``host:port``?

    ``"instagraph"``: an Instagraph server (``/openapi.json`` has ``info.title`` Instagraph).
    ``"other"``: something else accepts connections there. ``"none"``: nothing listens.
    """
    url = f"http://{host}:{port}/openapi.json"
    try:
        with _LOCAL_OPENER.open(url, timeout=timeout) as resp:
            body = json.loads(resp.read(2_000_000).decode("utf-8"))
        title = body.get("info", {}).get("title") if isinstance(body, dict) else None
        return "instagraph" if title == APP_NAME else "other"
    except (urllib.error.HTTPError, ValueError, UnicodeDecodeError):
        return "other"
    except (urllib.error.URLError, OSError, TimeoutError):
        return "other" if port_in_use(port, host) else "none"


# --- Files ---------------------------------------------------------------------------


def atomic_write_text(path: Path, text: str) -> None:
    """Write a file so readers never see half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def read_json(path: Path) -> dict[str, Any] | None:
    """A JSON object from ``path``, or None if missing or invalid."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# Run file (run.py) -------------------------------------------------------------------


def write_run_file(pid: int, port: int, path: Path = RUN_FILE) -> None:
    atomic_write_text(
        path, json.dumps({"pid": pid, "port": port, "started": now_iso()}, indent=2) + "\n"
    )


def read_run_file(path: Path = RUN_FILE) -> dict[str, Any] | None:
    """The run file if it is valid (``pid`` and ``port`` are ints), else None."""
    data = read_json(path)
    if data is None:
        return None
    if not isinstance(data.get("pid"), int) or not isinstance(data.get("port"), int):
        return None
    return data


def remove_run_file(path: Path = RUN_FILE, *, pid: int | None = None) -> None:
    """Delete the run file. With ``pid``, only if it still belongs to that process."""
    if pid is not None:
        data = read_run_file(path)
        if data is not None and data["pid"] != pid:
            return
    with contextlib.suppress(OSError):
        path.unlink()


# Install record (install.py) ---------------------------------------------------------
#
# {"version": 1, "repo": "<abs path>", "updated": "<iso>",
#  "shortcuts": ["<abs path>", ...], "ai": true|false, "node": "private"|"system"|null}


def read_install_record(path: Path = INSTALL_RECORD) -> dict[str, Any]:
    return read_json(path) or {}


def write_install_record(record: Mapping[str, Any], path: Path = INSTALL_RECORD) -> None:
    data = {"version": 1, "repo": str(REPO_ROOT), **record, "updated": now_iso()}
    atomic_write_text(path, json.dumps(data, indent=2) + "\n")


# --- Configuration -------------------------------------------------------------------


def configured_port(env_file: Path = BACKEND_DIR / ".env") -> int:
    """``INSTAGRAPH_PORT`` from the environment or ``backend/.env``, else 8000."""
    value = os.environ.get("INSTAGRAPH_PORT")
    if value is None:
        with contextlib.suppress(OSError):
            for line in env_file.read_text(encoding="utf-8").splitlines():
                key, sep, raw = line.strip().partition("=")
                if sep and key.strip() == "INSTAGRAPH_PORT":
                    value = raw.strip().strip("'\"")
    try:
        port = int(value) if value else DEFAULT_PORT
    except ValueError:
        return DEFAULT_PORT
    return port if 0 < port < 65536 else DEFAULT_PORT


def backend_version() -> str:
    """``version`` from ``backend/pyproject.toml``."""
    with (BACKEND_DIR / "pyproject.toml").open("rb") as fh:
        return str(tomllib.load(fh)["project"]["version"])
