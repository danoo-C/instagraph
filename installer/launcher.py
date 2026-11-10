"""run.py: start the Instagraph server and open the browser.

Cross-platform replacement for ``./launch`` (minus the frontend rebuild), used by the
desktop icon. See docs/plans/cross-platform-installer.md, section "run.py".

Flow: check the install, check whether Instagraph is already running, start
``<venv python> -m uvicorn instagraph.main:app`` in ``backend/``, wait until it answers,
write the run file, open the browser, then wait until the server stops (Ctrl+C or closing
the window).
"""

from __future__ import annotations

import argparse
import contextlib
import ipaddress
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
import traceback
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Literal

from installer import common, ui

#: Where the run file goes. A module constant so tests can point it elsewhere.
RUN_FILE: Path = common.RUN_FILE
#: How long to wait for the server to answer after starting it (seconds).
STARTUP_TIMEOUT = 20.0
#: How long to wait for the server to stop by itself after Ctrl+C before terminating it.
STOP_TIMEOUT = 10.0

#: Exit codes that mean "stopped by Ctrl+C / normally": 0, SIGINT on POSIX (-2, 130) and
#: STATUS_CONTROL_C_EXIT on Windows (0xC000013A, signed and unsigned).
_STOPPED_EXIT_CODES = frozenset({0, -int(signal.SIGINT), 130, 0xC000013A, -1073741510})

RunningState = Literal["ours", "instagraph", "other", "none"]


def _install_cmd() -> str:
    return "py install.py" if common.IS_WINDOWS else "python3 install.py"


def _run_cmd() -> str:
    return "py run.py" if common.IS_WINDOWS else "python3 run.py"


# --- Options -------------------------------------------------------------------------


@dataclass(frozen=True)
class Options:
    port: int
    lan: bool
    open_browser: bool


def _port_number(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"port must be a number, got '{value}'") from None
    if not 0 < port < 65536:
        raise argparse.ArgumentTypeError(f"port must be between 1 and 65535, got {port}")
    return port


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="Start Instagraph and open it in the browser.",
        epilog="Run install.py first. Developers can keep using ./launch.",
    )
    parser.add_argument(
        "-p",
        "--port",
        type=_port_number,
        default=None,
        help="port to use (default: PORT or INSTAGRAPH_PORT, backend/.env, else 8000)",
    )
    parser.add_argument(
        "-l",
        "--lan",
        action="store_true",
        help="also serve on the local network (phones, other computers)",
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="don't open the browser automatically"
    )
    return parser


