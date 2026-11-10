"""install.py: argument parsing and the install / update / uninstall flow. Owner: core agent.

See docs/plans/cross-platform-installer.md, "install.py flow", "Existing installs",
"--uninstall".
"""

from __future__ import annotations

import argparse
import contextlib
import os
import signal
import subprocess
import sys
import traceback
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
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
            data=common.data_dir(),  # honours INSTAGRAPH_DATA_DIR
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
            install_record=backend / common.INSTALL_RECORD.name,
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

_RECORD_KEYS = ("shortcuts", "ai", "node", "shortcut")


def _same_path(a: object, b: Path) -> bool:
    if not isinstance(a, str) or not a:
        return False
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


@dataclass
class Record:
    """The install record, kept in memory and saved after every step that changes it."""

    path: Path
    repo: Path
    data: dict[str, Any] = field(default_factory=dict)
    #: Shortcuts that an install on another OS listed (case B). Not updated from here;
    #: uninstall hands them to shortcuts.remove_shortcuts_detailed, which leaves them alone.
    elsewhere: list[str] = field(default_factory=list)
    #: The OS that made them, e.g. "Windows".
    elsewhere_os: str = "the other system"

    @classmethod
    def load(cls, path: Path, repo: Path) -> Record:
        """Read the record. Its shortcuts only count if it was written for this folder on
        this OS: a copied folder must not remove (or overwrite the record of) the
        original's icons, and paths from another OS mean nothing here."""
        raw = common.read_install_record(path)
        record = cls(path, repo)
        if not raw:
            return record
        listed = raw.get("shortcuts")
        listed = [str(p) for p in listed] if isinstance(listed, list) else []
        same_repo = _same_path(raw.get("repo"), repo)
        made_on = raw.get("os")
        if made_on != common.os_label():
            # From WSL a Windows folder has another path, so the repo can't be compared.
            record.elsewhere = listed
            record.elsewhere_os = str(made_on) if made_on else "another system"
            if listed:
                ui.info(
                    f"The desktop icons in the install record were made on "
                    f"{record.elsewhere_os}; they are not changed from here."
                )
        elif not same_repo:
            ui.info(
                f"The install record belongs to another copy of Instagraph ({raw.get('repo')}); "
                "it is ignored and its desktop icons are left alone."
            )
            return record
        if same_repo:
            record.data = {k: raw[k] for k in _RECORD_KEYS if k in raw and k != "shortcuts"}
            if made_on == common.os_label():
                record.data["shortcuts"] = listed
        return record

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
                    "repo": str(self.repo),
                    "shortcuts": self.shortcuts,
                    "ai": self.data.get("ai"),
                    "node": self.data.get("node"),
                    "shortcut": self.data.get("shortcut"),
                },
                self.path,
            )


def _exists(path: str) -> bool:
    return Path(path).exists() or Path(path).is_symlink()


def merge_shortcuts(existing: Sequence[str], created: Sequence[Path | str]) -> list[str]:
    """Paths for the record: earlier shortcuts that still exist, plus the new ones."""
    merged = [p for p in existing if _exists(p)]
    merged += [str(p) for p in created]
    return list(dict.fromkeys(merged))


#: Files in the record that aren't icons themselves (the .ico copied for the WSL icon).
_NOT_ICONS = (".ico",)


def want_shortcut(
    flag: bool | None, existing: Sequence[str], *, declined: bool = False, deleted: bool = False
) -> bool:
    """Create/update the desktop icon?

    Flags decide. Otherwise: existing icons are updated without asking; an earlier "no"
    is remembered; icons deleted by hand are asked about again, but ``--yes`` doesn't
    bring them back.
    """
    if flag is not None:
        return flag
    if existing:
        return True
    if declined:
        ui.info("No desktop icon (you said no earlier; add --shortcut to create one).")
        return False
    if deleted:
        return ui.ask_yes_no(
            "The desktop icon was deleted. Create it again?", default=True, yes_value=False
        )
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


