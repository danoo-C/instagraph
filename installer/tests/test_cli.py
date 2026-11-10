"""Tests for installer.cli: arguments, the install flow (all external pieces mocked), uninstall."""

from __future__ import annotations

import json
import shutil
import signal
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from installer import cli, common, frontend, node, running, shortcuts, ui, venv_setup
from installer.common import InstallError
from installer.frontend import FrontendState
from installer.node import NodeTool
from installer.shortcuts import RemoveResult, ShortcutResult


@pytest.fixture(autouse=True)
def _reset_ui(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("INSTAGRAPH_PORT", raising=False)
    ui.configure()
    yield
    ui.configure()


# --- Arguments -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["--ai", "--no-ai"],
        ["--shortcut", "--no-shortcut"],
        ["--uninstall", "--run"],
        ["--uninstall", "--ai"],
        ["--uninstall", "--no-shortcut"],
        ["--uninstall", "--rebuild"],
        ["--bogus"],
    ],
)
def test_conflicting_flags(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(argv) == 2
    assert "error" in capsys.readouterr().err


def test_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "--private-node" in out and "never deleted" in out


def test_flag_values() -> None:
    args = cli.parse_args([])
    assert (args.ai, args.shortcut, args.yes, args.run) == (None, None, False, False)
    args = cli.parse_args(["-y", "--no-ai", "--shortcut", "-v", "--private-node"])
    assert (args.ai, args.shortcut, args.yes, args.verbose, args.private_node) == (
        False,
        True,
        True,
        True,
        True,
    )
    assert cli.parse_args(["--uninstall", "--yes", "--verbose"]).uninstall


# --- Shortcut logic ------------------------------------------------------------------


def test_want_shortcut(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui, "ask_yes_no", lambda *a, **k: pytest.fail("asked"))
    assert cli.want_shortcut(True, []) is True
    assert cli.want_shortcut(False, ["/x"]) is False
    assert cli.want_shortcut(None, ["/x"]) is True  # re-run: update without asking


def test_want_shortcut_asks_default_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def ask(question: str, *, default: bool) -> bool:
        seen.update(q=question, default=default)
        return default

    monkeypatch.setattr(ui, "ask_yes_no", ask)
    assert cli.want_shortcut(None, []) is True
    assert seen == {"q": "Create a desktop icon?", "default": True}


def test_merge_shortcuts(tmp_path: Path) -> None:
    kept = tmp_path / "kept.desktop"
    kept.write_text("")
    gone = tmp_path / "gone.desktop"
    new = tmp_path / "new.desktop"
    assert cli.merge_shortcuts([str(kept), str(gone)], [new, kept]) == [str(kept), str(new)]


# --- Full flow -----------------------------------------------------------------------


class Env:
    """A fake repo in tmp with every external piece mocked."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
        self.mp = monkeypatch
        self.paths = cli.Paths.under(root / "repo")
        self.paths.backend.mkdir(parents=True)
        self.paths.data.mkdir()
        (self.paths.data / "messages.parquet").write_text("precious")
        self.events: list[str] = []
        self.pip_calls: list[list[str]] = []
        self.pip_output = "Successfully installed instagraph-0.1.0"
        self.anthropic = False
        self.fe_state = FrontendState(needs_install=True, needs_build=True, reason="no build yet")
        self.private_node = False
        self.shortcut_files = [root / "desktop" / "Instagraph.desktop"]
        self.ports: dict[int, common.ProbeResult] = {}
        self.started = False
        self.inputs: list[str] | None = None
        self.previous: list[str] | None = None  # what create_shortcuts got as previous=
        self.cannot_remove: set[str] = set()  # shortcuts whose deletion "fails"
        self.remove_repo: Path | None = None

        monkeypatch.setattr(sys, "prefix", str(root / "system-python"))
        monkeypatch.setattr(
            common, "probe_instagraph", lambda p, *a, **k: self.ports.get(p, "none")
        )
        monkeypatch.setattr(common, "pid_alive", lambda pid: False)
        monkeypatch.setattr(common, "port_in_use", lambda p, *a, **k: False)
        monkeypatch.setattr(venv_setup, "check_can_create", lambda: None)
        monkeypatch.setattr(venv_setup, "create_venv", self.create_venv)
        monkeypatch.setattr(venv_setup, "_python_runs", lambda p: True)
        monkeypatch.setattr(venv_setup, "run_streaming", self.run_pip)
        monkeypatch.setattr(venv_setup, "anthropic_installed", lambda p: self.anthropic)
        monkeypatch.setattr(frontend, "frontend_state", self.frontend_state)
        monkeypatch.setattr(frontend, "build_frontend", self.build_frontend)
        monkeypatch.setattr(node, "ensure_node", self.ensure_node)
        monkeypatch.setattr(shortcuts, "create_shortcuts", self.create_shortcuts)
        monkeypatch.setattr(shortcuts, "remove_shortcuts_detailed", self.remove_shortcuts)
        monkeypatch.setattr(cli, "start_app", self.start_app)

    # fakes
    def create_venv(self, path: Path) -> None:
        self.events.append("create_venv")
        python = common.venv_python(path)
        python.parent.mkdir(parents=True)
        python.write_text("")
        v = sys.version_info
        (path / "pyvenv.cfg").write_text(
            f"home = {'C:\\Python' if common.IS_WINDOWS else '/usr/bin'}\n"
            f"version_info = {v.major}.{v.minor}.{v.micro}.final.0\n"
        )

    def run_pip(self, cmd: list[str], *, cwd: Path, show_all: bool) -> tuple[int, str]:
        assert cwd == self.paths.repo
        self.pip_calls.append([str(c) for c in cmd])
        is_upgrade = "--upgrade" in cmd
        return 0, "" if is_upgrade else self.pip_output

    def frontend_state(self, *, rebuild: bool = False) -> FrontendState:
        self.events.append(f"frontend_state(rebuild={rebuild})")
        return self.fe_state

    def build_frontend(self, tool: NodeTool, state: FrontendState) -> str:
        self.events.append("build_frontend")
        return "built (no build yet)"

    def ensure_node(self, *, force_private: bool = False) -> NodeTool:
        self.events.append(f"ensure_node(force_private={force_private})")
        private = self.private_node or force_private
        return NodeTool(Path("node"), Path("npm"), Path("."), (24, 1, 0), private)

    def create_shortcuts(self, *, previous: Any = ()) -> ShortcutResult:
        self.events.append("create_shortcuts")
        self.previous = [str(p) for p in previous]
        for f in self.shortcut_files:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("")
        return ShortcutResult(list(self.shortcut_files), ["Desktop icon: created"])

    def remove_shortcuts(self, paths: Any, *, repo: Path) -> RemoveResult:
        paths = [str(p) for p in paths]
        self.events.append(f"remove_shortcuts({paths})")
        self.remove_repo = repo
        result = RemoveResult()
        for p in paths:
            if not Path(p).is_absolute():  # a path of another OS
                result.messages.append(
                    f"Not removed: {p} (made on another system; remove it there)"
                )
                result.failed.append(p)
            elif p in self.cannot_remove:
                result.messages.append(f"Could not remove {p}: Access is denied")
                result.failed.append(p)
            else:
                result.messages.append(f"Removed {p}")
        return result

    def start_app(self, paths: cli.Paths) -> int:
        self.started = True
        return 7

    def interactive(self, answers: list[str]) -> list[str]:
        asked: list[str] = []
        self.inputs = answers
        self.mp.setattr(ui, "interactive", lambda: True)

        def fake_input(prompt: str = "") -> str:
            asked.append(prompt)
            assert self.inputs, f"unexpected question: {prompt}"
            return self.inputs.pop(0)

        self.mp.setattr("builtins.input", fake_input)
        return asked

    def main(self, *argv: str) -> int:
        return cli.main(list(argv), paths=self.paths)

    def record(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self.paths.install_record.read_text())
        return data

    def install_target(self) -> str:
        return self.pip_calls[-1][-1]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Env:
    return Env(monkeypatch, tmp_path)


def test_fresh_install_with_yes(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    assert env.main("--yes") == 0
    assert env.events == [
        "create_venv",
        "frontend_state(rebuild=False)",
        "ensure_node(force_private=False)",
        "build_frontend",
        "create_shortcuts",
    ]
    assert env.install_target() == str(env.paths.backend)  # --yes: no AI
    record = env.record()
    assert record["ai"] is False
    assert record["node"] == "system"
    assert record["shortcuts"] == [str(env.shortcut_files[0])]
    assert not env.started  # --yes without --run doesn't start it
    out = capsys.readouterr().out
    for line in (
        "Python environment: created",
        "Python packages: installed",
        "AI assistant: not installed",
        "Frontend: built (no build yet)",
        "Desktop icon: created",
        "double-click the Instagraph icon",
    ):
        assert line in out
    assert (env.paths.data / "messages.parquet").read_text() == "precious"


def test_second_run_changes_nothing(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    assert env.main("--yes") == 0
    env.events.clear()
    env.fe_state = FrontendState(False, False, "")
    capsys.readouterr()
    assert env.main("--yes", "--no-shortcut") == 0
    assert env.events == ["frontend_state(rebuild=False)"]
    out = capsys.readouterr().out
    assert "Python environment: reused" in out
    assert "Python packages: up to date" in out
    assert "Frontend: up to date" in out
    assert "Desktop icon: left as it is" in out
    assert env.record()["node"] == "system"  # unchanged when no build was needed


def test_rerun_updates_shortcut_without_asking(env: Env) -> None:
    assert env.main("--yes") == 0
    env.events.clear()
    env.fe_state = FrontendState(False, False, "")
    asked = env.interactive(["n"])  # only "Start Instagraph now?" may be asked
    assert env.main() == 0
    assert "create_shortcuts" in env.events
    assert len(asked) == 1 and "Start Instagraph now?" in asked[0]
    assert not env.started


def test_interactive_fresh_install(env: Env) -> None:
    # AI? yes. Desktop icon? (default yes). Start now? (default yes)
    asked = env.interactive(["y", "", ""])
    assert env.main() == 0 or env.started
    assert env.started
    assert env.install_target() == f"{env.paths.backend}[ai]"
    assert "AI assistant" in asked[0] and "[y/N]" in asked[0]
    assert "Create a desktop icon?" in asked[1] and "[Y/n]" in asked[1]
    assert env.record()["ai"] is True


def test_run_flag_returns_app_exit_code(env: Env) -> None:
    assert env.main("--yes", "--run", "--no-shortcut") == 7
    assert env.started
    assert "create_shortcuts" not in env.events


def test_ai_already_installed_kept(env: Env) -> None:
    env.main("--yes", "--no-shortcut")
    env.anthropic = True
    env.interactive(["n"])  # only "Start now?"
    assert env.main("--no-shortcut") == 0
    assert env.install_target() == f"{env.paths.backend}[ai]"
    assert env.record()["ai"] is True


def test_no_ai_keeps_installed_ai(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    env.main("--yes", "--no-shortcut")
    env.anthropic = True
    assert env.main("--yes", "--no-ai", "--no-shortcut") == 0
    assert env.install_target() == str(env.paths.backend)
    assert env.record()["ai"] is True
    assert "stays installed" in capsys.readouterr().out


def test_rebuild_and_private_node_passed_on(env: Env) -> None:
    assert env.main("--yes", "--rebuild", "--private-node", "--no-shortcut") == 0
    assert "frontend_state(rebuild=True)" in env.events
    assert "ensure_node(force_private=True)" in env.events
    assert env.record()["node"] == "private"


def test_running_server_stops_install(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    env.ports[8000] = "instagraph"
    assert env.main("--yes") == 1
    assert env.events == []
    assert not env.paths.venv.exists()
    assert "An Instagraph server is running on port 8000" in capsys.readouterr().err


def test_configured_port_from_env_file(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    env.paths.env_file.write_text("INSTAGRAPH_PORT=8765\n")
    env.ports[8765] = "instagraph"
    assert env.main("--yes") == 1
    assert "port 8765" in capsys.readouterr().err


def test_cancel_while_waiting(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    env.ports[8000] = "instagraph"
    monkeypatch.setattr(ui, "interactive", lambda: True)
    monkeypatch.setattr(ui, "wait_for_enter", lambda prompt: False)
    assert env.main() == 1
    assert env.events == []


def test_other_os_venv_with_yes_stops(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    env.paths.venv.mkdir()
    home = "/usr/bin" if common.IS_WINDOWS else "C:\\Users\\me\\Python312"
    (env.paths.venv / "pyvenv.cfg").write_text(f"home = {home}\n")
    assert env.main("--yes") == 1
    assert "create_venv" not in env.events
    assert "doesn't work in" in capsys.readouterr().err
    assert (env.paths.venv / "pyvenv.cfg").exists()


def test_recreated_venv_keeps_record(env: Env) -> None:
    assert env.main("--yes") == 0
    cfg = env.paths.venv / "pyvenv.cfg"
    cfg.write_text(cfg.read_text().replace("version_info = 3.", "version_info = 2."))
    env.events.clear()
    assert env.main("--yes") == 0  # older Python: recreated by default
    assert env.events[0] == "create_venv"
    assert env.record()["shortcuts"] == [str(env.shortcut_files[0])]


def test_install_error_is_reported(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    def fail(**kw: Any) -> NodeTool:
        raise InstallError("Node.js is needed.", "Get it from nodejs.org.")

    env.mp.setattr(node, "ensure_node", fail)
    assert env.main("--yes") == 1
    err = capsys.readouterr().err
    assert "Node.js is needed." in err and "nodejs.org" in err
    assert env.record()["ai"] is False  # earlier steps were recorded


def test_shortcut_error_is_only_a_warning(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    def fail(*, previous: object = ()) -> ShortcutResult:
        raise InstallError("No desktop folder.")

    env.mp.setattr(shortcuts, "create_shortcuts", fail)
    assert env.main("--yes") == 0
    assert "Desktop icon: not created" in capsys.readouterr().out


@pytest.mark.parametrize("verbose", [False, True])
def test_unexpected_error(env: Env, capsys: pytest.CaptureFixture[str], verbose: bool) -> None:
    def boom(**kw: Any) -> FrontendState:
        raise RuntimeError("kaboom")

    env.mp.setattr(frontend, "frontend_state", boom)
    assert env.main("--yes", *(["-v"] if verbose else [])) == 1
    err = capsys.readouterr().err
    assert "kaboom" in err
    assert ("Traceback" in err) is verbose
    assert ("--verbose" in err) is not verbose


def test_ctrl_c(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    def interrupt(**kw: Any) -> FrontendState:
        raise KeyboardInterrupt

    env.mp.setattr(frontend, "frontend_state", interrupt)
    assert env.main("--yes") == 130
    assert "Cancelled." in capsys.readouterr().out


def test_file_in_use_advice(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    env.mp.setattr(
        venv_setup,
        "run_streaming",
        lambda cmd, **kw: (0, "") if "--upgrade" in cmd else (1, "[WinError 32] in use"),
    )
    assert env.main("--yes") == 1
    assert "Close the Instagraph window" in capsys.readouterr().err


@pytest.mark.parametrize("windows", [False, True])
def test_start_command(monkeypatch: pytest.MonkeyPatch, windows: bool) -> None:
    monkeypatch.setattr(common, "IS_WINDOWS", windows)
    paths = cli.Paths.under(Path("/r"))
    expected = (
        "backend\\.venv\\Scripts\\python.exe run.py"
        if windows
        else "backend/.venv/bin/python run.py"
    )
    assert cli.start_command(paths) == expected


# --- Uninstall -----------------------------------------------------------------------


def installed(env: Env) -> None:
    assert env.main("--yes") == 0
    env.paths.dist.mkdir(parents=True)
    (env.paths.dist / "index.html").write_text("")
    env.paths.node_modules.mkdir()
    (env.paths.venv / "node").mkdir()
    env.events.clear()


def test_uninstall_plan(env: Env) -> None:
    installed(env)
    plan = cli.uninstall_plan(env.paths, cli.Record.load(env.paths.install_record, env.paths.repo))
    assert plan.shortcuts == [str(env.shortcut_files[0])]
    assert plan.remove == [env.paths.venv]
    assert plan.ask == [env.paths.dist, env.paths.node_modules]
    assert plan.keep == [env.paths.data]
    assert env.paths.data not in plan.remove + plan.ask


def test_uninstall_with_yes(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    installed(env)
    assert env.main("--uninstall", "--yes") == 0
    assert env.events == [f"remove_shortcuts({[str(env.shortcut_files[0])]})"]
    assert env.remove_repo == env.paths.repo
    assert not env.paths.venv.exists()  # including the private Node
    assert env.paths.dist.exists() and env.paths.node_modules.exists()  # default: keep
    assert (env.paths.data / "messages.parquet").read_text() == "precious"
    out = capsys.readouterr().out
    assert str(env.paths.data) in out and "Clear all data" in out
    assert not env.paths.install_record.exists()  # the record goes too


def test_uninstall_interactive_deletes_frontend(env: Env) -> None:
    installed(env)
    asked = env.interactive(["y", "y"])
    assert env.main("--uninstall") == 0
    assert all("[y/N]" in q for q in asked)
    assert not env.paths.dist.exists() and not env.paths.node_modules.exists()
    assert (env.paths.data / "messages.parquet").read_text() == "precious"


def test_uninstall_nothing_installed(env: Env) -> None:
    assert env.main("--uninstall", "--yes") == 0
    assert env.events == []
    assert env.paths.data.exists()


def test_uninstall_refuses_inside_venv(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    installed(env)
    env.mp.setattr(sys, "prefix", str(env.paths.venv))
    assert env.main("--uninstall", "--yes") == 1
    assert env.paths.venv.exists()
    assert "normal Python" in capsys.readouterr().err


def test_uninstall_stops_while_running(env: Env) -> None:
    installed(env)
    env.ports[8000] = "instagraph"
    assert env.main("--uninstall", "--yes") == 1
    assert env.paths.venv.exists()
    assert env.events == []


def test_guard_never_deletes_data(env: Env) -> None:
    with pytest.raises(InstallError):
        cli._guard_not_data(env.paths.data, env.paths)
    with pytest.raises(InstallError):
        cli._guard_not_data(env.paths.repo, env.paths)
    cli._guard_not_data(env.paths.venv, env.paths)


def test_running_module_is_used(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[int]] = []
    monkeypatch.setattr(running, "ensure_not_running", lambda rf, ports: seen.append(ports))
    env.main("--yes", "--no-shortcut")
    assert seen == [[8000, 8000]]
    assert seen and env.paths.run_file.parent == env.paths.venv


# --- Install record: only this folder on this OS counts (finding: copied folders) ------


def other_os() -> str:
    return "Linux" if common.IS_WINDOWS else "Windows"


def foreign_shortcut() -> str:
    """A shortcut path that means nothing on this OS."""
    if common.IS_WINDOWS:
        return "/home/me/Desktop/instagraph.desktop"
    return "C:\\Users\\me\\Desktop\\Instagraph.lnk"


def write_record(env: Env, **values: Any) -> None:
    data = {"version": 1, "repo": str(env.paths.repo), "os": common.os_label(), **values}
    env.paths.install_record.write_text(json.dumps(data), encoding="utf-8")


def test_copied_folder_never_touches_the_originals_icons(
    env: Env, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert env.main("--yes", "--ai") == 0
    original_icon = env.shortcut_files[0]
    original_record = env.paths.install_record.read_text()
    copy = tmp_path / "instagraph2"
    shutil.copytree(env.paths.repo, copy, symlinks=True)
    env.paths = cli.Paths.under(copy)
    env.events.clear()
    capsys.readouterr()

    assert env.main("--uninstall", "--yes") == 0
    assert not any(e.startswith("remove_shortcuts") for e in env.events)
    assert "another copy" in capsys.readouterr().out
    assert original_icon.exists()
    assert (tmp_path / "repo" / "backend" / ".instagraph-install.json").read_text() == (
        original_record
    )


def test_copied_folder_install_starts_a_new_record(env: Env, tmp_path: Path) -> None:
    assert env.main("--yes", "--ai") == 0
    copy = tmp_path / "instagraph2"
    shutil.copytree(env.paths.repo, copy, symlinks=True)
    env.paths = cli.Paths.under(copy)
    env.shortcut_files = [tmp_path / "desktop" / "Instagraph (instagraph2).desktop"]
    assert env.main("--yes") == 0
    assert env.previous == []  # the original's icons are not "previous" here
    record = env.record()
    assert record["repo"] == str(copy)
    assert record["shortcuts"] == [str(env.shortcut_files[0])]
    assert record["ai"] is False  # nothing else was taken over from the original either


def test_record_from_other_os_keeps_the_rest(env: Env) -> None:
    write_record(env, os=other_os(), shortcuts=[foreign_shortcut()], ai=True, node="private")
    record = cli.Record.load(env.paths.install_record, env.paths.repo)
    assert record.shortcuts == []
    assert record.elsewhere == [foreign_shortcut()]
    assert record.elsewhere_os == other_os()
    assert record.data["ai"] is True


def test_record_without_os_counts_as_other_os(env: Env) -> None:
    raw = {"version": 1, "repo": str(env.paths.repo), "shortcuts": ["x"]}
    env.paths.install_record.write_text(json.dumps(raw))
    assert cli.Record.load(env.paths.install_record, env.paths.repo).shortcuts == []


def test_record_repo_compare_ignores_spelling(env: Env) -> None:
    write_record(env, shortcuts=[str(env.shortcut_files[0])])
    odd = env.paths.repo / "backend" / ".."
    assert cli.Record.load(env.paths.install_record, odd).shortcuts == [str(env.shortcut_files[0])]


# --- Recreating the venv: the old one survives until the packages are in -------------


def make_older(env: Env) -> None:
    cfg = env.paths.venv / "pyvenv.cfg"
    cfg.write_text(cfg.read_text().replace("version_info = 3.", "version_info = 2."))


def old_venvs(env: Env) -> list[Path]:
    return sorted(env.paths.backend.glob(".venv-old-*"))


def test_recreate_keeps_private_node_and_cleans_up(env: Env) -> None:
    assert env.main("--yes", "--no-shortcut") == 0
    make_older(env)
    (env.paths.venv / "node").mkdir()
    (env.paths.venv / "node" / "VERSION").write_text("v24.1.0")
    env.events.clear()
    assert env.main("--yes", "--no-shortcut") == 0
    assert env.events[0] == "create_venv"
    assert "version_info = 3." in (env.paths.venv / "pyvenv.cfg").read_text()
    assert (env.paths.venv / "node" / "VERSION").read_text() == "v24.1.0"
    assert old_venvs(env) == []


@pytest.mark.parametrize("failure", ["pip", "ctrl-c"])
def test_failed_recreate_puts_the_old_venv_back(env: Env, failure: str) -> None:
    assert env.main("--yes", "--no-shortcut") == 0
    make_older(env)
    (env.paths.venv / "node").mkdir()

    def pip(cmd: list[str], *, cwd: Path, show_all: bool) -> tuple[int, str]:
        if "--upgrade" in cmd:
            return 0, ""
        if failure == "ctrl-c":
            raise KeyboardInterrupt
        return 1, "ERROR: network down"

    env.mp.setattr(venv_setup, "run_streaming", pip)
    assert env.main("--yes", "--no-shortcut") == (130 if failure == "ctrl-c" else 1)
    assert "version_info = 2." in (env.paths.venv / "pyvenv.cfg").read_text()  # the old one
    assert (env.paths.venv / "node").is_dir()
    assert old_venvs(env) == []


def test_recreate_preflight_runs_before_anything_is_deleted(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    assert env.main("--yes", "--no-shortcut") == 0
    make_older(env)

    def cannot() -> None:
        raise InstallError("This Python can't create virtual environments.")

    env.mp.setattr(venv_setup, "check_can_create", cannot)
    env.events.clear()
    assert env.main("--yes", "--no-shortcut") == 1
    assert "create_venv" not in env.events
    assert "version_info = 2." in (env.paths.venv / "pyvenv.cfg").read_text()
    assert "can't create" in capsys.readouterr().err


def test_recreate_keeps_the_ai_assistant(env: Env) -> None:
    assert env.main("--yes", "--ai", "--no-shortcut") == 0
    make_older(env)
    env.anthropic = False  # a brand-new venv doesn't have it yet
    assert env.main("--yes", "--no-shortcut") == 0
    assert env.install_target() == f"{env.paths.backend}[ai]"
    assert env.record()["ai"] is True


def test_recreate_with_no_ai_drops_it(env: Env) -> None:
    assert env.main("--yes", "--ai", "--no-shortcut") == 0
    make_older(env)
    assert env.main("--yes", "--no-ai", "--no-shortcut") == 0
    assert env.install_target() == str(env.paths.backend)
    assert env.record()["ai"] is False


# --- Desktop icon: "no" and hand-deleted icons are remembered --------------------------


def test_declined_icon_is_not_asked_again(env: Env) -> None:
    env.interactive(["n", "n", "n"])  # AI? no. Icon? no. Start? no.
    assert env.main() == 0
    assert "create_shortcuts" not in env.events
    assert env.record()["shortcut"] is False

    env.fe_state = FrontendState(False, False, "")
    asked = env.interactive(["n"])  # only "Start now?"
    assert env.main() == 0
    assert "create_shortcuts" not in env.events
    assert len(asked) == 1 and "Start Instagraph now?" in asked[0]

    env.interactive(["n"])  # only "Start now?"
    assert env.main("--shortcut") == 0  # the flag overrides it
    assert "create_shortcuts" in env.events
    assert env.record()["shortcut"] is True


def test_icon_deleted_by_hand_doesnt_come_back(env: Env) -> None:
    assert env.main("--yes") == 0
    env.shortcut_files[0].unlink()
    env.events.clear()
    assert env.main("--yes") == 0
    assert "create_shortcuts" not in env.events
    record = env.record()
    assert record["shortcuts"] == []
    assert record["shortcut"] is False


def test_icon_deleted_by_hand_is_asked_about(env: Env) -> None:
    assert env.main("--yes") == 0
    env.shortcut_files[0].unlink()
    env.events.clear()
    asked = env.interactive(["y", "n"])  # Create it again? yes. Start? no.
    assert env.main() == 0
    assert "deleted" in asked[0]
    assert "create_shortcuts" in env.events
    assert env.previous == [str(env.shortcut_files[0])]  # earlier list passed on


def test_leftover_icon_file_isnt_an_icon(env: Env, tmp_path: Path) -> None:
    ico = tmp_path / "desktop" / "icon.ico"
    env.shortcut_files.append(ico)
    assert env.main("--yes") == 0
    env.shortcut_files[0].unlink()  # the shortcut is gone, its .ico remains
    env.events.clear()
    assert env.main("--yes") == 0
    assert "create_shortcuts" not in env.events
    assert env.record()["shortcuts"] == [str(ico)]


def test_want_shortcut_declined_and_deleted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui, "ask_yes_no", lambda *a, **k: pytest.fail("asked"))
    assert cli.want_shortcut(None, [], declined=True) is False
    assert cli.want_shortcut(True, [], declined=True) is True
    seen: dict[str, Any] = {}

    def ask(question: str, *, default: bool, yes_value: bool | None = None) -> bool:
        seen.update(q=question, default=default, yes_value=yes_value)
        return default

    monkeypatch.setattr(ui, "ask_yes_no", ask)
    assert cli.want_shortcut(None, [], deleted=True) is True
    assert seen == {
        "q": "The desktop icon was deleted. Create it again?",
        "default": True,
        "yes_value": False,
    }  # --yes: not recreated


# --- Uninstall: other OS, failures, the record file -----------------------------------


def other_os_venv(env: Env) -> None:
    env.paths.venv.mkdir()
    home = "/usr/bin" if common.IS_WINDOWS else "C:\\Users\\me\\Python312"
    (env.paths.venv / "pyvenv.cfg").write_text(f"home = {home}\n")


def test_uninstall_other_os_venv_with_yes_refuses(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    other_os_venv(env)
    write_record(env, shortcuts=[str(env.shortcut_files[0])])
    assert env.main("--uninstall", "--yes") == 1
    assert env.paths.venv.exists()
    assert env.events == []
    err = capsys.readouterr().err
    assert "breaks the" in err and "Nothing was removed" in err


def test_uninstall_other_os_venv_default_no(env: Env) -> None:
    other_os_venv(env)
    asked = env.interactive([""])
    assert env.main("--uninstall") == 1
    assert "[y/N]" in asked[0]
    assert env.paths.venv.exists()


def test_uninstall_other_os_venv_yes(env: Env) -> None:
    other_os_venv(env)
    env.interactive(["y"])
    assert env.main("--uninstall") == 0
    assert not env.paths.venv.exists()


def test_uninstall_leaves_other_os_shortcuts(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    write_record(env, os=other_os(), shortcuts=[foreign_shortcut()])
    before = env.paths.install_record.read_text()
    assert env.main("--uninstall", "--yes") == 1
    assert env.events == [f"remove_shortcuts({[foreign_shortcut()]})"]
    out, err = capsys.readouterr()
    assert "remove it there" in out
    assert "record was kept" in err
    assert env.paths.install_record.read_text() == before  # untouched for the other system


def test_uninstall_failure_keeps_only_failed_entries(
    env: Env, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    menu = tmp_path / "apps" / "instagraph.desktop"
    env.shortcut_files.append(menu)
    installed(env)
    env.cannot_remove = {str(menu)}
    assert env.main("--uninstall", "--yes") == 1
    assert env.record()["shortcuts"] == [str(menu)]
    assert not env.paths.venv.exists()  # the rest was still done
    assert "Not everything could be removed" in capsys.readouterr().err


def test_uninstall_venv_delete_failure(env: Env) -> None:
    installed(env)

    def fail(path: Path) -> None:
        raise PermissionError(13, "in use")

    env.mp.setattr(common, "remove_tree", fail)
    assert env.main("--uninstall", "--yes") == 1
    assert env.paths.install_record.exists()


def test_data_dir_from_setting(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("INSTAGRAPH_DATA_DIR", str(tmp_path / "elsewhere"))
    assert cli.Paths.from_common().data == tmp_path / "elsewhere"


# --- Windows: --run, network folders ----------------------------------------------------


def test_windows_run_ignores_ctrl_c_and_returns_child_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(common, "IS_WINDOWS", True)
    before = signal.getsignal(signal.SIGINT)
    seen: list[object] = []

    def call(cmd: list[str], **kw: Any) -> int:
        seen.append(signal.getsignal(signal.SIGINT))
        return 0

    monkeypatch.setattr(subprocess, "call", call)
    assert cli.start_app(cli.Paths.under(tmp_path)) == 0
    assert seen == [signal.SIG_IGN]
    assert signal.getsignal(signal.SIGINT) is before


def test_network_folder_stops_early(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(common, "IS_WINDOWS", True)
    monkeypatch.setattr(common, "is_unc_path", lambda p: True)
    assert env.main("--yes") == 1
    assert env.events == []
    assert not env.paths.venv.exists()
    assert "network folder" in capsys.readouterr().err
