"""Tests for installer/launcher.py (run.py)."""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import textwrap
import time
import webbrowser
from pathlib import Path
from typing import Any

import pytest

from installer import common, launcher, ui

_REAL_POPEN = subprocess.Popen

# --- Options -------------------------------------------------------------------------


@pytest.fixture
def no_port_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.delenv("INSTAGRAPH_PORT", raising=False)
    monkeypatch.setattr(common, "configured_port", lambda: 8123)


def test_defaults(no_port_env: None) -> None:
    opts = launcher.parse_args([], environ={})
    assert opts == launcher.Options(port=8123, lan=False, open_browser=True)


def test_flags(no_port_env: None) -> None:
    opts = launcher.parse_args(["--lan", "--no-browser", "--port", "9000"], environ={})
    assert opts == launcher.Options(port=9000, lan=True, open_browser=False)
    assert launcher.parse_args(["-l", "-p", "9001"], environ={}).port == 9001
    assert launcher.parse_args(["--port=9002"], environ={}).port == 9002


def test_port_precedence(no_port_env: None) -> None:
    assert launcher.parse_args([], environ={"PORT": "9100"}).port == 9100
    assert launcher.parse_args(["--port", "9200"], environ={"PORT": "9100"}).port == 9200
    assert launcher.parse_args([], environ={"PORT": ""}).port == 8123


