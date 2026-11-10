"""Tests for installer.venv_setup: venv state, (re)creation, pip, AI question."""

from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
import venv
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from installer import common, ui, venv_setup
from installer.common import InstallError

CURRENT = (sys.version_info.major, sys.version_info.minor)


@pytest.fixture(autouse=True)
def _reset_ui() -> Iterator[None]:
    ui.configure()
    yield
    ui.configure()


def interactive(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> list[str]:
    """Pretend to be a terminal; answer questions from ``answers``. Returns asked prompts."""
    asked: list[str] = []
    monkeypatch.setattr(ui, "interactive", lambda: True)

    def fake_input(prompt: str = "") -> str:
        asked.append(prompt)
        return answers.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    return asked


def fake_venv(
    root: Path,
    *,
    home: str | None = "/usr/bin",
    version: str | None = None,
    posix_python: bool = True,
    windows_python: bool = False,
) -> Path:
    path = root / ".venv"
    path.mkdir(parents=True)
    lines = []
    if home is not None:
        lines.append(f"home = {home}")
    lines.append("include-system-site-packages = false")
    if version is not None:
        lines.append(f"version_info = {version}")
    (path / "pyvenv.cfg").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if posix_python:
        (path / "bin").mkdir()
        (path / "bin" / "python").write_text("", encoding="utf-8")
    if windows_python:
        (path / "Scripts").mkdir()
        (path / "Scripts" / "python.exe").write_text("", encoding="utf-8")
    return path


def runs(_: Path) -> bool:
    return True


# --- pyvenv.cfg parsing ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("C:\\Users\\Dušan\\AppData\\Local\\Programs\\Python\\Python312", True),
        ("c:/Python312", True),
        ("\\\\server\\share\\python", True),
        ("/usr/bin", False),
        ("/mnt/c/Python312", False),
        ("", False),
    ],
)
def test_is_windows_path(value: str, expected: bool) -> None:
    assert venv_setup.is_windows_path(value) is expected


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        ({"version_info": "3.12.3.final.0"}, (3, 12)),
        ({"version": "3.13.1"}, (3, 13)),
        ({"version_info": "3.11.9.final.0", "version": "3.13.0"}, (3, 11)),
        ({}, None),
        ({"version": "garbage"}, None),
    ],
)
def test_cfg_version(cfg: dict[str, str], expected: tuple[int, int] | None) -> None:
    assert venv_setup.cfg_version(cfg) == expected


def test_read_pyvenv_cfg_keys_lowercased(tmp_path: Path) -> None:
    (tmp_path / "pyvenv.cfg").write_text("Home = /usr/bin\nVersion = 3.12.1\njunk\n")
    assert venv_setup.read_pyvenv_cfg(tmp_path) == {"home": "/usr/bin", "version": "3.12.1"}
    assert venv_setup.read_pyvenv_cfg(tmp_path / "missing") == {}


# --- State detection ------------------------------------------------------------------


def test_missing(tmp_path: Path) -> None:
    info = venv_setup.detect_venv_state(tmp_path / ".venv", windows=False)
    assert info.state == "missing"
    assert info.python == tmp_path / ".venv" / "bin" / "python"


def test_ok_posix(tmp_path: Path) -> None:
    v = fake_venv(tmp_path, version="3.12.3.final.0")
    info = venv_setup.detect_venv_state(v, windows=False, current=(3, 12), python_runs=runs)
    assert info.state == "ok"
    assert info.version == (3, 12)


def test_newer_python_venv_is_ok(tmp_path: Path) -> None:
    v = fake_venv(tmp_path, version="3.14.0.final.0")
    info = venv_setup.detect_venv_state(v, windows=False, current=(3, 12), python_runs=runs)
    assert info.state == "ok"


def test_windows_home_on_posix_is_other_os(tmp_path: Path) -> None:
    v = fake_venv(tmp_path, home="C:\\Python312", posix_python=False, windows_python=True)
    info = venv_setup.detect_venv_state(v, windows=False, python_runs=runs)
    assert info.state == "other-os"
    assert info.created_on == "Windows"