def _check_location(paths: Paths) -> None:
    if common.IS_WINDOWS and common.is_unc_path(paths.repo):
        raise InstallError(
            f"Instagraph can't be installed from a network folder ({paths.repo}).",
            "Copy it to a folder on this computer, or map a drive letter, and run the "
            "installer again.",
        )


def start_command(paths: Paths) -> str:
    """How to start Instagraph from a terminal in the repo folder."""
    python = paths.show(common.venv_python(paths.venv, windows=common.IS_WINDOWS))
    if common.IS_WINDOWS:  # not "py run.py": py may pick another Python, or be missing
        return f"{PureWindowsPath(python)} run.py"
    return f"{python} run.py"


def install(args: argparse.Namespace, paths: Paths) -> int:
    summary: list[str] = []
    record = Record.load(paths.install_record, paths.repo)

    ui.step("Checking that Instagraph isn't running")
    running.ensure_not_running(paths.run_file, _ports(paths))

    ui.step("Checking Python")
    _check_python()
    _check_location(paths)
    if common.is_wsl() and str(paths.repo).startswith("/mnt/"):
        ui.info(venv_setup.WSL_MNT_TIP)

    ui.step(f"Setting up the Python environment ({paths.show(paths.venv)})")
    setup = venv_setup.ensure_venv(paths.venv)
    summary.append(f"Python environment: {setup.outcome}")
    python = common.venv_python(paths.venv)

    ui.step("Installing Python packages (this can take a few minutes the first time)")
    fresh = setup.outcome != "reused"
    try:
        installed_ai = not fresh and venv_setup.anthropic_installed(python)
        # After a recreate the record remembers that the assistant was installed.
        already_ai = installed_ai or record.data.get("ai") is True
        ai = venv_setup.choose_ai(args.ai, already_ai, answered_no=record.data.get("ai") is False)
        if installed_ai and args.ai is False:
            ui.info("The AI assistant is already installed; it stays installed.")
        packages = venv_setup.pip_install(
            python, backend_dir=paths.backend, repo_root=paths.repo, ai=ai, fresh=fresh
        )
    except BaseException:
        setup.rollback()  # a recreated venv: put the previous one back
        raise
    setup.commit()
    summary.append(f"Python packages: {packages}")
    has_ai = ai or installed_ai
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
    listed = record.shortcuts
    on_disk = [p for p in listed if _exists(p)]
    icons = [p for p in on_disk if not p.lower().endswith(_NOT_ICONS)]
    want = want_shortcut(
        args.shortcut,
        icons,
        declined=record.data.get("shortcut") is False,
        deleted=bool(listed) and not icons,
    )
    if not want:
        if on_disk != listed:
            record.update(shortcuts=on_disk)  # forget icons deleted by hand
        if icons:
            ui.info("Leaving the existing desktop icon as it is.")
            return ["Desktop icon: left as it is"]
        record.update(shortcut=False)  # remembered: not asked again
        return ["Desktop icon: not created"]
    try:
        result = shortcuts.create_shortcuts(previous=listed)
    except InstallError as exc:
        # Everything else worked; a missing icon shouldn't fail the whole install.
        ui.warn(f"Could not create the desktop icon: {exc.message}")
        if exc.hint:
            ui.info(exc.hint)
        return ["Desktop icon: not created (see the message above)"]
    record.update(shortcuts=merge_shortcuts(listed, result.created), shortcut=True)
    if result.messages:
        return list(result.messages)
    return ["Desktop icon: " + ("updated" if icons else "created")]


def start_app(paths: Paths) -> int:
    """Start run.py with the venv's Python: replace this process, or wait for it on Windows."""
    python = common.venv_python(paths.venv)
    cmd = [str(python), str(paths.run_py)]
    ui.step("Starting Instagraph")
    sys.stdout.flush()
    sys.stderr.flush()
    if common.IS_WINDOWS:  # os.execv misbehaves in Windows consoles
        # Ctrl+C reaches both processes. Only run.py should react to it; its exit code
        # is the result (not 130 for a normal stop).
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            return subprocess.call(cmd, cwd=paths.repo)
        finally:
            signal.signal(signal.SIGINT, previous)
    os.chdir(paths.repo)
    os.execv(cmd[0], cmd)
    return 0  # pragma: no cover  (execv doesn't return)