def test_configured_port_reads_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("INSTAGRAPH_PORT", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("INSTAGRAPH_PORT=8555\n", encoding="utf-8")
    original = common.configured_port
    monkeypatch.setattr(common, "configured_port", lambda: original(env_file))
    assert launcher.parse_args([], environ={}).port == 8555


@pytest.mark.parametrize(
    ("argv", "environ"),
    [
        (["--port", "abc"], {}),
        (["--port", "0"], {}),
        (["--port", "70000"], {}),
        ([], {"PORT": "x"}),
    ],
)
def test_bad_port(no_port_env: None, argv: list[str], environ: dict[str, str]) -> None:
    with pytest.raises(SystemExit) as exc:
        launcher.parse_args(argv, environ=environ)
    assert exc.value.code == 2


def test_main_help_and_bad_option(capsys: pytest.CaptureFixture[str]) -> None:
    assert launcher.main(["--help"]) == 0
    assert "--no-browser" in capsys.readouterr().out
    assert launcher.main(["--bogus"]) == 2


# --- Environment ---------------------------------------------------------------------


def test_env_local_sets_paths() -> None:
    env = launcher.build_server_env({"PATH": "/bin"}, lan=False)
    assert env["PATH"] == "/bin"
    assert env["INSTAGRAPH_HOST"] == "127.0.0.1"
    assert env["INSTAGRAPH_DATA_DIR"] == str(common.DATA_DIR)
    assert env["INSTAGRAPH_FRONTEND_DIST"] == str(common.DIST_DIR)
    assert Path(env["INSTAGRAPH_DATA_DIR"]).is_absolute()
    assert "INSTAGRAPH_ALLOWED_HOSTS" not in env


def test_env_keeps_user_values() -> None:
    base = {"INSTAGRAPH_DATA_DIR": "/my/data", "INSTAGRAPH_HOST": "0.0.0.0"}
    env = launcher.build_server_env(
        base, lan=False, env_file={"INSTAGRAPH_FRONTEND_DIST": "/my/dist"}
    )
    assert env["INSTAGRAPH_DATA_DIR"] == "/my/data"
    # Set in backend/.env: the server reads it there, so we must not override it.
    assert "INSTAGRAPH_FRONTEND_DIST" not in env
    # Like ./launch, the host always follows --lan.
    assert env["INSTAGRAPH_HOST"] == "127.0.0.1"


def test_env_empty_user_value_is_replaced() -> None:
    env = launcher.build_server_env({"INSTAGRAPH_DATA_DIR": ""}, lan=False)
    assert env["INSTAGRAPH_DATA_DIR"] == str(common.DATA_DIR)


def test_env_lan() -> None:
    env = launcher.build_server_env(
        {"INSTAGRAPH_ALLOWED_HOSTS": "example.org"},
        lan=True,
        lan_ips=["192.168.1.5"],
        hostname="box",
    )
    assert env["INSTAGRAPH_HOST"] == "0.0.0.0"
    assert env["INSTAGRAPH_ALLOWED_HOSTS"] == "192.168.1.5,box,box.local,example.org"


def test_env_lan_allowed_hosts_from_env_file() -> None:
    env = launcher.build_server_env(
        {}, lan=True, lan_ips=[], hostname="", env_file={"INSTAGRAPH_ALLOWED_HOSTS": "a,b"}
    )
    assert env["INSTAGRAPH_ALLOWED_HOSTS"] == "a,b"


def test_lan_allowed_hosts() -> None:
    assert launcher.lan_allowed_hosts(["10.0.0.2", "192.168.0.3"], "pc", None) == (
        "10.0.0.2,192.168.0.3,pc,pc.local"
    )
    assert launcher.lan_allowed_hosts([], "mac.local", "mac.local, x ,") == "mac,mac.local,x"
    assert launcher.lan_allowed_hosts([], "", None) == ""


def test_env_file_values_respected(tmp_path: Path) -> None:
    """Values from backend/.env (read by common.read_env_file) count as user-set,
    whatever their case, like pydantic-settings reads them."""
    path = tmp_path / ".env"
    path.write_text(
        textwrap.dedent(
            """\
            # comment
            instagraph_data_dir = "/x y"
            export INSTAGRAPH_ALLOWED_HOSTS='a,b'
            garbage
            """
        ),
        encoding="utf-8",
    )
    env = launcher.build_server_env({}, lan=True, env_file=common.read_env_file(path))
    assert "INSTAGRAPH_DATA_DIR" not in env
    assert env["INSTAGRAPH_FRONTEND_DIST"] == str(common.DIST_DIR)
    assert env["INSTAGRAPH_ALLOWED_HOSTS"] == "a,b"


def test_dist_index_honours_custom_folder(tmp_path: Path) -> None:
    assert launcher._dist_index({}, {}) == common.DIST_INDEX
    assert launcher._dist_index({"INSTAGRAPH_FRONTEND_DIST": str(tmp_path)}, {}) == (
        tmp_path / "index.html"
    )


# --- LAN addresses -------------------------------------------------------------------


def test_lan_ipv4_addresses_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSocket:
        def __init__(self, *args: Any) -> None:
            pass

        def __enter__(self) -> FakeSocket:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def connect(self, addr: tuple[str, int]) -> None:
            pass

        def getsockname(self) -> tuple[str, int]:
            return ("192.168.1.20", 50000)

    infos = [
        (socket.AF_INET, 0, 0, "", (ip, 0))
        for ip in ("127.0.1.1", "192.168.1.20", "169.254.3.4", "10.1.2.3")
    ]
    monkeypatch.setattr(socket, "socket", FakeSocket)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: infos)
    assert launcher.lan_ipv4_addresses() == ["192.168.1.20", "10.1.2.3"]


def test_lan_ipv4_addresses_survives_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise OSError("no network")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    assert launcher.lan_ipv4_addresses() == []


def test_lan_ipv4_addresses_real() -> None:
    for ip in launcher.lan_ipv4_addresses():
        assert not ip.startswith("127.")


def test_wsl_warning() -> None:
    assert launcher.wsl_lan_warning_needed(["172.20.1.2"], wsl=True)
    assert launcher.wsl_lan_warning_needed([], wsl=True)
    assert not launcher.wsl_lan_warning_needed(["192.168.1.2"], wsl=True)
    assert not launcher.wsl_lan_warning_needed(["172.32.0.1"], wsl=True)
    assert not launcher.wsl_lan_warning_needed(["172.20.1.2"], wsl=False)