def test_unc_home_on_posix_is_other_os(tmp_path: Path) -> None:
    # Even if a bin/python happens to exist, a Windows home means another OS made it.
    v = fake_venv(tmp_path, home="\\\\server\\python")
    assert venv_setup.detect_venv_state(v, windows=False, python_runs=runs).state == "other-os"


def test_posix_home_on_windows_is_other_os(tmp_path: Path) -> None:
    v = fake_venv(tmp_path, home="/usr/bin")
    info = venv_setup.detect_venv_state(v, windows=True, python_runs=runs)
    assert info.state == "other-os"
    assert info.created_on == "Linux or macOS"
    assert info.python == v / "Scripts" / "python.exe"


def test_windows_venv_on_windows_ok(tmp_path: Path) -> None:
    v = fake_venv(
        tmp_path,
        home="C:\\Python312",
        version=f"{CURRENT[0]}.{CURRENT[1]}.0.final.0",
        posix_python=False,
        windows_python=True,
    )
    assert venv_setup.detect_venv_state(v, windows=True, python_runs=runs).state == "ok"


def test_only_other_os_interpreter_is_other_os(tmp_path: Path) -> None:
    v = fake_venv(tmp_path, home=None, posix_python=False, windows_python=True)
    assert venv_setup.detect_venv_state(v, windows=False, python_runs=runs).state == "other-os"


def test_no_interpreter_at_all_is_broken(tmp_path: Path) -> None:
    v = fake_venv(tmp_path, posix_python=False)
    assert venv_setup.detect_venv_state(v, windows=False, python_runs=runs).state == "broken"


def test_empty_folder_is_broken(tmp_path: Path) -> None:
    (tmp_path / ".venv").mkdir()
    info = venv_setup.detect_venv_state(tmp_path / ".venv", windows=False, python_runs=runs)
    assert info.state == "broken"


def test_interpreter_that_doesnt_run_is_broken(tmp_path: Path) -> None:
    v = fake_venv(tmp_path)
    info = venv_setup.detect_venv_state(v, windows=False, python_runs=lambda _: False)
    assert info.state == "broken"


def test_older_python(tmp_path: Path) -> None:
    v = fake_venv(tmp_path, version="3.12.3.final.0")
    info = venv_setup.detect_venv_state(v, windows=False, current=(3, 13), python_runs=runs)
    assert info.state == "older-python"
    assert info.version == (3, 12)


def test_other_os_wins_over_older_python(tmp_path: Path) -> None:
    v = fake_venv(tmp_path, home="C:\\Python311", version="3.11.0.final.0")
    info = venv_setup.detect_venv_state(v, windows=False, current=(3, 13), python_runs=runs)
    assert info.state == "other-os"


# --- A real venv ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_venv(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("realvenv") / ".venv"
    venv.EnvBuilder(with_pip=False).create(path)
    return path


def test_real_venv_is_ok(real_venv: Path) -> None:
    info = venv_setup.detect_venv_state(real_venv)
    assert info.state == "ok"
    assert info.version == CURRENT
    assert info.python == common.venv_python(real_venv)


def test_real_venv_has_no_anthropic(real_venv: Path) -> None:
    assert venv_setup.anthropic_installed(common.venv_python(real_venv)) is False


def test_real_venv_with_edited_cfg(real_venv: Path, tmp_path: Path) -> None:
    import shutil

    copy = tmp_path / ".venv"
    shutil.copytree(real_venv, copy, symlinks=True)
    cfg = copy / "pyvenv.cfg"
    text = cfg.read_text(encoding="utf-8")
    other_home = "/usr/bin" if common.IS_WINDOWS else "C:\\Python312"
    lines = [
        f"home = {other_home}" if line.startswith("home") else line for line in text.splitlines()
    ]
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert venv_setup.detect_venv_state(copy).state == "other-os"

    lines = [
        "version_info = 3.11.0.final.0" if line.startswith(("version", "home")) else line
        for line in text.splitlines()
    ]
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert venv_setup.detect_venv_state(copy, current=CURRENT).state == "older-python"


