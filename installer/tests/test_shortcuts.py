"""Tests for installer/shortcuts.py.

Linux shortcuts are written for real, but only into tmp folders. Windows, WSL and macOS
use a fake runner that records commands; nothing touches the real desktop or Start menu.
"""

from __future__ import annotations

import json
import re
import shlex
import stat
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path, PureWindowsPath
from typing import Any

import pytest

from installer import shortcuts, ui
from installer.shortcuts import (
    LINUX_FILE,
    _split_exec,
    create_shortcuts,
    decode_ps,
    ps_quote,
    remove_shortcuts,
)

Handler = Callable[[list[str]], tuple[int, str]]


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
    values: dict[str, str] = {}
    for line in path.read_text().splitlines()[1:]:
        if "=" in line:
            key, _, value = line.partition("=")
            values[key] = value
    return values


@pytest.fixture(autouse=True)
def _non_interactive() -> None:
    ui.configure(assume_yes=True)


@pytest.fixture
def linux(tmp_path: Path) -> dict[str, Any]:
    home = tmp_path / "home"
    desktop = home / "Desktop"
    desktop.mkdir(parents=True)
    apps = home / ".local/share/applications"
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


def write_desktop_entry(path: Path, exec_line: str, extra: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"[Desktop Entry]\nType=Application\nName=Instagraph\nExec={exec_line}\n{extra}"
    )


# --- Linux ---------------------------------------------------------------------------


def test_linux_creates_menu_and_desktop_entries(linux: dict[str, Any]) -> None:
    result = run_linux(linux)
    menu, desk = linux["apps"] / LINUX_FILE, linux["desktop"] / LINUX_FILE
    assert result.created == [menu, desk]
    repo = linux["repo"]
    values = parse_desktop(menu)
    assert values["Name"] == "Instagraph"
    assert values["Exec"] == f'"{repo}/backend/.venv/bin/python" "{repo}/run.py"'
    assert values["Path"] == str(repo)
    assert values["Icon"] == f"{repo}/assets/icon.png"
    assert values["Terminal"] == "true"
    assert values["Type"] == "Application"
    assert desk.read_text() == menu.read_text()
    for f in (menu, desk):
        assert stat.S_IMODE(f.stat().st_mode) == 0o755
    assert result.messages == [
        f"App menu entry: created ({menu})",
        f"Desktop icon: created ({desk})",
    ]


def test_linux_quotes_special_characters(linux: dict[str, Any], tmp_path: Path) -> None:
    repo = make_repo(tmp_path / 'my "100%" $dir `x` back\\slash')
    python = repo / "venv dir/bin/python"
    run_linux(linux, repo=repo, python=python)
    text = (linux["apps"] / LINUX_FILE).read_text()
    exec_line = next(ln for ln in text.splitlines() if ln.startswith("Exec="))[5:]
    # Spec: quoted arg escapes " ` $ \ with a backslash, % doubles; then the string-level
    # escaping doubles every backslash once more.
    inner = 'my \\"100%%\\" \\$dir \\`x\\` back\\\\slash'
    inner = inner.replace("\\", "\\\\")
    assert exec_line == f'"{tmp_path}/{inner}/venv dir/bin/python" "{tmp_path}/{inner}/run.py"'
    # And it parses back to the real paths.
    unescaped = exec_line.replace("\\\\", "\\")
    assert _split_exec(unescaped) == [str(python), str(repo / "run.py")]
    path_line = next(ln for ln in text.splitlines() if ln.startswith("Path="))
    assert path_line == f"Path={str(repo).replace(chr(92), chr(92) * 2)}"


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
    assert result.created[0] == tmp_path / "xdg/applications" / LINUX_FILE


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


# Case C on Linux ---------------------------------------------------------------------


def test_case_c_same_repo_updates(linux: dict[str, Any]) -> None:
    run_linux(linux)
    result = run_linux(linux)
    assert result.messages == ["App menu entry: updated", "Desktop icon: updated"]


def test_case_c_folder_gone_replaces(linux: dict[str, Any]) -> None:
    for d in (linux["apps"], linux["desktop"]):
        write_desktop_entry(d / LINUX_FILE, '"/gone/py" "/gone/repo/run.py"', "Path=/gone/repo\n")
    result = run_linux(linux)
    assert result.messages[0] == (
        "App menu entry: replaced (it pointed to /gone/repo, which no longer exists)"
    )
    assert parse_desktop(linux["apps"] / LINUX_FILE)["Path"] == str(linux["repo"])