def test_announce_lan_wsl(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(common, "is_wsl", lambda: True)
    launcher._announce(9000, True, ["172.24.0.5"])
    out = capsys.readouterr()
    assert "http://127.0.0.1:9000" in out.out
    assert "http://172.24.0.5:9000" in out.out
    assert "LAN mode" in out.err
    assert "mirrored" in out.err and "port 9000" in out.err


def _recorder(calls: list[str]) -> Any:
    def record(url: str) -> bool:
        calls.append(url)
        return True

    return record


# --- Browser -------------------------------------------------------------------------

URL = "http://127.0.0.1:8000"


def test_browser_command_not_wsl() -> None:
    assert launcher.browser_command(URL, wsl=False, which=lambda name: "/x") is None


def test_browser_command_wslview() -> None:
    def which(name: str) -> str | None:
        return "/usr/bin/wslview" if name == "wslview" else "/c/cmd.exe"

    assert launcher.browser_command(URL, wsl=True, which=which) == ["/usr/bin/wslview", URL]


def test_browser_command_cmd_exe() -> None:
    def which(name: str) -> str | None:
        return "/c/cmd.exe" if name == "cmd.exe" else None

    assert launcher.browser_command(URL, wsl=True, which=which) == [
        "/c/cmd.exe",
        "/c",
        "start",
        "",
        URL,
    ]


def test_open_browser_wsl_runs_command(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append({"cmd": cmd, **kwargs})
        return subprocess.CompletedProcess(cmd, 0)

    def fail_webbrowser(url: str) -> bool:
        raise AssertionError("webbrowser must not be used under WSL")

    monkeypatch.setattr(common, "is_wsl", lambda: True)
    monkeypatch.setattr(shutil, "which", lambda n: "/c/cmd.exe" if n == "cmd.exe" else None)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(webbrowser, "open", fail_webbrowser)
    assert launcher.open_browser(URL) is True
    assert calls[0]["cmd"] == ["/c/cmd.exe", "/c", "start", "", URL]
    assert calls[0]["stdout"] is subprocess.DEVNULL
    assert calls[0]["stderr"] is subprocess.DEVNULL


def test_open_browser_other_os_uses_webbrowser(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(common, "is_wsl", lambda: False)
    monkeypatch.setattr(common, "IS_LINUX", False)
    monkeypatch.setattr(webbrowser, "open", _recorder(opened))
    assert launcher.open_browser(URL) is True
    assert opened == [URL]


def test_open_browser_headless_linux_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "is_wsl", lambda: False)
    monkeypatch.setattr(common, "IS_LINUX", True)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(webbrowser, "open", lambda url: pytest.fail("opened"))
    assert launcher.open_browser(URL) is False


def test_open_browser_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise OSError("nope")

    monkeypatch.setattr(common, "is_wsl", lambda: True)
    monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/wslview")
    monkeypatch.setattr(subprocess, "run", boom)
    assert launcher.open_browser(URL) is False


# --- Already running -----------------------------------------------------------------


def _write_run_file(path: Path, pid: int, port: int) -> None:
    path.write_text(json.dumps({"pid": pid, "port": port, "started": "x"}), encoding="utf-8")


def _fake_probe(answers: dict[int, common.ProbeResult]) -> Any:
    return lambda port, *a, **k: answers.get(port, "none")


def test_running_ours(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run_file = tmp_path / "run.json"
    _write_run_file(run_file, 4242, 9000)
    monkeypatch.setattr(common, "pid_alive", lambda pid: pid == 4242)
    monkeypatch.setattr(common, "probe_instagraph", _fake_probe({9000: "instagraph"}))
    assert launcher.find_running(8000, run_file) == ("ours", 9000)
    assert run_file.exists()


@pytest.mark.parametrize(
    ("alive", "answers"),
    [(False, {9000: "instagraph"}), (True, {9000: "none"}), (True, {9000: "other"})],
)
def test_running_stale_run_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    alive: bool,
    answers: dict[int, common.ProbeResult],
) -> None:
    run_file = tmp_path / "run.json"
    _write_run_file(run_file, 4242, 9000)
    monkeypatch.setattr(common, "pid_alive", lambda pid: alive)
    monkeypatch.setattr(common, "probe_instagraph", _fake_probe(answers))
    assert launcher.find_running(8000, run_file) == ("none", 8000)
    assert not run_file.exists()


def test_running_invalid_run_file_removed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run_file = tmp_path / "run.json"
    run_file.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(common, "probe_instagraph", _fake_probe({}))
    assert launcher.find_running(8000, run_file) == ("none", 8000)
    assert not run_file.exists()


@pytest.mark.parametrize("answer", ["instagraph", "other", "none"])
def test_running_port_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, answer: common.ProbeResult
) -> None:
    monkeypatch.setattr(common, "probe_instagraph", _fake_probe({8000: answer}))
    assert launcher.find_running(8000, tmp_path / "none.json") == (answer, 8000)


# --- main() before starting the server -----------------------------------------------


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A fake install: venv python and dist exist; the server must never be started."""
    python = tmp_path / "python"
    python.touch()
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").touch()
    monkeypatch.setattr(common, "venv_python", lambda: python)
    monkeypatch.setattr(common, "DIST_INDEX", dist / "index.html")
    monkeypatch.setattr(common, "BACKEND_DIR", tmp_path)
    monkeypatch.setattr(launcher, "RUN_FILE", tmp_path / "run.json")
    monkeypatch.delenv("INSTAGRAPH_FRONTEND_DIST", raising=False)
    monkeypatch.setattr(ui, "pause_before_exit", lambda: None)

    def no_popen(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the server must not be started")

    monkeypatch.setattr(subprocess, "Popen", no_popen)
    return tmp_path


def test_main_missing_venv(
    installed: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(common, "venv_python", lambda: installed / "nope")
    assert launcher.main(["--no-browser", "--port", "9000"]) == 1
    assert "install.py" in capsys.readouterr().err


def test_install_hint_windows(
    installed: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(common, "IS_WINDOWS", True)
    monkeypatch.setattr(common, "venv_python", lambda: installed / "nope")
    assert launcher.main(["--no-browser", "--port", "9000"]) == 1
    err = capsys.readouterr().err
    assert "Run the installer first, with Python 3.12 or newer (for example: py install.py)" in (
        err
    )
    assert "network folder" not in err


def test_install_hint_elsewhere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "IS_WINDOWS", False)
    assert launcher._install_hint("Run the installer first") == (
        "Run the installer first: python3 install.py"
    )


@pytest.mark.parametrize(
    ("windows", "root", "warned"),
    [
        (True, "//server/share/instagraph", True),
        (True, "C:/Users/me/instagraph", False),
        (False, "//server/share/instagraph", False),
    ],
)
def test_unc_repo_warns_once(
    installed: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    windows: bool,
    root: str,
    warned: bool,
) -> None:
    monkeypatch.setattr(common, "IS_WINDOWS", windows)
    monkeypatch.setattr(common, "REPO_ROOT", Path(root))
    monkeypatch.setattr(common, "venv_python", lambda: installed / "nope")
    assert launcher.main(["--no-browser", "--port", "9000"]) == 1  # not installed: stops
    err = capsys.readouterr().err
    assert err.count("network folder") == (1 if warned else 0)


def test_main_missing_dist(
    installed: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(common, "DIST_INDEX", installed / "nope" / "index.html")
    assert launcher.main(["--no-browser", "--port", "9000"]) == 1
    err = capsys.readouterr().err
    assert "index.html" in err and "install.py" in err


def test_main_already_running_opens_browser(
    installed: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(common, "probe_instagraph", _fake_probe({9000: "instagraph"}))
    monkeypatch.setattr(launcher, "open_browser", _recorder(opened))
    assert launcher.main(["--port", "9000"]) == 0
    assert opened == ["http://127.0.0.1:9000"]
    assert "already running on port 9000" in capsys.readouterr().out


def test_main_port_used_by_other(
    installed: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(common, "probe_instagraph", _fake_probe({9000: "other"}))
    assert launcher.main(["--no-browser", "--port", "9000"]) == 1
    assert "--port" in capsys.readouterr().err


def test_main_server_dies_during_startup(
    installed: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A real child that exits at once stands in for a broken server."""
    monkeypatch.setattr(common, "probe_instagraph", _fake_probe({}))
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda cmd, **kw: _REAL_POPEN([sys.executable, "-c", "raise SystemExit(3)"], **kw),
    )
    assert launcher.main(["--no-browser", "--port", "9000"]) == 3
    assert "stopped before it was ready (exit code 3)" in capsys.readouterr().err
    assert not (installed / "run.json").exists()