def test_running_inside(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    v = tmp_path / ".venv"
    v.mkdir()
    monkeypatch.setattr(sys, "prefix", str(v))
    assert venv_setup.running_inside(v)
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    assert not venv_setup.running_inside(v)


# --- ensure_venv ----------------------------------------------------------------------


class Calls:
    def __init__(self) -> None:
        self.created: list[Path] = []
        self.recreated: list[tuple[Path, bool]] = []

    def create(self, path: Path) -> None:
        self.created.append(path)

    def recreate(self, path: Path, *, keep_node: bool) -> None:
        self.recreated.append((path, keep_node))


def info(state: venv_setup.VenvState, tmp_path: Path, **kw: Any) -> venv_setup.VenvInfo:
    return venv_setup.VenvInfo(state, tmp_path / ".venv" / "bin" / "python", **kw)


def ensure(tmp_path: Path, vinfo: venv_setup.VenvInfo, calls: Calls) -> str:
    return venv_setup.ensure_venv(
        tmp_path / ".venv", info=vinfo, create=calls.create, recreate=calls.recreate
    )


def test_ensure_missing_creates(tmp_path: Path) -> None:
    calls = Calls()
    assert ensure(tmp_path, info("missing", tmp_path), calls) == "created"
    assert calls.created == [tmp_path / ".venv"]


def test_ensure_ok_reuses(tmp_path: Path) -> None:
    calls = Calls()
    assert ensure(tmp_path, info("ok", tmp_path), calls) == "reused"
    assert not calls.created and not calls.recreated


def test_other_os_with_yes_stops(tmp_path: Path) -> None:
    ui.configure(assume_yes=True)
    calls = Calls()
    with pytest.raises(InstallError) as exc:
        ensure(tmp_path, info("other-os", tmp_path, created_on="Windows"), calls)
    assert "created on Windows" in exc.value.message
    assert "left alone" in exc.value.message
    assert exc.value.hint and "~/" in exc.value.hint
    assert not calls.recreated


def test_other_os_interactive_default_no(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    asked = interactive(monkeypatch, [""])
    calls = Calls()
    with pytest.raises(InstallError, match="not changed"):
        ensure(tmp_path, info("other-os", tmp_path, created_on="Windows"), calls)
    assert "[y/N]" in asked[0]
    assert "breaks the Windows install" in asked[0]
    assert not calls.recreated


def test_other_os_interactive_yes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    interactive(monkeypatch, ["y"])
    calls = Calls()
    assert ensure(tmp_path, info("other-os", tmp_path, created_on="Windows"), calls) == "recreated"
    assert calls.recreated == [(tmp_path / ".venv", False)]


def test_older_python_with_yes_recreates(tmp_path: Path) -> None:
    ui.configure(assume_yes=True)
    calls = Calls()
    assert ensure(tmp_path, info("older-python", tmp_path, version=(3, 12)), calls) == "recreated"
    assert calls.recreated == [(tmp_path / ".venv", True)]


def test_older_python_declined_reuses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    asked = interactive(monkeypatch, ["n"])
    calls = Calls()
    assert ensure(tmp_path, info("older-python", tmp_path, version=(3, 12)), calls) == "reused"
    assert "[Y/n]" in asked[0] and "3.12" in asked[0]
    assert not calls.recreated


def test_broken_declined_stops(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    interactive(monkeypatch, ["n"])
    with pytest.raises(InstallError, match="can't be used"):
        ensure(tmp_path, info("broken", tmp_path), Calls())


@pytest.mark.parametrize("state", ["older-python", "broken", "other-os"])
def test_refuses_to_recreate_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, state: venv_setup.VenvState
) -> None:
    interactive(monkeypatch, ["y"])
    (tmp_path / ".venv").mkdir()
    monkeypatch.setattr(sys, "prefix", str(tmp_path / ".venv"))
    calls = Calls()
    with pytest.raises(InstallError) as exc:
        ensure(tmp_path, info(state, tmp_path, version=(3, 11), created_on="Windows"), calls)
    assert "normal Python" in (exc.value.hint or "")
    assert not calls.recreated


# --- create / recreate / remove ---------------------------------------------------------


def test_create_failure_removes_half_made_venv(tmp_path: Path) -> None:
    target = tmp_path / ".venv"

    def failing(path: Path) -> None:
        (path / "bin").mkdir(parents=True)
        raise subprocess.CalledProcessError(1, ["python", "-m", "ensurepip"])

    with pytest.raises(InstallError) as exc:
        venv_setup.create_venv(target, builder=failing)
    assert not target.exists()
    if common.IS_LINUX:
        major, minor = sys.version_info[:2]
        assert f"python{major}.{minor}-venv" in (exc.value.hint or "")
        assert "python3-venv" in (exc.value.hint or "")


def test_create_failure_by_sys_exit(tmp_path: Path) -> None:
    def exits(path: Path) -> None:
        path.mkdir()
        sys.exit(1)

    with pytest.raises(InstallError):
        venv_setup.create_venv(tmp_path / ".venv", builder=exits)
    assert not (tmp_path / ".venv").exists()


def test_create_without_ensurepip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    real = importlib.util.find_spec

    def find_spec(name: str, *a: Any, **kw: Any) -> Any:
        return None if name == "ensurepip" else real(name, *a, **kw)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    with pytest.raises(InstallError, match="can't create"):
        venv_setup.create_venv(tmp_path / ".venv")
    assert not (tmp_path / ".venv").exists()


def make_fake(path: Path) -> None:
    common.venv_python(path).parent.mkdir(parents=True)
    common.venv_python(path).write_text("", encoding="utf-8")


def test_recreate_keeps_private_node(tmp_path: Path) -> None:
    v = fake_venv(tmp_path)
    (v / "node").mkdir()
    (v / "node" / "VERSION").write_text("v24.1.0")
    (v / "junk").write_text("old")
    venv_setup.recreate_venv(v, keep_node=True, create=make_fake)
    assert (v / "node" / "VERSION").read_text() == "v24.1.0"
    assert not (v / "junk").exists()
    assert not (tmp_path / ".instagraph-node-keep").exists()


def test_recreate_for_other_os_drops_node(tmp_path: Path) -> None:
    v = fake_venv(tmp_path)
    (v / "node").mkdir()
    venv_setup.recreate_venv(v, keep_node=False, create=make_fake)
    assert not (v / "node").exists()
    assert common.venv_python(v).exists()


def test_recreate_failure_keeps_node_aside_cleanly(tmp_path: Path) -> None:
    v = fake_venv(tmp_path)
    (v / "node").mkdir()

    def fail(path: Path) -> None:
        raise InstallError("nope")

    with pytest.raises(InstallError):
        venv_setup.recreate_venv(v, keep_node=True, create=fail)
    assert not (tmp_path / ".instagraph-node-keep").exists()


@pytest.mark.skipif(common.IS_WINDOWS or os.geteuid() == 0, reason="POSIX permissions")
def test_remove_tree_read_only(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    sub = root / "sub"
    sub.mkdir(parents=True)
    f = sub / "file.txt"
    f.write_text("x")
    f.chmod(stat.S_IREAD)
    sub.chmod(stat.S_IREAD | stat.S_IEXEC)
    venv_setup.remove_tree(root)
    assert not root.exists()


def test_remove_tree_missing_is_fine(tmp_path: Path) -> None:
    venv_setup.remove_tree(tmp_path / "nothing")


# --- pip ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("OSError: [WinError 32] The process cannot access the file", True),
        ("PermissionError: [WinError 5] Access is denied: 'c:\\...\\_polars.pyd'", True),
        ("because it is being used by another process", True),
        ("[WinError 50] The request is not supported", False),
        ("ERROR: No matching distribution found for polars", False),
    ],
)
def test_file_in_use_detection(output: str, expected: bool) -> None:
    assert venv_setup.is_file_in_use_error(output) is expected


class FakePip:
    def __init__(self, results: list[tuple[int, str]]) -> None:
        self.results = results
        self.calls: list[tuple[list[str], Path]] = []

    def __call__(self, cmd: list[str], *, cwd: Path, show_all: bool) -> tuple[int, str]:
        self.calls.append(([str(c) for c in cmd], cwd))
        return self.results.pop(0)


def pip(tmp_path: Path, runner: FakePip, *, ai: bool = False, fresh: bool = False) -> str:
    return venv_setup.pip_install(
        tmp_path / "py",
        backend_dir=tmp_path / "my backend",
        repo_root=tmp_path,
        ai=ai,
        fresh=fresh,
        runner=runner,
    )


def test_pip_commands(tmp_path: Path) -> None:
    runner = FakePip([(0, ""), (0, "Successfully installed instagraph-0.1.0")])
    assert pip(tmp_path, runner, ai=True) == "up to date"
    (upgrade, cwd1), (install, cwd2) = runner.calls
    assert upgrade[:6] == [str(tmp_path / "py"), "-m", "pip", "install", "--upgrade", "pip"]
    assert "--quiet" in upgrade
    assert install[-2:] == ["-e", f"{tmp_path / 'my backend'}[ai]"]
    assert cwd1 == cwd2 == tmp_path


def test_pip_without_ai(tmp_path: Path) -> None:
    runner = FakePip([(0, ""), (0, "")])
    pip(tmp_path, runner)
    assert runner.calls[1][0][-1] == str(tmp_path / "my backend")


def test_pip_summary(tmp_path: Path) -> None:
    out = "Successfully installed instagraph-0.1.0 polars-1.20.0 orjson-3.11.0"
    assert pip(tmp_path, FakePip([(0, ""), (0, out)])) == "updated (2 packages)"
    assert pip(tmp_path, FakePip([(0, ""), (0, out)]), fresh=True) == "installed"


def test_pip_upgrade_failure_is_not_fatal(tmp_path: Path) -> None:
    assert pip(tmp_path, FakePip([(1, "network down"), (0, "")])) == "up to date"


def test_pip_file_in_use(tmp_path: Path) -> None:
    runner = FakePip([(0, ""), (1, "ERROR: [WinError 32] being used by another process")])
    with pytest.raises(InstallError) as exc:
        pip(tmp_path, runner)
    assert "in use" in exc.value.message
    assert "Close the Instagraph window" in (exc.value.hint or "")


def test_pip_failure_shows_tail(tmp_path: Path) -> None:
    runner = FakePip([(0, ""), (1, "ERROR: No matching distribution found for polars")])
    with pytest.raises(InstallError) as exc:
        pip(tmp_path, runner)
    assert "No matching distribution" in (exc.value.hint or "")


def test_run_streaming_shows_progress(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code = "print('noise'); print('Collecting polars'); raise SystemExit(3)"
    rc, out = venv_setup.run_streaming([sys.executable, "-c", code], cwd=tmp_path, show_all=False)
    assert rc == 3
    assert out.splitlines() == ["noise", "Collecting polars"]
    shown = capsys.readouterr().out
    assert "Collecting polars" in shown and "noise" not in shown


# --- AI question ------------------------------------------------------------------------


def never_ask(*a: Any, **kw: Any) -> bool:
    raise AssertionError("should not ask")


@pytest.mark.parametrize("already", [True, False])
def test_ai_flags_decide(already: bool) -> None:
    assert venv_setup.choose_ai(True, already, ask=never_ask) is True
    assert venv_setup.choose_ai(False, already, ask=never_ask) is False


def test_ai_already_installed_is_kept_without_asking() -> None:
    assert venv_setup.choose_ai(None, True, ask=never_ask) is True


def test_ai_asked_with_default_no(capsys: pytest.CaptureFixture[str]) -> None:
    seen: dict[str, Any] = {}

    def ask(question: str, *, default: bool) -> bool:
        seen.update(question=question, default=default)
        return True

    assert venv_setup.choose_ai(None, False, ask=ask) is True
    assert seen["default"] is False
    assert "online" in capsys.readouterr().out


def test_ai_with_yes_is_no() -> None:
    ui.configure(assume_yes=True)
    assert venv_setup.choose_ai(None, False) is False


def test_ai_earlier_no_is_not_asked_again(capsys: pytest.CaptureFixture[str]) -> None:
    assert venv_setup.choose_ai(None, False, answered_no=True, ask=never_ask) is False
    assert "--ai" in capsys.readouterr().out
    assert venv_setup.choose_ai(True, False, answered_no=True, ask=never_ask) is True
