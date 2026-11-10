"""install.py: argument parsing and the install / update / uninstall flow. Owner: core agent.

See docs/plans/cross-platform-installer.md, "install.py flow", "Existing installs",
"--uninstall".
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import traceback
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from installer import common, frontend, node, running, shortcuts, ui, venv_setup
from installer.common import InstallError

# --- Paths ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Paths:
    """Every location the flow touches. Tests point these into a temporary folder."""

    repo: Path
    backend: Path
    venv: Path
    dist: Path
    node_modules: Path
    data: Path
    run_file: Path
    install_record: Path
    env_file: Path
    run_py: Path

    @classmethod
    def from_common(cls) -> Paths:
        """The real locations, read from :mod:`installer.common` at call time."""
        return cls(
            repo=common.REPO_ROOT,
            backend=common.BACKEND_DIR,
            venv=common.VENV_DIR,
            dist=common.DIST_DIR,
            node_modules=common.NODE_MODULES_DIR,
            data=common.DATA_DIR,
            run_file=common.RUN_FILE,
            install_record=common.INSTALL_RECORD,
            env_file=common.BACKEND_DIR / ".env",
            run_py=common.RUN_PY,
        )

    @classmethod
    def under(cls, repo: Path) -> Paths:
        """The standard layout below ``repo``."""
        backend = repo / "backend"
        venv = backend / ".venv"
        return cls(
            repo=repo,
            backend=backend,
            venv=venv,
            dist=repo / "frontend" / "dist",
            node_modules=repo / "frontend" / "node_modules",
            data=repo / "data",
            run_file=venv / common.RUN_FILE.name,
            install_record=venv / common.INSTALL_RECORD.name,
            env_file=backend / ".env",
            run_py=repo / "run.py",
        )

    def show(self, path: Path) -> str:
        """``path`` relative to the repo for messages, e.g. ``backend/.venv``."""
        try:
            return path.relative_to(self.repo).as_posix()
        except ValueError:
            return str(path)


# --- Arguments -----------------------------------------------------------------------

_INSTALL_ONLY = ("ai", "shortcut", "rebuild", "private_node", "run")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="install.py",
        description=(
            "Install Instagraph, or update it when it is already installed. "
            "Run it again at any time (for example after downloading a new version)."
        ),
        epilog="Your imported data in the data/ folder is never deleted, not even by --uninstall.",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="don't ask questions; use the default answer for each one",
    )
    ai = parser.add_mutually_exclusive_group()
    ai.add_argument(
        "--ai",
        dest="ai",
        action="store_const",
        const=True,
        default=None,
        help="also install the optional AI assistant",
    )
    ai.add_argument(
        "--no-ai",
        dest="ai",
        action="store_const",
        const=False,
        help="don't install the AI assistant (the default; an installed one is kept)",
    )
    icon = parser.add_mutually_exclusive_group()
    icon.add_argument(
        "--shortcut",
        dest="shortcut",
        action="store_const",
        const=True,
        default=None,
        help="create a desktop icon without asking",
    )
    icon.add_argument(
        "--no-shortcut",
        dest="shortcut",
        action="store_const",
        const=False,
        help="don't create or update a desktop icon",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="build the app's interface again, even if it looks up to date",
    )
    parser.add_argument(
        "--private-node",
        action="store_true",
        help="use a private copy of Node.js, even if Node.js is already installed",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="start Instagraph when the installation is done",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="remove the desktop icon and the installed packages (keeps your data)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="show every command and its full output",
    )
    return parser


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.uninstall:
        for name in _INSTALL_ONLY:
            if getattr(args, name) != parser.get_default(name):
                flag = {"ai": "--ai/--no-ai", "shortcut": "--shortcut/--no-shortcut"}.get(
                    name, "--" + name.replace("_", "-")
                )
                parser.error(f"--uninstall can't be combined with {flag}")
    return args


# --- Entry point ---------------------------------------------------------------------


def main(argv: list[str] | None = None, *, paths: Paths | None = None) -> int:
    """Entry point of install.py. Returns the process exit code."""
    try:
        args = parse_args(argv)
    except SystemExit as exc:  # --help or a usage error
        return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 2)
    ui.configure(assume_yes=args.yes, verbose=args.verbose)
    paths = paths or Paths.from_common()
    try:
        return uninstall(args, paths) if args.uninstall else install(args, paths)
    except running.Cancelled:
        print("Cancelled. Nothing was changed.", flush=True)
        return 1
    except InstallError as exc:
        if isinstance(exc, common.CommandError) and exc.output.strip():
            tail = "\n".join(exc.output.strip().splitlines()[-15:])
            if tail not in (exc.hint or ""):
                print(tail, file=sys.stderr, flush=True)
        ui.error(exc.message, exc.hint)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", flush=True)
        return 130
    except Exception as exc:
        if args.verbose:
            traceback.print_exc()
            ui.error(f"Something unexpected went wrong: {exc}")
        else:
            ui.error(
                f"Something unexpected went wrong: {exc}",
                "Run again with --verbose for details.",
            )
        return 1


# --- Install record ------------------------------------------------------------------

_RECORD_KEYS = ("shortcuts", "ai", "node")


@dataclass
class Record:
    """The install record, kept in memory and saved after every step that changes it."""

    path: Path
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Record:
        raw = common.read_install_record(path)
        return cls(path, {k: raw[k] for k in _RECORD_KEYS if k in raw})

    @property
    def shortcuts(self) -> list[str]:
        value = self.data.get("shortcuts")
        return [str(p) for p in value] if isinstance(value, list) else []

    def update(self, **values: Any) -> None:
        self.data.update(values)
        self.save()

    def save(self) -> None:
        if self.path.parent.is_dir():
            common.write_install_record(
                {
                    "shortcuts": self.shortcuts,
                    "ai": self.data.get("ai"),
                    "node": self.data.get("node"),
                },
                self.path,
            )


def merge_shortcuts(existing: Sequence[str], created: Sequence[Path | str]) -> list[str]:
    """Paths for the record: earlier shortcuts that still exist, plus the new ones."""
    merged = [p for p in existing if Path(p).exists() or Path(p).is_symlink()]
    merged += [str(p) for p in created]
    return list(dict.fromkeys(merged))


def want_shortcut(flag: bool | None, existing: Sequence[str]) -> bool:
    """Create/update the desktop icon? Flags decide; a re-run updates existing ones silently."""
    if flag is not None:
        return flag
    if existing:
        return True
    return ui.ask_yes_no("Create a desktop icon?", default=True)


# --- Install -------------------------------------------------------------------------


def _ports(paths: Paths) -> list[int]:
    return [common.DEFAULT_PORT, common.configured_port(paths.env_file)]


def _check_python() -> None:
    if sys.version_info < common.MIN_PYTHON:  # install.py checks first; keep a safety net
        raise InstallError(
            "Instagraph needs Python 3.12 or newer.",
            "Get it from https://www.python.org/downloads/ and run install.py again with it.",
        )
    major, minor, micro = sys.version_info[:3]
    ui.info(f"Python {major}.{minor}.{micro} on {common.os_label()} ({sys.executable})")


def start_command(paths: Paths) -> str:
    """How to start Instagraph from a terminal in the repo folder."""
    if common.IS_WINDOWS:
        return "py run.py"
    return f"{paths.show(common.venv_python(paths.venv))} run.py"


def install(args: argparse.Namespace, paths: Paths) -> int:
    summary: list[str] = []
    record = Record.load(paths.install_record)

    ui.step("Checking that Instagraph isn't running")
    running.ensure_not_running(paths.run_file, _ports(paths))

    ui.step("Checking Python")
    _check_python()
    if common.is_wsl() and str(paths.repo).startswith("/mnt/"):
        ui.info(venv_setup.WSL_MNT_TIP)

    ui.step(f"Setting up the Python environment ({paths.show(paths.venv)})")
    outcome = venv_setup.ensure_venv(paths.venv)
    summary.append(f"Python environment: {outcome}")
    record.save()  # a recreated venv lost the record file; put it back
    python = common.venv_python(paths.venv)

    ui.step("Installing Python packages (this can take a few minutes the first time)")
    fresh = outcome != "reused"
    already_ai = not fresh and venv_setup.anthropic_installed(python)
    ai = venv_setup.choose_ai(args.ai, already_ai, answered_no=record.data.get("ai") is False)
    if already_ai and args.ai is False:
        ui.info("The AI assistant is already installed; it stays installed.")
    packages = venv_setup.pip_install(
        python, backend_dir=paths.backend, repo_root=paths.repo, ai=ai, fresh=fresh
    )
    summary.append(f"Python packages: {packages}")
    has_ai = ai or already_ai
    summary.append(
        "AI assistant: " + ("installed (turn it on in Settings)" if has_ai else "not installed")
    )
    record.update(ai=has_ai)

    ui.step("Setting up the app's interface")
    state = frontend.frontend_state(rebuild=args.rebuild)
    if state.needs_install or state.needs_build:
        if state.reason:
            ui.info(f"Needs a build: {state.reason}.")
        tool = node.ensure_node(force_private=args.private_node)
        record.update(node="private" if tool.private else "system")
        result = frontend.build_frontend(tool, state).strip()
        summary.append(result if result.lower().startswith("frontend") else f"Frontend: {result}")
    else:
        ui.info("Already up to date.")
        summary.append("Frontend: up to date")

    ui.step("Desktop icon")
    summary += _shortcut_step(args, record)

    ui.step("Done")
    for line in summary:
        ui.info(line)
    has_icon = bool(record.shortcuts)
    print(flush=True)
    how = (
        f"double-click the Instagraph icon, or run this in {paths.repo}:"
        if has_icon
        else f"run this in {paths.repo}:"
    )
    ui.info(f"To start Instagraph, {how}")
    ui.info(f"    {start_command(paths)}")

    if args.run or ui.ask_yes_no("Start Instagraph now?", default=True, yes_value=False):
        return start_app(paths)
    return 0


def _shortcut_step(args: argparse.Namespace, record: Record) -> list[str]:
    existing = record.shortcuts
    if not want_shortcut(args.shortcut, existing):
        if existing:
            ui.info("Leaving the existing desktop icon as it is.")
            return ["Desktop icon: left as it is"]
        return ["Desktop icon: not created"]
    try:
        result = shortcuts.create_shortcuts(previous=existing)
    except InstallError as exc:
        # Everything else worked; a missing icon shouldn't fail the whole install.
        ui.warn(f"Could not create the desktop icon: {exc.message}")
        if exc.hint:
            ui.info(exc.hint)
        return ["Desktop icon: not created (see the message above)"]
    record.update(shortcuts=merge_shortcuts(existing, result.created))
    if result.messages:
        return list(result.messages)
    return ["Desktop icon: " + ("updated" if existing else "created")]


def start_app(paths: Paths) -> int:
    """Start run.py with the venv's Python: replace this process, or wait for it on Windows."""
    python = common.venv_python(paths.venv)
    cmd = [str(python), str(paths.run_py)]
    ui.step("Starting Instagraph")
    sys.stdout.flush()
    sys.stderr.flush()
    if common.IS_WINDOWS:  # os.execv misbehaves in Windows consoles
        try:
            return subprocess.call(cmd, cwd=paths.repo)
        except KeyboardInterrupt:
            return 130
    os.chdir(paths.repo)
    os.execv(cmd[0], cmd)
    return 0  # pragma: no cover  (execv doesn't return)