class FakeChild:
    """Stands in for the uvicorn process. It exits with ``code`` on its ``exit_on_wait``-th
    ``wait()`` (0: it has already exited when started)."""

    pid = 54321

    def __init__(self, exit_on_wait: int, code: int) -> None:
        self.exit_on_wait = exit_on_wait
        self.code = code
        self.waits = 0
        self.returncode: int | None = code if exit_on_wait == 0 else None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.waits += 1
            if self.waits < self.exit_on_wait:
                raise subprocess.TimeoutExpired("uvicorn", timeout or 0)
            self.returncode = self.code
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    kill = terminate


def _start_fake_child(monkeypatch: pytest.MonkeyPatch, child: FakeChild, port: int) -> None:
    """Popen returns ``child``; from then on Instagraph answers on ``port``."""
    started: list[bool] = []

    def fake_popen(cmd: list[str], **kwargs: Any) -> FakeChild:
        started.append(True)
        return child

    def probe(p: int, *args: Any, **kwargs: Any) -> common.ProbeResult:
        return "instagraph" if started and p == port else "none"

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(common, "probe_instagraph", probe)


@pytest.mark.parametrize(
    "exit_on_wait",
    [0, 1],  # 0: exits before the port answers; 1: exits right after (in the confirm wait)
)
def test_main_other_instagraph_took_the_port(
    installed: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    exit_on_wait: int,
) -> None:
    """Icon double-clicked twice: our server can't bind, the other copy answers. Treat it
    as "already running" and leave the other copy's run file alone."""
    run_file = installed / "run.json"
    child = FakeChild(exit_on_wait=exit_on_wait, code=1)
    _start_fake_child(monkeypatch, child, 9000)
    opened: list[str] = []
    monkeypatch.setattr(launcher, "open_browser", _recorder(opened))
    start_child = subprocess.Popen

    def popen_and_other_copy_writes(cmd: list[str], **kwargs: Any) -> Any:
        _write_run_file(run_file, 999, 9000)  # the first copy writes its run file meanwhile
        return start_child(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen_and_other_copy_writes)
    monkeypatch.setattr(
        common, "write_run_file", lambda *a, **k: pytest.fail("must not write the run file")
    )
    assert launcher.main(["--port", "9000"]) == 0
    out = capsys.readouterr()
    assert "already running on port 9000" in out.out
    assert "error" not in out.err
    assert opened == ["http://127.0.0.1:9000"]
    assert common.read_run_file(run_file) == {"pid": 999, "port": 9000, "started": "x"}