def parse_args(argv: Sequence[str] | None, environ: Mapping[str, str] | None = None) -> Options:
    """Parse the command line. Port: ``--port``, then ``PORT``, then ``configured_port()``.

    Exits (``SystemExit``) on ``--help`` and on invalid options, like argparse does.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    env = os.environ if environ is None else environ
    port: int | None = args.port
    if port is None and env.get("PORT"):
        try:
            port = _port_number(env["PORT"])
        except argparse.ArgumentTypeError as exc:
            parser.error(f"PORT environment variable: {exc}")
    if port is None:
        port = common.configured_port()
    return Options(port=port, lan=args.lan, open_browser=not args.no_browser)


# --- Server environment --------------------------------------------------------------


def read_env_file(path: Path) -> dict[str, str]:
    """``KEY=value`` pairs from a dotenv file (``backend/.env``). Missing file: empty."""
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return values
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ")
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip().strip("'\"")
    return values


def _lookup(name: str, *sources: Mapping[str, str]) -> str | None:
    """First non-empty value of ``name`` (case-insensitive, like pydantic-settings)."""
    wanted = name.upper()
    for source in sources:
        for key, value in source.items():
            if key.upper() == wanted and value:
                return value
    return None


def lan_allowed_hosts(lan_ips: Sequence[str], hostname: str, existing: str | None) -> str:
    """``INSTAGRAPH_ALLOWED_HOSTS`` for LAN mode, as ``./launch`` builds it.

    The LAN addresses, the host name and ``<name>.local``, then whatever the user already
    allowed. Duplicates are dropped.
    """
    names: list[str] = list(lan_ips)
    if hostname:
        base = hostname.removesuffix(".local")
        names += [base, f"{base}.local"]
    if existing:
        names += [h.strip() for h in existing.split(",")]
    return ",".join(dict.fromkeys(n for n in names if n))


def build_server_env(
    base: Mapping[str, str],
    *,
    lan: bool,
    lan_ips: Sequence[str] = (),
    hostname: str = "",
    env_file: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """The environment for the uvicorn process.

    ``INSTAGRAPH_HOST`` is always set (like ``./launch``). The data and frontend folders are
    set to absolute paths unless the user set them in the environment or ``backend/.env``.
    """
    file_values = env_file or {}
    env = dict(base)
    env["INSTAGRAPH_HOST"] = "0.0.0.0" if lan else "127.0.0.1"
    for name, path in (
        ("INSTAGRAPH_DATA_DIR", common.DATA_DIR),
        ("INSTAGRAPH_FRONTEND_DIST", common.DIST_DIR),
    ):
        if _lookup(name, base, file_values) is None:
            env[name] = str(path)
    if lan:
        existing = _lookup("INSTAGRAPH_ALLOWED_HOSTS", base, file_values)
        env["INSTAGRAPH_ALLOWED_HOSTS"] = lan_allowed_hosts(lan_ips, hostname, existing)
    return env


def _dist_index(base: Mapping[str, str], env_file: Mapping[str, str]) -> Path:
    """The ``index.html`` the server will serve (honours a user-set frontend folder)."""
    custom = _lookup("INSTAGRAPH_FRONTEND_DIST", base, env_file)
    if custom is None:
        return common.DIST_INDEX
    path = Path(custom).expanduser()
    if not path.is_absolute():
        path = common.BACKEND_DIR / path  # the server runs in backend/
    return path / "index.html"


# --- LAN addresses -------------------------------------------------------------------


def _usable_lan_ip(ip: str) -> bool:
    try:
        addr = ipaddress.IPv4Address(ip)
    except ValueError:
        return False
    return not (addr.is_loopback or addr.is_unspecified or addr.is_link_local)


def lan_ipv4_addresses() -> list[str]:
    """This computer's IPv4 addresses that other devices may reach. Standard library only.

    The UDP-connect trick finds the address of the default route (no packet is sent);
    ``getaddrinfo(gethostname())`` adds the others. Loopback and 169.254.x are skipped.
    """
    found: list[str] = []
    for target in ("8.8.8.8", "10.255.255.255"):
        with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((target, 80))
            found.append(str(s.getsockname()[0]))
            break
    with contextlib.suppress(OSError, UnicodeError):
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(str(info[4][0]))
    return list(dict.fromkeys(ip for ip in found if _usable_lan_ip(ip)))


def wsl_lan_warning_needed(lan_ips: Sequence[str], *, wsl: bool) -> bool:
    """WSL2 in NAT mode only has an internal 172.16–31.x address other devices can't reach."""
    if not wsl:
        return False
    return not lan_ips or any(ipaddress.IPv4Address(ip) in _WSL_NAT_NET for ip in lan_ips)


_WSL_NAT_NET = ipaddress.IPv4Network("172.16.0.0/12")


# --- Browser -------------------------------------------------------------------------


def browser_command(
    url: str, *, wsl: bool, which: Callable[[str], str | None] | None = None
) -> list[str] | None:
    """The command that opens ``url`` under WSL, or None to use :mod:`webbrowser`.

    Under WSL ``webbrowser`` usually can't reach the Windows browser, so use ``wslview``,
    else ``cmd.exe /c start "" <url>`` (the empty argument is the window title).
    """
    if not wsl:
        return None
    which = which or shutil.which
    wslview = which("wslview")
    if wslview:
        return [wslview, url]
    cmd = which("cmd.exe")
    if cmd is None and Path("/mnt/c/Windows/System32/cmd.exe").exists():
        cmd = "/mnt/c/Windows/System32/cmd.exe"
    if cmd:
        return [cmd, "/c", "start", "", url]
    return None