def test_case_c_broken_file_replaces(linux: dict[str, Any]) -> None:
    (linux["apps"]).mkdir(parents=True)
    (linux["apps"] / LINUX_FILE).write_text("garbage")
    result = run_linux(linux)
    assert result.messages[0] == "App menu entry: replaced (it was broken)"


def test_case_c_folder_without_run_py_replaces(linux: dict[str, Any], tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    write_desktop_entry(linux["apps"] / LINUX_FILE, f'"/py" "{empty}/run.py"')
    result = run_linux(linux)
    assert "which no longer has Instagraph" in result.messages[0]


def _other_copy(linux: dict[str, Any], tmp_path: Path) -> Path:
    other = make_repo(tmp_path / "other copy")
    for d in (linux["apps"], linux["desktop"]):
        write_desktop_entry(d / LINUX_FILE, f'"{other}/py" "{other}/run.py"', f"Path={other}\n")
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
    h = shortcuts._Ctx(
        plat="linux",
        repo=linux["repo"],
        python=Path(),
        home=Path(),
        env={},
        runner=FakeRunner(),
        which=no_which,
    ).hash8()
    menu_alt = linux["apps"] / f"instagraph-{h}.desktop"
    assert result.created == [menu_alt, linux["desktop"] / f"instagraph-{h}.desktop"]
    assert parse_desktop(menu_alt)["Name"] == "Instagraph (repo)"
    assert parse_desktop(linux["apps"] / LINUX_FILE)["Path"] == str(other)  # untouched
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
    assert parse_desktop(linux["apps"] / LINUX_FILE)["Path"] == str(linux["repo"])


def test_case_c_foreign_keeps_both(linux: dict[str, Any]) -> None:
    write_desktop_entry(linux["apps"] / LINUX_FILE, "/usr/bin/instagraph-viewer %U")
    result = run_linux(linux, desktop_dir=None, home=linux["home"] / "nowhere")
    assert result.created[0].name.startswith("instagraph-")
    assert "/usr/bin/instagraph-viewer" in (linux["apps"] / LINUX_FILE).read_text()


def test_case_c_relative_run_py_uses_path_key(linux: dict[str, Any]) -> None:
    repo = linux["repo"]
    write_desktop_entry(linux["apps"] / LINUX_FILE, "python run.py", f"Path={repo}\n")
    result = run_linux(linux)
    assert result.messages[0] == "App menu entry: updated"


# --- remove_shortcuts ----------------------------------------------------------------


def test_remove_only_listed_files(tmp_path: Path) -> None:
    a, b, keep = tmp_path / "a.desktop", tmp_path / "b.lnk", tmp_path / "keep.desktop"
    for f in (a, b, keep):
        f.write_text("x")
    icon_dir = tmp_path / "Instagraph"
    icon_dir.mkdir()
    icon = icon_dir / "icon.ico"
    icon.write_text("x")
    lines = remove_shortcuts([a, str(b), tmp_path / "missing.lnk", icon])
    assert not a.exists() and not b.exists() and not icon_dir.exists()
    assert keep.exists()
    assert lines == [
        f"Removed {a}",
        f"Removed {b}",
        f"Shortcut already gone: {tmp_path / 'missing.lnk'}",
        f"Removed {icon}",
    ]


def test_remove_reports_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "dir.desktop"
    folder.mkdir()
    monkeypatch.setattr(ui, "warn", lambda m: None)
    lines = remove_shortcuts([folder])
    assert lines[0].startswith("Could not remove")
    assert folder.exists()


# --- macOS ---------------------------------------------------------------------------


def test_macos_command_file_and_icon(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "Desktop").mkdir(parents=True)
    repo = make_repo(tmp_path / "it's my $repo")
    runner = FakeRunner()
    result = create_shortcuts(
        repo=repo, platform="macos", home=home, runner=runner, which=no_which, env={}
    )
    cmd_file = home / "Desktop/Instagraph.command"
    assert result.created == [cmd_file]
    assert stat.S_IMODE(cmd_file.stat().st_mode) == 0o755
    text = cmd_file.read_text()
    assert text.startswith("#!/bin/bash\n")
    py = repo / "backend/.venv/bin/python"
    assert f"cd {shlex.quote(str(repo))} || exit 1\n" in text
    assert f"exec {shlex.quote(str(py))} {shlex.quote(str(repo / 'run.py'))}\n" in text
    # bash would see exactly these words:
    exec_line = text.splitlines()[-1]
    assert shlex.split(exec_line) == ["exec", str(py), str(repo / "run.py")]

    [osa] = runner.calls
    assert osa[:4] == ["osascript", "-l", "JavaScript", "-e"]
    script = osa[4]
    assert "ObjC.import('AppKit')" in script
    assert json.dumps(str(repo / "assets/icon.icns")) in script
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
    (desk / "Instagraph.command").write_text(
        f"#!/bin/bash\ncd {other}\nexec {other}/backend/.venv/bin/python {other}/run.py\n"
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


class FakeWindows:
    """Answers the PowerShell scripts: folder query, shortcut read, shortcut write."""

    def __init__(self, folders: dict[str, str], lnks: dict[str, dict[str, str]] | None = None):
        self.folders = folders
        self.lnks = lnks or {}
        self.written: list[str] = []

    def __call__(self, cmd: list[str]) -> tuple[int, str]:
        if not _is_ps(cmd):
            return 0, ""
        script = decode_ps(cmd[cmd.index("-EncodedCommand") + 1])
        if "GetFolderPath('Desktop')" in script:
            return 0, json.dumps(self.folders) + "\n"
        if "$s.Save()" in script:
            self.written.append(script)
            return 0, "OK\n"
        m = re.search(r"CreateShortcut\('((?:[^']|'')*)'\)", script)
        assert m, script
        return 0, json.dumps(self.lnks.get(m.group(1).replace("''", "'"), {})) + "\n"


def test_windows_creates_desktop_and_start_menu(tmp_path: Path) -> None:
    desk, progs = tmp_path / "OneDrive/Desktop", tmp_path / "Start Menu/Programs"
    repo = make_repo(tmp_path / "Dušan's repo")
    win = FakeWindows({"desktop": str(desk), "programs": str(progs), "localappdata": "x"})
    runner = FakeRunner(win)
    result = create_shortcuts(
        repo=repo, platform="windows", runner=runner, which=which_of("powershell.exe"), env={}
    )
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
    esc = str(repo).replace("'", "''")
    assert "CreateShortcut($p)" in script
    assert f"$p = '{str(desk).replace(chr(39), chr(39) * 2)}" in script
    assert f"$s.TargetPath = '{esc}/backend/.venv/Scripts/python.exe'" in script
    assert f"$s.Arguments = '\"{esc}/run.py\"'" in script
    assert f"$s.WorkingDirectory = '{esc}'" in script
    assert f"$s.IconLocation = '{esc}/assets/icon.ico,0'" in script
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
            "args": f'"{other}/run.py"',
            "workdir": str(other),
        },
        str(progs / "Instagraph.lnk"): {
            "target": "C:\\py.exe",
            "args": f'"{repo}/run.py"',
            "workdir": str(repo),
        },
    }
    win = FakeWindows({"desktop": str(desk), "programs": str(progs)}, lnks)
    result = create_shortcuts(
        repo=repo,
        platform="windows",
        runner=FakeRunner(win),
        which=which_of("powershell.exe"),
        env={},
    )
    assert result.created == [desk / "Instagraph (repo).lnk", progs / "Instagraph.lnk"]
    assert "kept another copy's icon" in result.messages[0]
    assert result.messages[1] == "Start menu entry: updated"


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

