"""Tests for installer/shortcuts.py.

Linux and macOS shortcuts are written for real, but only into tmp folders. Windows and WSL
use a fake runner that answers the PowerShell scripts; nothing touches the real desktop,
Start menu or home folder. An autouse fixture makes sure of that: the real runner and
``Path.home()`` raise if a test reaches them.

The tests run on Linux, macOS and Windows. Tests that depend on POSIX paths or file modes
are skipped on Windows; the others build expected paths from ``Path`` objects.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path, PureWindowsPath
from typing import Any

import pytest

from installer import shortcuts, ui
from installer.shortcuts import (
    LINUX_FILE,
    Platform,
    _desktop_escape,
    _desktop_unescape,
    _exec_arg,
    _split_exec,
    create_shortcuts,
    decode_ps,
    ps_quote,
    remove_shortcuts,
    remove_shortcuts_detailed,
    wsl_icon_name,
)

Handler = Callable[[list[str]], tuple[int, str]]

POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="needs POSIX paths and file modes")
#: The platform whose paths are valid here (for tests that remove real tmp files).
HOST: Platform = "windows" if os.name == "nt" else "macos" if sys.platform == "darwin" else "linux"


class FakeRunner:
    """Records commands; ``handler`` returns (exit code, stdout)."""

    def __init__(self, handler: Handler | None = None) -> None:
        self.calls: list[list[str]] = []
        self.handler = handler

    def __call__(self, cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        cmd = list(cmd)
        self.calls.append(cmd)
        code, out = self.handler(cmd) if self.handler else (0, "")
        return subprocess.CompletedProcess(cmd, code, out, "")

    def ps_scripts(self) -> list[str]:
        return [decode_ps(c[c.index("-EncodedCommand") + 1]) for c in self.calls if _is_ps(c)]


def _is_ps(cmd: list[str]) -> bool:
    return "-EncodedCommand" in cmd


def no_which(_name: str) -> str | None:
    return None


def which_of(*names: str) -> Callable[[str], str | None]:
    return lambda name: name if name in names else None


def make_repo(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "run.py").write_text("# fake\n")
    return root


def parse_desktop(path: Path) -> dict[str, str]:
    """Raw (still escaped) values of a .desktop file."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        if "=" in line:
            key, _, value = line.partition("=")
            values[key] = value
    return values


def desktop_value(path: Path, key: str) -> str:
    return _desktop_unescape(parse_desktop(path)[key])


def exec_args(path: Path) -> list[str]:
    return _split_exec(desktop_value(path, "Exec"))


def exec_line(*args: Path | str) -> str:
    """An escaped ``Exec`` value, as a real .desktop file has it (works with any path)."""
    return _desktop_escape(" ".join(_exec_arg(str(a)) for a in args))


def path_line(folder: Path) -> str:
    return f"Path={_desktop_escape(str(folder))}\n"


def hash8(repo: Path) -> str:
    return shortcuts._hash8(Path(os.path.abspath(repo)))


@pytest.fixture(autouse=True)
def _non_interactive() -> None:
    ui.configure(assume_yes=True)