def _has_display() -> bool:
    """False on a Linux box without a desktop, where webbrowser would pick a text browser
    (lynx, w3m) and take over this console."""
    if not common.IS_LINUX or common.is_wsl():
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def open_browser(url: str) -> bool:
    """Open ``url`` in the user's browser. Never raises; returns False if it failed."""
    try:
        cmd = browser_command(url, wsl=common.is_wsl())
        if cmd is None:
            return _has_display() and bool(webbrowser.open(url))
        # cmd.exe warns about UNC paths when started from a Linux folder; /mnt/c avoids it.
        cwd = "/mnt/c" if Path("/mnt/c").is_dir() else None
        result = subprocess.run(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except Exception:  # a browser problem must never stop the launcher
        return False
    return result.returncode == 0


def _open_and_report(url: str) -> bool:
    if open_browser(url):
        ui.info("Opened it in your browser.")
        return True
    ui.info(f"Could not open the browser. Open {url} in your browser yourself.")
    return False


# --- Already running -----------------------------------------------------------------


def find_running(port: int, run_file: Path | None = None) -> tuple[RunningState, int]:
    """Is Instagraph already running? Returns the state and the port it's on.

    ``"ours"``: the run file is valid, its process is alive and its port answers as
    Instagraph. Otherwise a stale run file is deleted and ``port`` is probed:
    ``"instagraph"`` (maybe another copy), ``"other"`` (another program) or ``"none"``.
    """
    path = RUN_FILE if run_file is None else run_file
    data = common.read_run_file(path)
    if (
        data is not None
        and common.pid_alive(data["pid"])
        and common.probe_instagraph(data["port"]) == "instagraph"
    ):
        return "ours", int(data["port"])
    if path.exists():
        common.remove_run_file(path)
    result = common.probe_instagraph(port)
    return result, port


# --- Running the server --------------------------------------------------------------


def _server_command(python: Path, host: str, port: int) -> list[str]:
    return [
        str(python),
        "-m",
        "uvicorn",
        "instagraph.main:app",
        "--host",
        host,
        "--port",
        str(port),
    ]


def _wait_until_ready(proc: subprocess.Popen[bytes], port: int, timeout: float) -> bool:
    """Poll until the server answers as Instagraph. False if it exited or timed out."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        if common.probe_instagraph(port) == "instagraph":
            return True
        time.sleep(0.25)
    return False


def _wait_for_exit(proc: subprocess.Popen[bytes]) -> int:
    # A short timeout in a loop lets Ctrl+C through on Windows too.
    while True:
        try:
            return proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            continue


def _stop(proc: subprocess.Popen[bytes]) -> int:
    """After Ctrl+C: the server usually got it too. Give it time, then terminate it."""
    if proc.poll() is None:
        ui.step("Stopping Instagraph…")
        with contextlib.suppress(subprocess.TimeoutExpired, KeyboardInterrupt):
            return proc.wait(timeout=STOP_TIMEOUT)
    return _terminate(proc)


def _terminate(proc: subprocess.Popen[bytes]) -> int:
    if proc.poll() is None:
        with contextlib.suppress(OSError):
            proc.terminate()
        try:
            return proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                proc.kill()
    return proc.wait()


def _install_stop_handlers(proc: subprocess.Popen[bytes]) -> None:
    """POSIX: treat SIGTERM / SIGHUP (terminal closed) like Ctrl+C, so the run file is removed.

    The server is asked to stop too, since it may not have received the signal.
    """
    if common.IS_WINDOWS or threading.current_thread() is not threading.main_thread():
        return

    def handler(signum: int, frame: FrameType | None) -> None:
        if proc.poll() is None:
            with contextlib.suppress(OSError):
                proc.terminate()
        raise KeyboardInterrupt

    for name in ("SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is not None:
            with contextlib.suppress(OSError, ValueError):
                signal.signal(sig, handler)


def _announce(port: int, lan: bool, lan_ips: Sequence[str]) -> None:
    local = f"http://127.0.0.1:{port}"
    if not lan:
        ui.step(f"Instagraph is running. Open {local}")
        return
    ui.warn("LAN mode: anyone on your network who can reach this computer can see your messages.")
    ui.step(f"Open on this computer:  {local}")
    if not lan_ips:
        ui.warn("Could not find a network address for this computer.")
    for ip in lan_ips:
        ui.step(f"Open on other devices:  http://{ip}:{port}")
    if wsl_lan_warning_needed(lan_ips, wsl=common.is_wsl()):
        ui.warn("WSL detected: other devices may not reach a 172.x address. Enable mirrored")
        ui.warn(
            "networking (networkingMode=mirrored in %UserProfile%\\.wslconfig, "
            "then 'wsl --shutdown')"
        )
        ui.warn(f"and allow port {port} in Windows Firewall.")


def _serve(python: Path, opts: Options, env: Mapping[str, str], lan_ips: Sequence[str]) -> int:
    host = env["INSTAGRAPH_HOST"]
    ui.step(f"Starting Instagraph on port {opts.port}…")
    try:
        proc = subprocess.Popen(
            _server_command(python, host, opts.port), cwd=common.BACKEND_DIR, env=dict(env)
        )
    except OSError as exc:
        raise common.InstallError(
            f"Could not start the server: {exc}",
            hint=f"Try running the installer again: {_install_cmd()}",
        ) from exc

    interrupted = False
    try:
        _install_stop_handlers(proc)
        try:
            ready = _wait_until_ready(proc, opts.port, STARTUP_TIMEOUT)
            code = proc.poll()
            if code is not None:
                ui.error(
                    f"The server stopped before it was ready (exit code {code}). "
                    "See the messages above."
                )
                ui.pause_before_exit()
                return code or 1
            if not ready:
                ui.warn(
                    "Instagraph is taking longer than usual to start. "
                    "If the page doesn't load, wait a moment and reload it."
                )
            try:
                common.write_run_file(proc.pid, opts.port, path=RUN_FILE)
            except OSError as exc:
                ui.warn(f"Could not write {RUN_FILE.name}: {exc}")
            _announce(opts.port, opts.lan, lan_ips)
            if opts.open_browser:
                _open_and_report(f"http://127.0.0.1:{opts.port}")
            ui.info("To stop Instagraph, close this window or press Ctrl+C.")
            code = _wait_for_exit(proc)
        except KeyboardInterrupt:
            interrupted = True
            code = _stop(proc)
    finally:
        if proc.poll() is None:  # an unexpected error: don't leave the server behind
            _terminate(proc)
        common.remove_run_file(RUN_FILE, pid=proc.pid)

    if interrupted or code in _STOPPED_EXIT_CODES:
        ui.step("Instagraph stopped.")
        return 0
    ui.error(f"The server stopped unexpectedly (exit code {code}). See the messages above.")
    ui.pause_before_exit()
    return code


def _launch(opts: Options) -> int:
    python = common.venv_python()
    if not python.exists():
        raise common.InstallError(
            "Instagraph isn't installed yet (backend/.venv was not found).",
            hint=f"Run the installer first: {_install_cmd()}",
        )
    env_file = read_env_file(common.BACKEND_DIR / ".env")
    if not _dist_index(os.environ, env_file).is_file():
        raise common.InstallError(
            "The Instagraph web page hasn't been built yet (frontend/dist/index.html is missing).",
            hint=f"Run the installer, which builds it: {_install_cmd()}",
        )

    state, port = find_running(opts.port)
    if state in ("ours", "instagraph"):
        url = f"http://127.0.0.1:{port}"
        if state == "ours":
            ui.step(f"Instagraph is already running on port {port}: {url}")
        else:
            ui.step(f"An Instagraph server is already running on port {port}: {url}")
            ui.info(
                "If that is another Instagraph copy, start this one on another port, "
                f"for example: {_run_cmd()} --port {port + 1}"
            )
        if opts.open_browser and not _open_and_report(url):
            ui.pause_before_exit()  # keep the URL readable
        return 0
    if state == "other":
        raise common.InstallError(
            f"Port {port} is already used by another program.",
            hint=f"Start Instagraph on another port, for example: {_run_cmd()} --port {port + 1}",
        )

    lan_ips: list[str] = []
    hostname = ""
    if opts.lan:
        lan_ips = lan_ipv4_addresses()
        with contextlib.suppress(OSError):
            hostname = socket.gethostname()
    env = build_server_env(
        os.environ, lan=opts.lan, lan_ips=lan_ips, hostname=hostname, env_file=env_file
    )
    return _serve(python, opts, env, lan_ips)


def main(argv: list[str] | None = None) -> int:
    """Entry point of run.py. Returns the process exit code."""
    ui.configure()
    try:
        opts = parse_args(argv)
    except SystemExit as exc:  # --help or a bad option; argparse already printed why
        return exc.code if isinstance(exc.code, int) else 0 if exc.code is None else 2
    try:
        return _launch(opts)
    except common.InstallError as exc:
        ui.error(exc.message, exc.hint)
        ui.pause_before_exit()
        return 1
    except KeyboardInterrupt:
        print()
        return 130
    except Exception:  # show what happened instead of a vanishing window
        traceback.print_exc()
        ui.error("Something went wrong while starting Instagraph. See the messages above.")
        ui.pause_before_exit()
        return 1