# --- Uninstall -----------------------------------------------------------------------


@dataclass(frozen=True)
class UninstallPlan:
    shortcuts: list[str]  # removed through shortcuts.remove_shortcuts
    remove: list[Path]  # deleted without asking
    ask: list[Path]  # deleted only if the user agrees (default no)
    keep: list[Path]  # never deleted (data/)


def uninstall_plan(paths: Paths, record: Record) -> UninstallPlan:
    return UninstallPlan(
        shortcuts=record.shortcuts,
        remove=[paths.venv] if paths.venv.exists() else [],
        ask=[p for p in (paths.dist, paths.node_modules) if p.exists()],
        keep=[paths.data],
    )


def uninstall(args: argparse.Namespace, paths: Paths) -> int:
    ui.step("Checking that Instagraph isn't running")
    running.ensure_not_running(paths.run_file, _ports(paths))
    if paths.venv.exists() and venv_setup.running_inside(paths.venv):
        raise venv_setup.refuse_inside(paths.venv, "would be deleted")

    plan = uninstall_plan(paths, Record.load(paths.install_record))
    assert paths.data not in plan.remove and paths.data not in plan.ask

    ui.step("Removing desktop icons")
    if plan.shortcuts:
        for line in shortcuts.remove_shortcuts(plan.shortcuts):
            ui.info(line)
    else:
        ui.info("No desktop icons to remove.")

    ui.step(f"Removing the Python environment ({paths.show(paths.venv)})")
    for path in plan.remove:
        _guard_not_data(path, paths)
        venv_setup.remove_tree(path)
        ui.info(f"Deleted {paths.show(path)}")
    if not plan.remove:
        ui.info("Nothing to remove.")

    ui.step("The app's interface files")
    descriptions = {
        paths.dist: "the built app interface",
        paths.node_modules: "the tools used to build it",
    }
    for path in plan.ask:
        what = descriptions.get(path, "")
        if ui.ask_yes_no(
            f"Also delete {paths.show(path)} ({what}; install.py can make it again)?",
            default=False,
        ):
            _guard_not_data(path, paths)
            venv_setup.remove_tree(path)
            ui.info(f"Deleted {paths.show(path)}")
        else:
            ui.info(f"Kept {paths.show(path)}")
    if not plan.ask:
        ui.info("Nothing to remove.")

    ui.step("Your data")
    ui.info(f"Your imported data was NOT deleted. It is in: {paths.data}")
    ui.info(
        "To delete it, use Settings > Clear all data in Instagraph (before uninstalling), "
        "or delete that folder yourself."
    )
    ui.step("Uninstalled. To install again, run install.py.")
    return 0


def _guard_not_data(path: Path, paths: Paths) -> None:
    """Refuse to delete data/ or anything that contains it (belt and braces)."""
    target = path.resolve()
    data = paths.data.resolve()
    if target == data or data.is_relative_to(target):
        raise InstallError(f"Refusing to delete {path}: it contains your data.")