@pytest.fixture(autouse=True)
def _no_real_system(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Fail any test that would run a real program or use the real home folder.

    Temporary folders (such as the guard's temporary home) go under pytest's tmp folder.
    """

    def forbidden_runner(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        raise AssertionError(f"a test tried to run a real program: {cmd[:1]}")

    def forbidden_home() -> Path:
        raise AssertionError("a test reached the real home folder")

    monkeypatch.setattr(shortcuts, "_default_runner", forbidden_runner)
    monkeypatch.setattr(Path, "home", staticmethod(forbidden_home))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path_factory.mktemp("tempdir")))


@pytest.fixture
def linux(tmp_path: Path) -> dict[str, Any]:
    home = tmp_path / "home"
    desktop = home / "Desktop"
    desktop.mkdir(parents=True)
    apps = home / ".local" / "share" / "applications"
    repo = make_repo(tmp_path / "repo")
    return {"home": home, "desktop": desktop, "apps": apps, "repo": repo}


def run_linux(setup: dict[str, Any], **kw: Any) -> shortcuts.ShortcutResult:
    args: dict[str, Any] = {
        "repo": setup["repo"],
        "platform": "linux",
        "home": setup["home"],
        "desktop_dir": setup["desktop"],
        "apps_dir": setup["apps"],
        "env": {},
        "runner": FakeRunner(),
        "which": no_which,
    }
    args.update(kw)
    return create_shortcuts(**args)


def write_desktop_entry(path: Path, exec_value: str, extra: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"[Desktop Entry]\nType=Application\nName=Instagraph\nExec={exec_value}\n{extra}",
        encoding="utf-8",
    )


def write_entry_for(path: Path, repo: Path) -> None:
    """A .desktop file that starts the copy in ``repo``."""
    write_desktop_entry(path, exec_line(repo / "py", repo / "run.py"), path_line(repo))


# --- Linux ---------------------------------------------------------------------------


def test_linux_creates_menu_and_desktop_entries(linux: dict[str, Any]) -> None:
    result = run_linux(linux)
    menu, desk = linux["apps"] / LINUX_FILE, linux["desktop"] / LINUX_FILE
    assert result.created == [menu, desk]
    repo = linux["repo"]
    values = parse_desktop(menu)
    assert values["Name"] == "Instagraph"
    assert exec_args(menu) == [
        str(repo / "backend" / ".venv" / "bin" / "python"),
        str(repo / "run.py"),
    ]
    assert desktop_value(menu, "Path") == str(repo)
    assert desktop_value(menu, "Icon") == str(repo / "assets" / "icon.png")
    assert values["Terminal"] == "true"
    assert values["Type"] == "Application"
    assert desk.read_text() == menu.read_text()
    assert result.messages == [
        f"App menu entry: created ({menu})",
        f"Desktop icon: created ({desk})",
    ]


@POSIX_ONLY
def test_linux_entry_text_and_mode(linux: dict[str, Any]) -> None:
    run_linux(linux)
    repo = linux["repo"]
    for f in (linux["apps"] / LINUX_FILE, linux["desktop"] / LINUX_FILE):
        assert stat.S_IMODE(f.stat().st_mode) == 0o755
        values = parse_desktop(f)
        assert values["Exec"] == f'"{repo}/backend/.venv/bin/python" "{repo}/run.py"'
        assert values["Icon"] == f"{repo}/assets/icon.png"


@POSIX_ONLY  # these characters aren't allowed in Windows file names
def test_linux_quotes_special_characters(linux: dict[str, Any], tmp_path: Path) -> None:
    repo = make_repo(tmp_path / 'my "100%" $dir `x` back\\slash')
    python = repo / "venv dir/bin/python"
    run_linux(linux, repo=repo, python=python)
    text = (linux["apps"] / LINUX_FILE).read_text()
    exec_value = next(ln for ln in text.splitlines() if ln.startswith("Exec="))[5:]
    # Spec: quoted arg escapes " ` $ \ with a backslash, % doubles; then the string-level
    # escaping doubles every backslash once more.
    inner = 'my \\"100%%\\" \\$dir \\`x\\` back\\\\slash'
    inner = inner.replace("\\", "\\\\")
    assert exec_value == f'"{tmp_path}/{inner}/venv dir/bin/python" "{tmp_path}/{inner}/run.py"'
    # And it parses back to the real paths.
    unescaped = exec_value.replace("\\\\", "\\")
    assert _split_exec(unescaped) == [str(python), str(repo / "run.py")]
    path_value = next(ln for ln in text.splitlines() if ln.startswith("Path="))
    assert path_value == f"Path={str(repo).replace(chr(92), chr(92) * 2)}"


def test_linux_no_desktop_still_creates_menu_entry(linux: dict[str, Any]) -> None:
    linux["desktop"].rmdir()
    result = run_linux(linux, desktop_dir=None)
    assert result.created == [linux["apps"] / LINUX_FILE]
    assert any("Desktop icon: skipped" in m for m in result.messages)


def test_linux_desktop_from_xdg_user_dir(linux: dict[str, Any]) -> None:
    other = linux["home"] / "Schreibtisch"
    other.mkdir()

    runner = FakeRunner(lambda cmd: (0, f"{other}\n") if cmd[0] == "xdg-user-dir" else (0, ""))
    result = run_linux(
        linux,
        desktop_dir=None,
        runner=runner,
        which=which_of("xdg-user-dir", "gio", "update-desktop-database"),
    )
    assert other / LINUX_FILE in result.created
    assert ["gio", "set", str(other / LINUX_FILE), "metadata::trusted", "true"] in runner.calls
    assert ["update-desktop-database", str(linux["apps"])] in runner.calls


def test_linux_xdg_user_dir_returning_home_falls_back(linux: dict[str, Any]) -> None:
    home = linux["home"]
    runner = FakeRunner(lambda cmd: (0, f"{home}\n"))
    result = run_linux(linux, desktop_dir=None, runner=runner, which=which_of("xdg-user-dir"))
    assert linux["desktop"] / LINUX_FILE in result.created


def test_linux_apps_dir_from_xdg_data_home(linux: dict[str, Any], tmp_path: Path) -> None:
    result = run_linux(linux, apps_dir=None, env={"XDG_DATA_HOME": str(tmp_path / "xdg")})
    assert result.created[0] == tmp_path / "xdg" / "applications" / LINUX_FILE


def test_linux_gio_failure_is_not_fatal(linux: dict[str, Any]) -> None:
    def handler(cmd: list[str]) -> tuple[int, str]:
        if cmd[0] == "gio":
            raise OSError("boom")
        return 0, ""

    result = run_linux(linux, runner=FakeRunner(handler), which=which_of("gio"))
    assert len(result.created) == 2


def test_linux_write_error_is_reported(
    linux: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("")
    warnings: list[str] = []
    monkeypatch.setattr(ui, "warn", warnings.append)
    result = run_linux(linux, apps_dir=blocker / "apps")
    assert result.created == [linux["desktop"] / LINUX_FILE]
    assert result.messages[0].startswith("App menu entry: could not be created")
    assert warnings


def test_fake_runner_without_home_uses_a_tmp_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guard: a test that forgets ``home`` can't write into the real home folder."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    repo = make_repo(tmp_path / "repo")
    result = create_shortcuts(repo=repo, platform="linux", runner=FakeRunner(), which=no_which)
    [menu] = result.created
    assert menu.is_relative_to(tmp_path / "tmp")
    assert menu.name == LINUX_FILE


# Case C on Linux ---------------------------------------------------------------------


def test_case_c_same_repo_updates(linux: dict[str, Any]) -> None:
    run_linux(linux)
    result = run_linux(linux)
    assert result.messages == ["App menu entry: updated", "Desktop icon: updated"]


def test_case_c_folder_gone_replaces(linux: dict[str, Any], tmp_path: Path) -> None:
    gone = tmp_path / "gone" / "repo"
    for d in (linux["apps"], linux["desktop"]):
        write_entry_for(d / LINUX_FILE, gone)
    result = run_linux(linux)
    assert result.messages[0] == (
        f"App menu entry: replaced (it pointed to {gone}, which no longer exists)"
    )
    assert desktop_value(linux["apps"] / LINUX_FILE, "Path") == str(linux["repo"])


def test_case_c_unparsable_file_is_kept(linux: dict[str, Any]) -> None:
    linux["apps"].mkdir(parents=True)
    (linux["apps"] / LINUX_FILE).write_text("garbage")
    result = run_linux(linux)
    h = hash8(linux["repo"])
    assert result.created[0] == linux["apps"] / f"instagraph-{h}.desktop"
    assert result.messages[0].endswith(f"kept the existing {LINUX_FILE} (it could not be read)")
    assert (linux["apps"] / LINUX_FILE).read_text() == "garbage"


def test_case_c_unreadable_file_is_kept(linux: dict[str, Any]) -> None:
    (linux["apps"] / LINUX_FILE).mkdir(parents=True)  # reading it fails
    result = run_linux(linux)
    assert result.created[0].name == f"instagraph-{hash8(linux['repo'])}.desktop"
    assert (linux["apps"] / LINUX_FILE).is_dir()


def test_case_c_folder_without_run_py_replaces(linux: dict[str, Any], tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    write_desktop_entry(linux["apps"] / LINUX_FILE, exec_line("/py", empty / "run.py"))
    result = run_linux(linux)
    assert "which no longer has Instagraph" in result.messages[0]


def _other_copy(linux: dict[str, Any], tmp_path: Path) -> Path:
    other = make_repo(tmp_path / "other copy")
    for d in (linux["apps"], linux["desktop"]):
        write_entry_for(d / LINUX_FILE, other)
    return other


def test_case_c_other_copy_keep_both(
    linux: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = _other_copy(linux, tmp_path)
    asked: list[str] = []

    def ask(question: str, choices: dict[str, str], **kw: Any) -> str:
        asked.append(question)
        assert kw == {"default": "k", "yes_value": "k"}
        return "k"

    monkeypatch.setattr(ui, "ask_choice", ask)
    result = run_linux(linux)
    assert asked == [f"A desktop icon for another Instagraph copy exists ({other})."]
    h = hash8(linux["repo"])
    menu_alt = linux["apps"] / f"instagraph-{h}.desktop"
    assert result.created == [menu_alt, linux["desktop"] / f"instagraph-{h}.desktop"]
    assert parse_desktop(menu_alt)["Name"] == "Instagraph (repo)"
    assert desktop_value(linux["apps"] / LINUX_FILE, "Path") == str(other)  # untouched
    assert "kept another copy's icon" in result.messages[0]

    # A second run updates the "keep both" copy without asking again.
    asked.clear()
    result = run_linux(linux)
    assert asked == []
    assert result.messages == [
        f"App menu entry: updated ({menu_alt})",
        f"Desktop icon: updated ({linux['desktop'] / f'instagraph-{h}.desktop'})",
    ]


def test_case_c_other_copy_yes_keeps_both(linux: dict[str, Any], tmp_path: Path) -> None:
    _other_copy(linux, tmp_path)
    result = run_linux(linux)  # --yes: non-destructive choice
    assert all(p.name.startswith("instagraph-") for p in result.created)


def test_case_c_other_copy_replace(
    linux: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = _other_copy(linux, tmp_path)
    monkeypatch.setattr(ui, "ask_choice", lambda *a, **k: "r")
    result = run_linux(linux)
    assert result.created == [linux["apps"] / LINUX_FILE, linux["desktop"] / LINUX_FILE]
    assert result.messages[0] == f"App menu entry: replaced (it pointed to another copy at {other})"
    assert desktop_value(linux["apps"] / LINUX_FILE, "Path") == str(linux["repo"])


def test_case_c_foreign_keeps_both(linux: dict[str, Any]) -> None:
    write_desktop_entry(linux["apps"] / LINUX_FILE, "/usr/bin/instagraph-viewer %U")
    result = run_linux(linux, desktop_dir=None, home=linux["home"] / "nowhere")
    assert result.created[0].name.startswith("instagraph-")
    assert "/usr/bin/instagraph-viewer" in (linux["apps"] / LINUX_FILE).read_text()


def test_case_c_relative_run_py_uses_path_key(linux: dict[str, Any]) -> None:
    repo = linux["repo"]
    write_desktop_entry(linux["apps"] / LINUX_FILE, "python run.py", path_line(repo))
    result = run_linux(linux)
    assert result.messages[0] == "App menu entry: updated"


# --- remove_shortcuts ----------------------------------------------------------------


def remove(paths: Sequence[str | Path], **kw: Any) -> shortcuts.RemoveResult:
    args: dict[str, Any] = {
        "platform": HOST,
        "env": {},
        "runner": FakeRunner(),
        "which": no_which,
    }
    args.update(kw)
    return remove_shortcuts_detailed(paths, **args)


def test_remove_own_shortcuts(linux: dict[str, Any]) -> None:
    created = run_linux(linux).created
    result = remove(created, repo=linux["repo"])
    assert result.messages == [f"Removed {p}" for p in created]
    assert result.failed == []
    assert not any(p.exists() for p in created)
    # Running it again: nothing left, which is fine.
    result = remove(created, repo=linux["repo"])
    assert result.messages == [f"Shortcut already gone: {p}" for p in created]
    assert result.failed == []


def test_remove_keeps_shortcut_now_owned_by_another_copy(
    linux: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Copy B replaced the shared instagraph.desktop; uninstalling A must keep it."""
    record_a = run_linux(linux).created
    other = make_repo(tmp_path / "copy b")
    monkeypatch.setattr(ui, "ask_choice", lambda *a, **k: "r")
    run_linux(linux, repo=other)
    result = remove(record_a, repo=linux["repo"])
    assert result.messages == [
        f"Kept {p} (it now belongs to another Instagraph copy: {other})" for p in record_a
    ]
    assert result.failed == []
    assert all(desktop_value(p, "Path") == str(other) for p in record_a)


def test_remove_record_copied_from_another_folder(linux: dict[str, Any], tmp_path: Path) -> None:
    """A record copied along with the repo lists the original copy's icons: keep them."""
    record = run_linux(linux).created
    copy = make_repo(tmp_path / "copied repo")
    result = remove(record, repo=copy)
    assert all(m.startswith("Kept ") for m in result.messages)
    assert all(p.exists() for p in record)


def test_remove_skips_entries_that_are_not_shortcuts(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    notes = tmp_path / "notes.txt"
    shared_icon = tmp_path / "Instagraph" / "icon.ico"  # not this copy's icon name
    other_icon = tmp_path / "Instagraph" / "icon-00000000.ico"
    for f in (notes, shared_icon, other_icon):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    relative = str(Path("Desktop") / LINUX_FILE)
    entries: list[str | Path] = [notes, shared_icon, other_icon, relative, tmp_path]
    result = remove(entries, repo=repo)
    assert result.messages == [f"Skipped {p} (not an Instagraph shortcut)" for p in entries]
    assert result.failed == []
    assert notes.exists() and shared_icon.exists() and other_icon.exists()


def test_remove_paths_from_another_system_are_not_removed() -> None:
    win = r"C:\Users\x\Desktop\Instagraph.lnk"
    unc = r"\\server\share\Desktop\Instagraph.lnk"
    mnt = "/mnt/c/Users/x/Desktop/Instagraph.lnk"
    posix = "/home/x/Desktop/instagraph.desktop"
    cases: list[tuple[Platform, list[str]]] = [
        ("linux", [win, unc, mnt]),
        ("macos", [win, mnt]),
        ("wsl", [win, unc]),
        ("windows", [posix, mnt]),
    ]
    for plat, entries in cases:
        result = remove(entries, platform=plat)
        assert result.messages == [
            f"Not removed: {p} (made on another system; remove it there)" for p in entries
        ], plat
        assert result.failed == entries


def test_remove_wsl_accepts_mnt_paths() -> None:
    missing = "/mnt/c/instagraph-test-does-not-exist/Desktop/Instagraph.lnk"
    result = remove([missing], platform="wsl")
    assert result.messages == [f"Shortcut already gone: {Path(missing)}"]
    assert result.failed == []


def test_remove_broken_files_only_with_our_names(linux: dict[str, Any], tmp_path: Path) -> None:
    repo = linux["repo"]
    apps = linux["apps"]
    apps.mkdir(parents=True)
    garbage_ours = apps / LINUX_FILE
    garbage_ours.write_text("garbage")
    garbage_other = apps / "something.desktop"
    garbage_other.write_text("garbage")
    gone_ours = linux["desktop"] / f"instagraph-{hash8(repo)}.desktop"
    write_entry_for(gone_ours, tmp_path / "moved away")
    foreign_ours = linux["desktop"] / LINUX_FILE
    write_desktop_entry(foreign_ours, "/usr/bin/viewer %U")
    entries = [garbage_ours, garbage_other, gone_ours, foreign_ours]
    result = remove(entries, repo=repo)
    assert result.messages == [
        f"Removed {garbage_ours}",
        f"Kept {garbage_other} (it doesn't start this copy and isn't named like its icon)",
        f"Removed {gone_ours}",
        f"Skipped {foreign_ours} (not an Instagraph shortcut)",
    ]
    assert result.failed == []
    assert garbage_other.exists() and foreign_ours.exists()


def test_remove_command_file(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    desk = tmp_path / "Desktop"
    desk.mkdir()
    created = create_shortcuts(
        repo=repo, platform="macos", desktop_dir=desk, runner=FakeRunner(), which=no_which
    ).created
    assert remove(created, repo=repo).messages == [f"Removed {created[0]}"]


def test_remove_reports_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = make_repo(tmp_path / "repo")
    folder = tmp_path / LINUX_FILE  # our name, but a folder: unreadable, can't be unlinked
    folder.mkdir()
    monkeypatch.setattr(ui, "warn", lambda m: None)
    result = remove([folder], repo=repo)
    assert result.messages[0].startswith(f"Could not remove {folder}")
    assert result.failed == [str(folder)]
    assert folder.exists()


def test_remove_shortcuts_returns_lines(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    lines = remove_shortcuts(
        [tmp_path / "x.txt"], repo=repo, platform=HOST, runner=FakeRunner(), which=no_which
    )
    assert lines == [f"Skipped {tmp_path / 'x.txt'} (not an Instagraph shortcut)"]


# --- macOS ---------------------------------------------------------------------------


def test_macos_command_file_and_icon(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "Desktop").mkdir(parents=True)
    repo = make_repo(tmp_path / "it's my $repo")
    runner = FakeRunner()
    result = create_shortcuts(
        repo=repo, platform="macos", home=home, runner=runner, which=no_which, env={}
    )
    cmd_file = home / "Desktop" / "Instagraph.command"
    assert result.created == [cmd_file]
    if os.name != "nt":  # Windows has no executable bit
        assert stat.S_IMODE(cmd_file.stat().st_mode) == 0o755
    text = cmd_file.read_text()
    assert text.startswith("#!/bin/bash\n")
    py = repo / "backend" / ".venv" / "bin" / "python"
    assert f"cd {shlex.quote(str(repo))} || exit 1\n" in text
    assert f"exec {shlex.quote(str(py))} {shlex.quote(str(repo / 'run.py'))}\n" in text
    # bash would see exactly these words:
    exec_words = text.splitlines()[-1]
    assert shlex.split(exec_words) == ["exec", str(py), str(repo / "run.py")]

    [osa] = runner.calls
    assert osa[:4] == ["osascript", "-l", "JavaScript", "-e"]
    script = osa[4]
    assert "ObjC.import('AppKit')" in script
    assert json.dumps(str(repo / "assets" / "icon.icns")) in script
    assert f"setIconForFileOptions(img, {json.dumps(str(cmd_file))}, 0)" in script
    assert result.messages == [f"Desktop icon: created ({cmd_file})"]


def test_macos_icon_failure_is_not_fatal(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "Desktop").mkdir(parents=True)
    repo = make_repo(tmp_path / "repo")
    result = create_shortcuts(
        repo=repo,
        platform="macos",
        home=home,
        runner=FakeRunner(lambda c: (1, "")),
        which=no_which,
        env={},
    )
    assert len(result.created) == 1
    assert "custom icon could not be set" in result.messages[-1]


def test_macos_case_c_other_copy_and_same(tmp_path: Path) -> None:
    home = tmp_path / "home"
    desk = home / "Desktop"
    desk.mkdir(parents=True)
    other = make_repo(tmp_path / "other")
    repo = make_repo(tmp_path / "repo")
    q = shlex.quote
    py, run_py = other / "backend" / ".venv" / "bin" / "python", other / "run.py"
    (desk / "Instagraph.command").write_text(
        f"#!/bin/bash\ncd {q(str(other))}\nexec {q(str(py))} {q(str(run_py))}\n"
    )
    kw: dict[str, Any] = {"platform": "macos", "home": home, "which": no_which, "env": {}}
    result = create_shortcuts(repo=repo, runner=FakeRunner(), **kw)
    assert result.created == [desk / "Instagraph (repo).command"]
    # From the other copy's point of view, the original is its own: updated.
    result = create_shortcuts(repo=other, runner=FakeRunner(), **kw)
    assert result.messages[0] == "Desktop icon: updated"


# --- Windows -------------------------------------------------------------------------


def test_ps_quote_doubles_all_single_quote_kinds() -> None:
    assert ps_quote("C:\\Users\\O'Brien") == "'C:\\Users\\O''Brien'"
    assert ps_quote("a\u2019b") == "'a\u2019\u2019b'"


def _ps_value(script: str, name: str) -> str | None:
    m = re.search(rf"^{re.escape(name)} = '((?:[^']|'')*)'$", script, re.M)
    return m.group(1).replace("''", "'") if m else None


class FakeWindows:
    """Answers the PowerShell scripts: folder query, shortcut read, shortcut write.

    A written shortcut is remembered (so it can be read back) and, if ``to_local`` is
    given, also created as an empty file at that local path. Reading a shortcut listed in
    ``unreadable`` fails, like a PowerShell/COM error.
    """

    def __init__(
        self,
        folders: dict[str, str],
        lnks: dict[str, dict[str, str]] | None = None,
        to_local: Callable[[str], Path] | None = None,
    ) -> None:
        self.folders = folders
        self.lnks = lnks or {}
        self.to_local = to_local
        self.unreadable: set[str] = set()
        self.fail_writes = False
        self.written: list[str] = []

    def __call__(self, cmd: list[str]) -> tuple[int, str]:
        if not _is_ps(cmd):
            return 0, ""
        script = decode_ps(cmd[cmd.index("-EncodedCommand") + 1])
        if "GetFolderPath('Desktop')" in script:
            return 0, json.dumps(self.folders) + "\n"
        if "$s.Save()" in script:
            if self.fail_writes:
                return 1, "ERROR: Access denied\n"
            self.written.append(script)
            lnk = _ps_value(script, "$p")
            assert lnk
            self.lnks[lnk] = {
                "target": _ps_value(script, "$s.TargetPath") or "",
                "args": _ps_value(script, "$s.Arguments") or "",
                "workdir": _ps_value(script, "$s.WorkingDirectory") or "",
            }
            if self.to_local:
                local = self.to_local(lnk)
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(b"lnk")
            return 0, "OK\n"
        m = re.search(r"CreateShortcut\('((?:[^']|'')*)'\)", script)
        assert m, script
        lnk = m.group(1).replace("''", "'")
        if lnk in self.unreadable:
            return 1, "ERROR: COM object not available\n"
        return 0, json.dumps(self.lnks.get(lnk, {})) + "\n"


def run_windows(repo: Path, win: FakeWindows, **kw: Any) -> shortcuts.ShortcutResult:
    args: dict[str, Any] = {
        "repo": repo,
        "platform": "windows",
        "runner": FakeRunner(win),
        "which": which_of("powershell.exe"),
        "env": {},
    }
    args.update(kw)
    return create_shortcuts(**args)


def test_windows_creates_desktop_and_start_menu(tmp_path: Path) -> None:
    desk, progs = tmp_path / "OneDrive" / "Desktop", tmp_path / "Start Menu" / "Programs"
    repo = make_repo(tmp_path / "Dušan's repo")
    win = FakeWindows({"desktop": str(desk), "programs": str(progs), "localappdata": "x"})
    runner = FakeRunner(win)
    result = run_windows(repo, win, runner=runner)
    assert result.created == [desk / "Instagraph.lnk", progs / "Instagraph.lnk"]
    assert result.messages == [
        f"Desktop icon: created ({desk / 'Instagraph.lnk'})",
        f"Start menu entry: created ({progs / 'Instagraph.lnk'})",
    ]
    for call in runner.calls:
        assert call[:6] == [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
        ]
    script = win.written[0]
    assert "CreateShortcut($p)" in script
    assert _ps_value(script, "$p") == str(desk / "Instagraph.lnk")
    python = repo / "backend" / ".venv" / "Scripts" / "python.exe"
    assert _ps_value(script, "$s.TargetPath") == str(python)
    assert _ps_value(script, "$s.Arguments") == f'"{repo / "run.py"}"'
    assert _ps_value(script, "$s.WorkingDirectory") == str(repo)
    assert _ps_value(script, "$s.IconLocation") == f"{repo / 'assets' / 'icon.ico'},0"
    assert "Dušan''s repo" in script  # non-ASCII survives the UTF-16 encoding


def test_windows_case_c_reads_existing_lnk(tmp_path: Path) -> None:
    desk, progs = tmp_path / "Desktop", tmp_path / "Programs"
    desk.mkdir()
    progs.mkdir()
    repo = make_repo(tmp_path / "repo")
    other = make_repo(tmp_path / "other")
    (desk / "Instagraph.lnk").write_bytes(b"lnk")
    (progs / "Instagraph.lnk").write_bytes(b"lnk")
    lnks = {
        str(desk / "Instagraph.lnk"): {
            "target": "C:\\py.exe",
            "args": f'"{other / "run.py"}"',
            "workdir": str(other),
        },
        str(progs / "Instagraph.lnk"): {
            "target": "C:\\py.exe",
            "args": f'"{repo / "run.py"}"',
            "workdir": str(repo),
        },
    }
    win = FakeWindows({"desktop": str(desk), "programs": str(progs)}, lnks)
    result = run_windows(repo, win)
    assert result.created == [desk / "Instagraph (repo).lnk", progs / "Instagraph.lnk"]
    assert "kept another copy's icon" in result.messages[0]
    assert result.messages[1] == "Start menu entry: updated"


def test_windows_unreadable_lnk_is_kept(tmp_path: Path) -> None:
    """A read error, or a .lnk without a target, never counts as broken: keep both."""
    desk, progs = tmp_path / "Desktop", tmp_path / "Programs"
    for d in (desk, progs):
        d.mkdir()
        (d / "Instagraph.lnk").write_bytes(b"lnk")
    repo = make_repo(tmp_path / "repo")
    win = FakeWindows({"desktop": str(desk), "programs": str(progs)})
    win.unreadable.add(str(desk / "Instagraph.lnk"))  # COM error
    win.lnks[str(progs / "Instagraph.lnk")] = {"target": "", "args": "", "workdir": ""}
    result = run_windows(repo, win)
    assert result.created == [desk / "Instagraph (repo).lnk", progs / "Instagraph (repo).lnk"]
    assert result.messages[0].endswith("kept the existing Instagraph.lnk (it could not be read)")
    assert [_ps_value(w, "$p") for w in win.written] == [str(p) for p in result.created]


def test_windows_powershell_error_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ui, "warn", lambda m: None)
    runner = FakeRunner(lambda c: (1, "ERROR: Access denied \\u00e9\n"))
    result = create_shortcuts(
        repo=tmp_path, platform="windows", runner=runner, which=which_of("powershell.exe"), env={}
    )
    assert result.created == []
    assert result.messages == [
        "Desktop icon: could not be created (PowerShell failed: Access denied é)"
    ]


# --- WSL -----------------------------------------------------------------------------
# The repo has to be a POSIX path (it is passed to Linux through wsl.exe), so these tests
# don't run on Windows.

WIN_DESKTOP = "C:\\Users\\Dušan\\OneDrive\\Desktop"
WIN_PROGRAMS = "C:\\Users\\Dušan\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs"
WIN_LAD = "C:\\Users\\Dušan\\AppData\\Local"


def wsl_setup(
    tmp_path: Path, lnks: dict[str, dict[str, str]] | None = None, repo_name: str = "instagraph"
) -> dict[str, Any]:
    mnt = tmp_path / "mnt" / "c"

    def to_unix(win: str) -> str:
        rest = PureWindowsPath(win).parts[1:]
        return str(mnt.joinpath(*rest))

    def to_win(local: str) -> str:
        return str(PureWindowsPath("C:\\", *Path(local).relative_to(mnt).parts))

    win = FakeWindows(
        {
            "desktop": WIN_DESKTOP,
            "programs": WIN_PROGRAMS,
            "localappdata": WIN_LAD,
            "systemroot": "C:\\Windows",
        },
        lnks,
        to_local=lambda w: Path(to_unix(w)),
    )

    def handler(cmd: list[str]) -> tuple[int, str]:
        if cmd[0] == "wslpath":
            assert cmd[1] in ("-u", "-w")
            return 0, (to_unix(cmd[2]) if cmd[1] == "-u" else to_win(cmd[2])) + "\n"
        return win(cmd)

    repo = make_repo(tmp_path / "home" / "dano" / repo_name)
    (repo / "assets").mkdir()
    (repo / "assets" / "icon.ico").write_bytes(b"ICO")
    for d in (WIN_DESKTOP, WIN_PROGRAMS):
        Path(to_unix(d)).mkdir(parents=True, exist_ok=True)
    return {
        "repo": repo,
        "win": win,
        "runner": FakeRunner(handler),
        "to_unix": to_unix,
        "tmp": tmp_path,
    }


def run_wsl(s: dict[str, Any], **kw: Any) -> shortcuts.ShortcutResult:
    args: dict[str, Any] = {
        "repo": s["repo"],
        "platform": "wsl",
        "runner": s["runner"],
        "which": which_of("powershell.exe", "wslpath"),
        "env": {"WSL_DISTRO_NAME": "Ubuntu-24.04"},
        # A fallback to Linux shortcuts must land in tmp folders, never the real home.
        "home": s["tmp"] / "linux-home",
        "desktop_dir": None,
        "apps_dir": s["tmp"] / "apps",
    }
    args.update(kw)
    return create_shortcuts(**args)


def remove_wsl(s: dict[str, Any], paths: Sequence[str | Path], **kw: Any) -> shortcuts.RemoveResult:
    args: dict[str, Any] = {
        "repo": s["repo"],
        "platform": "wsl",
        "runner": s["runner"],
        "which": which_of("powershell.exe", "wslpath"),
        "env": {"WSL_DISTRO_NAME": "Ubuntu-24.04"},
    }
    args.update(kw)
    return remove_shortcuts_detailed(paths, **args)


@POSIX_ONLY
def test_wsl_windows_shortcut(tmp_path: Path) -> None:
    s = wsl_setup(tmp_path)
    result = run_wsl(s)
    to_unix = s["to_unix"]
    repo = s["repo"]
    icon_name = wsl_icon_name(repo)
    assert re.fullmatch(r"icon-[0-9a-f]{8}\.ico", icon_name)
    icon_local = Path(to_unix(WIN_LAD)) / "Instagraph" / icon_name
    assert icon_local.read_bytes() == b"ICO"
    assert result.created == [
        icon_local,
        Path(to_unix(WIN_DESKTOP)) / "Instagraph.lnk",
        Path(to_unix(WIN_PROGRAMS)) / "Instagraph.lnk",
    ]
    script = s["win"].written[0]
    assert f"$p = '{WIN_DESKTOP}\\Instagraph.lnk'" in script
    assert "$s.TargetPath = 'C:\\Windows\\System32\\wsl.exe'" in script
    args = f'-d Ubuntu-24.04 --cd "{repo}" -- "{repo}/backend/.venv/bin/python" "{repo}/run.py"'
    assert f"$s.Arguments = '{args}'" in script
    assert f"$s.IconLocation = '{WIN_LAD}\\Instagraph\\{icon_name},0'" in script
    assert ["wslpath", "-u", WIN_DESKTOP] in s["runner"].calls
    assert result.messages[0].startswith("Windows desktop icon: created")


@POSIX_ONLY
def test_wsl_case_c_same_distro_and_repo_updates(tmp_path: Path) -> None:
    repo_args = '-d Ubuntu-24.04 --cd "{repo}" -- "{repo}/py" "{repo}/run.py"'
    s = wsl_setup(tmp_path)
    repo = s["repo"]
    lnk_win = f"{WIN_DESKTOP}\\Instagraph.lnk"
    s["win"].lnks = {
        lnk_win: {"target": "C:\\Windows\\System32\\wsl.exe", "args": repo_args.format(repo=repo)}
    }
    Path(s["to_unix"](lnk_win)).write_bytes(b"lnk")
    result = run_wsl(s)
    assert result.messages[0] == "Windows desktop icon: updated"


@POSIX_ONLY
def test_wsl_case_c_other_distro_is_another_copy(tmp_path: Path) -> None:
    s = wsl_setup(tmp_path)
    lnk_win = f"{WIN_DESKTOP}\\Instagraph.lnk"
    s["win"].lnks = {
        lnk_win: {
            "target": "C:\\Windows\\System32\\wsl.exe",
            "args": '-d Debian --cd "/x" -- "/x/py" "/x/run.py"',
        }
    }
    Path(s["to_unix"](lnk_win)).write_bytes(b"lnk")
    result = run_wsl(s)
    assert "/x in WSL Debian" in result.messages[0]
    assert Path(s["to_unix"](WIN_DESKTOP)) / "Instagraph (instagraph).lnk" in result.created


@POSIX_ONLY
def test_wsl_case_c_windows_native_copy_gone(tmp_path: Path) -> None:
    s = wsl_setup(tmp_path)
    lnk_win = f"{WIN_DESKTOP}\\Instagraph.lnk"
    s["win"].lnks = {
        lnk_win: {"target": "C:\\old\\python.exe", "args": '"C:\\old\\repo\\run.py"', "workdir": ""}
    }
    Path(s["to_unix"](lnk_win)).write_bytes(b"lnk")
    result = run_wsl(s)
    assert result.messages[0] == (
        "Windows desktop icon: replaced (it pointed to C:\\old\\repo, which no longer exists)"
    )


@POSIX_ONLY
def test_wsl_without_interop_falls_back_to_linux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = wsl_setup(tmp_path)
    monkeypatch.setattr(ui, "warn", lambda m: None)
    result = run_wsl(s, which=no_which)
    assert result.created == [tmp_path / "apps" / LINUX_FILE]


@POSIX_ONLY
def test_wsl_user_says_no_gets_linux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = wsl_setup(tmp_path)
    questions: list[str] = []

    def ask(q: str, **kw: Any) -> bool:
        questions.append(q)
        return False

    monkeypatch.setattr(ui, "ask_yes_no", ask)
    result = run_wsl(s)
    assert questions == ["Create a Windows desktop icon (starts Instagraph in WSL)?"]
    assert result.created == [tmp_path / "apps" / LINUX_FILE]
    assert s["runner"].calls == []


@POSIX_ONLY
def test_wsl_unsafe_path_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = wsl_setup(tmp_path)
    monkeypatch.setattr(ui, "warn", lambda m: None)
    repo = make_repo(tmp_path / "cost$")
    result = run_wsl(s, repo=repo)
    assert result.created == [tmp_path / "apps" / LINUX_FILE]


@POSIX_ONLY
def test_wsl_no_lnk_written_removes_icon_and_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = wsl_setup(tmp_path)
    s["win"].fail_writes = True
    warnings: list[str] = []
    monkeypatch.setattr(ui, "warn", warnings.append)
    result = run_wsl(s)
    assert result.created == [tmp_path / "apps" / LINUX_FILE]  # never just the icon
    assert not (Path(s["to_unix"](WIN_LAD)) / "Instagraph").exists()
    assert "No Windows shortcut could be created; creating a Linux shortcut instead." in warnings


@POSIX_ONLY
def test_wsl_icon_per_copy_and_remove(tmp_path: Path) -> None:
    """Each copy has its own icon file; removing one copy leaves the other's working."""
    a = wsl_setup(tmp_path)
    b_repo = make_repo(tmp_path / "home" / "dano" / "second")
    (b_repo / "assets").mkdir()
    (b_repo / "assets" / "icon.ico").write_bytes(b"ICO2")
    record_a = run_wsl(a).created
    record_b = run_wsl(a, repo=b_repo).created  # keeps both ("Instagraph (second).lnk")
    icon_dir = Path(a["to_unix"](WIN_LAD)) / "Instagraph"
    assert record_a[0] == icon_dir / wsl_icon_name(a["repo"])
    assert record_b[0] == icon_dir / wsl_icon_name(b_repo)
    assert record_a[0] != record_b[0]

    result = remove_wsl(a, record_a)
    assert result.messages == [f"Removed {p}" for p in record_a]
    assert result.failed == []
    assert icon_dir.is_dir() and all(p.exists() for p in record_b)

    result = remove_wsl(a, record_b, repo=b_repo)
    assert result.messages == [f"Removed {p}" for p in record_b]
    assert not icon_dir.exists()  # empty now


@POSIX_ONLY
def test_wsl_remove_keeps_lnk_of_another_copy(tmp_path: Path) -> None:
    s = wsl_setup(tmp_path)
    record = run_wsl(s).created
    desk_lnk = f"{WIN_DESKTOP}\\Instagraph.lnk"
    # Another distro's copy replaced the desktop icon since.
    s["win"].lnks[desk_lnk]["args"] = '-d Debian --cd "/x" -- "/x/py" "/x/run.py"'
    result = remove_wsl(s, record)
    assert result.messages[1] == (
        f"Kept {record[1]} (it now belongs to another Instagraph copy: /x in WSL Debian)"
    )
    assert record[1].exists() and not record[2].exists()
    assert ["wslpath", "-w", str(record[1])] in s["runner"].calls


@POSIX_ONLY
def test_wsl_remove_unreadable_lnk_only_with_our_name(tmp_path: Path) -> None:
    s = wsl_setup(tmp_path)
    desk = Path(s["to_unix"](WIN_DESKTOP))
    ours, theirs = desk / "Instagraph.lnk", desk / "Shortcut.lnk"
    for f in (ours, theirs):
        f.write_bytes(b"lnk")
        s["win"].unreadable.add(f"{WIN_DESKTOP}\\{f.name}")
    result = remove_wsl(s, [ours, theirs])
    assert result.messages == [
        f"Removed {ours}",
        f"Kept {theirs} (it doesn't start this copy and isn't named like its icon)",
    ]
    assert theirs.exists()


def test_wsl_reuses_earlier_icon_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_questions(*args: object, **kwargs: object) -> bool:
        raise AssertionError("should not ask")

    monkeypatch.setattr(ui, "ask_yes_no", no_questions)
    assert shortcuts._wsl_wants_windows_icon(["/mnt/c/Users/x/Desktop/Instagraph.lnk"])
    assert not shortcuts._wsl_wants_windows_icon(["/home/x/Desktop/instagraph.desktop"])
    monkeypatch.setattr(ui, "ask_yes_no", lambda *a, **k: True)
    assert shortcuts._wsl_wants_windows_icon([])