# --- Uninstall -----------------------------------------------------------------------


@dataclass(frozen=True)
class UninstallPlan:
    shortcuts: list[str]  # handed to shortcuts.remove_shortcuts_detailed (it checks each)
    remove: list[Path]  # deleted without asking
    ask: list[Path]  # deleted only if the user agrees (default no)
    keep: list[Path]  # never deleted (the data folder)


def uninstall_plan(paths: Paths, record: Record) -> UninstallPlan:
    return UninstallPlan(
        # Shortcuts of another OS are reported, not removed, by remove_shortcuts_detailed.
        shortcuts=list(dict.fromkeys(record.shortcuts + record.elsewhere)),
        remove=[paths.venv] if paths.venv.exists() else [],
        ask=[p for p in (paths.dist, paths.node_modules) if p.exists()],
        keep=[paths.data],
    )


def _check_venv_os(paths: Paths) -> None:
    """Case B: deleting a venv made on the other OS breaks that install. Ask first."""
    info = venv_setup.detect_venv_state(paths.venv)
    if info.state != "other-os":
        return
    other = info.created_on or "another system"
    problem = (
        f"{paths.show(paths.venv)} was created on {other}. Deleting it breaks the {other} "
        "install of this folder."
    )
    if not ui.interactive():
        raise InstallError(
            f"{problem} Nothing was removed.",
            f"Uninstall from {other} instead. To uninstall from here anyway, run "
            "install.py --uninstall without --yes and answer yes.",
        )
    ui.warn(problem)
    if not ui.ask_yes_no("Uninstall anyway?", default=False):
        raise running.Cancelled


def _delete(path: Path, paths: Paths) -> bool:
    """Delete ``path``; on failure warn and return False."""
    _guard_not_data(path, paths)
    try:
        common.remove_tree(path)
    except OSError as exc:
        ui.warn(f"Could not delete {paths.show(path)}: {exc}")
        return False
    ui.info(f"Deleted {paths.show(path)}")
    return True


def uninstall(args: argparse.Namespace, paths: Paths) -> int:
    ui.step("Checking that Instagraph isn't running")
    running.ensure_not_running(paths.run_file, _ports(paths))
    if paths.venv.exists():
        if venv_setup.running_inside(paths.venv):
            raise venv_setup.refuse_inside(paths.venv, "would be deleted")
        _check_venv_os(paths)

    record = Record.load(paths.install_record, paths.repo)
    plan = uninstall_plan(paths, record)
    assert paths.data not in plan.remove and paths.data not in plan.ask
    ok = True

    ui.step("Removing desktop icons")
    failed: list[str] = []
    if plan.shortcuts:
        result = shortcuts.remove_shortcuts_detailed(plan.shortcuts, repo=paths.repo)
        for line in result.messages:
            ui.info(line)
        failed = list(result.failed)
    else:
        ui.info("No desktop icons to remove.")

    ui.step(f"Removing the Python environment ({paths.show(paths.venv)})")
    for path in plan.remove:
        ok = _delete(path, paths) and ok
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
            ok = _delete(path, paths) and ok
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

    if failed or not ok:
        if failed != plan.shortcuts:  # keep only what is still there to remove
            Record(paths.install_record, paths.repo, {"shortcuts": failed}).save()
        ui.warn(
            "Not everything could be removed (see above). The install record was kept. "
            "Remove desktop icons made on another system from there; close any program "
            "using the other files, then run install.py --uninstall again."
        )
        return 1
    with contextlib.suppress(OSError):
        paths.install_record.unlink(missing_ok=True)
    ui.step("Uninstalled. To install again, run install.py.")
    return 0


def _guard_not_data(path: Path, paths: Paths) -> None:
    """Refuse to delete data/ or anything that contains it (belt and braces)."""
    target = path.resolve()
    data = paths.data.resolve()
    if target == data or data.is_relative_to(target):
        raise InstallError(f"Refusing to delete {path}: it contains your data.")