def test_main_own_server_writes_and_removes_run_file(
    installed: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_file = installed / "run.json"
    child = FakeChild(exit_on_wait=2, code=0)  # survives the confirm wait, then stops
    _start_fake_child(monkeypatch, child, 9000)
    seen: list[dict[str, Any] | None] = []

    def open_browser(url: str) -> bool:
        seen.append(common.read_run_file(run_file))
        return True

    monkeypatch.setattr(launcher, "open_browser", open_browser)
    before = signal.getsignal(signal.SIGTERM)
    assert launcher.main(["--port", "9000"]) == 0
    assert seen and seen[0] is not None and seen[0]["pid"] == FakeChild.pid
    assert not run_file.exists()
    assert "Instagraph stopped." in capsys.readouterr().out
    assert signal.getsignal(signal.SIGTERM) == before  # stop handlers are put back


# --- Windows console -----------------------------------------------------------------


class FakeKernel32:
    def __init__(self, mode: int | None) -> None:
        self.mode = mode  # None: no console
        self.set_calls: list[int] = []

    def GetStdHandle(self, which: int) -> int:  # Win32 name
        assert which == -10
        return 7

    def GetConsoleMode(self, handle: int, ref: Any) -> int:
        if self.mode is None:
            return 0
        ref._obj.value = self.mode
        return 1

    def SetConsoleMode(self, handle: int, mode: int) -> int:
        assert handle == 7
        self.set_calls.append(mode)
        return 1


def test_quick_edit_disabled_and_restored() -> None:
    kernel32 = FakeKernel32(0x01F7)  # Windows 10 default: QuickEdit on
    with launcher.quick_edit_disabled(kernel32):
        assert kernel32.set_calls == [0x01B7]  # QuickEdit off, extended flags kept
    assert kernel32.set_calls == [0x01B7, 0x01F7]


def test_quick_edit_already_off_or_no_console() -> None:
    off = FakeKernel32(0x00B7)
    with launcher.quick_edit_disabled(off):
        pass
    assert off.set_calls == []
    no_console = FakeKernel32(None)
    with launcher.quick_edit_disabled(no_console):
        pass
    assert no_console.set_calls == []


def test_quick_edit_never_raises() -> None:
    class Broken:
        def GetStdHandle(self, which: int) -> int:
            raise OSError("no console")

    with launcher.quick_edit_disabled(Broken()):
        pass


class FakeWindll:
    def __init__(self, kernel32: FakeKernel32) -> None:
        self.kernel32 = kernel32


def test_quick_edit_skipped_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel32 = FakeKernel32(0x01F7)
    monkeypatch.setattr(common, "IS_WINDOWS", False)
    monkeypatch.setattr(ctypes, "windll", FakeWindll(kernel32), raising=False)
    with launcher.quick_edit_disabled():
        pass
    assert kernel32.set_calls == []


def test_quick_edit_uses_windll_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel32 = FakeKernel32(0x01F7)
    monkeypatch.setattr(common, "IS_WINDOWS", True)
    monkeypatch.setattr(ctypes, "windll", FakeWindll(kernel32), raising=False)
    with launcher.quick_edit_disabled():
        assert kernel32.set_calls == [0x01B7]
    assert kernel32.set_calls == [0x01B7, 0x01F7]


# --- Integration: the real server ----------------------------------------------------

_VENV_PY = common.venv_python()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.skipif(
    not _VENV_PY.exists() or not common.DIST_INDEX.is_file(),
    reason="needs backend/.venv and frontend/dist/index.html",
)
@pytest.mark.skipif(os.name == "nt", reason="uses process groups and SIGINT (POSIX)")
def test_integration_real_server(tmp_path: Path) -> None:
    port = _free_port()
    run_file = tmp_path / "instagraph-run.json"
    # run.py itself, but with the run file in tmp so a real running copy isn't disturbed.
    code = (
        "import sys; from pathlib import Path; import installer.launcher as l; "
        f"l.RUN_FILE = Path({str(run_file)!r}); "
        f"sys.exit(l.main(['--no-browser', '--port', '{port}']))"
    )
    env = {**os.environ, "INSTAGRAPH_DATA_DIR": str(tmp_path / "data"), "PYTHONUNBUFFERED": "1"}
    env.pop("INSTAGRAPH_FRONTEND_DIST", None)
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=common.REPO_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,  # so Ctrl+C can be imitated for the whole group
    )
    try:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline and not run_file.exists():
            assert proc.poll() is None, proc.communicate()[0].decode(errors="replace")
            time.sleep(0.2)
        assert run_file.exists(), "run file was not written"
        data = common.read_run_file(run_file)
        assert data is not None and data["port"] == port
        assert common.pid_alive(data["pid"])
        assert common.probe_instagraph(port) == "instagraph"

        # A second launch sees this one and exits without starting another server.
        again = subprocess.run(
            [sys.executable, "-c", code],
            cwd=common.REPO_ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert again.returncode == 0, again.stdout + again.stderr
        assert "already running" in again.stdout

        os.killpg(proc.pid, signal.SIGINT)  # what Ctrl+C in the terminal does
        output = proc.communicate(timeout=30)[0].decode(errors="replace")
        assert proc.returncode == 0, output
        assert "Instagraph stopped." in output
        assert not run_file.exists()
        assert common.probe_instagraph(port) == "none"
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=15)