WIN_DESKTOP = "C:\\Users\\Dušan\\OneDrive\\Desktop"
WIN_PROGRAMS = "C:\\Users\\Dušan\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs"
WIN_LAD = "C:\\Users\\Dušan\\AppData\\Local"


def wsl_setup(tmp_path: Path, lnks: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    mnt = tmp_path / "mnt/c"

    def to_unix(win: str) -> str:
        rest = PureWindowsPath(win).parts[1:]
        return str(mnt.joinpath(*rest))

    win = FakeWindows(
        {
            "desktop": WIN_DESKTOP,
            "programs": WIN_PROGRAMS,
            "localappdata": WIN_LAD,
            "systemroot": "C:\\Windows",
        },
        lnks,
    )

    def handler(cmd: list[str]) -> tuple[int, str]:
        if cmd[0] == "wslpath":
            assert cmd[1] == "-u"
            return 0, to_unix(cmd[2]) + "\n"
        return win(cmd)

    repo = make_repo(tmp_path / "home/dano/instagraph")
    (repo / "assets").mkdir()
    (repo / "assets/icon.ico").write_bytes(b"ICO")
    for d in (WIN_DESKTOP, WIN_PROGRAMS):
        Path(to_unix(d)).mkdir(parents=True)
    return {"repo": repo, "win": win, "runner": FakeRunner(handler), "to_unix": to_unix}


def run_wsl(s: dict[str, Any], **kw: Any) -> shortcuts.ShortcutResult:
    args: dict[str, Any] = {
        "repo": s["repo"],
        "platform": "wsl",
        "runner": s["runner"],
        "which": which_of("powershell.exe", "wslpath"),
        "env": {"WSL_DISTRO_NAME": "Ubuntu-24.04"},
    }
    args.update(kw)
    return create_shortcuts(**args)


def test_wsl_windows_shortcut(tmp_path: Path) -> None:
    s = wsl_setup(tmp_path)
    result = run_wsl(s)
    to_unix = s["to_unix"]
    icon_local = Path(to_unix(WIN_LAD)) / "Instagraph/icon.ico"
    assert icon_local.read_bytes() == b"ICO"
    assert result.created == [
        icon_local,
        Path(to_unix(WIN_DESKTOP)) / "Instagraph.lnk",
        Path(to_unix(WIN_PROGRAMS)) / "Instagraph.lnk",
    ]
    repo = s["repo"]
    script = s["win"].written[0]
    assert f"$p = '{WIN_DESKTOP}\\Instagraph.lnk'" in script
    assert "$s.TargetPath = 'C:\\Windows\\System32\\wsl.exe'" in script
    args = f'-d Ubuntu-24.04 --cd "{repo}" -- "{repo}/backend/.venv/bin/python" "{repo}/run.py"'
    assert f"$s.Arguments = '{args}'" in script
    assert f"$s.IconLocation = '{WIN_LAD}\\Instagraph\\icon.ico,0'" in script
    assert ["wslpath", "-u", WIN_DESKTOP] in s["runner"].calls
    assert result.messages[0].startswith("Windows desktop icon: created")


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


def test_wsl_without_interop_falls_back_to_linux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = wsl_setup(tmp_path)
    monkeypatch.setattr(ui, "warn", lambda m: None)
    apps = tmp_path / "apps"
    result = run_wsl(s, which=no_which, apps_dir=apps, home=tmp_path / "nohome")
    assert result.created == [apps / LINUX_FILE]


def test_wsl_user_says_no_gets_linux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = wsl_setup(tmp_path)
    questions: list[str] = []

    def ask(q: str, **kw: Any) -> bool:
        questions.append(q)
        return False

    monkeypatch.setattr(ui, "ask_yes_no", ask)
    apps = tmp_path / "apps"
    result = run_wsl(s, apps_dir=apps, home=tmp_path / "nohome")
    assert questions == ["Create a Windows desktop icon (starts Instagraph in WSL)?"]
    assert result.created == [apps / LINUX_FILE]
    assert s["runner"].calls == []


def test_wsl_unsafe_path_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = wsl_setup(tmp_path)
    monkeypatch.setattr(ui, "warn", lambda m: None)
    repo = make_repo(tmp_path / "cost$")
    apps = tmp_path / "apps"
    result = run_wsl(s, repo=repo, apps_dir=apps, home=tmp_path / "nohome")
    assert result.created == [apps / LINUX_FILE]


def test_wsl_reuses_earlier_icon_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_questions(*args: object, **kwargs: object) -> bool:
        raise AssertionError("should not ask")

    monkeypatch.setattr(ui, "ask_yes_no", no_questions)
    assert shortcuts._wsl_wants_windows_icon(["/mnt/c/Users/x/Desktop/Instagraph.lnk"])
    assert not shortcuts._wsl_wants_windows_icon(["/home/x/Desktop/instagraph.desktop"])
    monkeypatch.setattr(ui, "ask_yes_no", lambda *a, **k: True)
    assert shortcuts._wsl_wants_windows_icon([])
