"""backend/.venv: detect its state, create or recreate it, install the Python packages.

Owner: core agent. See docs/plans/cross-platform-installer.md, "install.py flow" steps 1-3,
"Existing installs" (the table and the venv half of case B).
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import re
import shutil
import stat
import subprocess
import sys
import venv as venv_module
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from installer import common, ui
from installer.common import InstallError
from installer.running import CLOSE_HINT

VenvState = Literal["missing", "ok", "older-python", "other-os", "broken"]

ONE_CLONE_PER_OS = (
    "Tip: use a separate copy of Instagraph for each system. For WSL, clone it into your "
    "Linux home folder (~/...), which is also much faster than /mnt/c."
)
WSL_MNT_TIP = (
    "Tip: Instagraph installs and runs much faster from your Linux home folder (~/...) "
    "than from /mnt/..."
)


# --- State detection -----------------------------------------------------------------


@dataclass(frozen=True)
class VenvInfo:
    state: VenvState
    #: The interpreter this OS would use (it may not exist).
    python: Path
    #: For "other-os": "Windows" or "Linux or macOS".
    created_on: str | None = None
    #: major.minor from pyvenv.cfg, if readable.
    version: tuple[int, int] | None = None


def read_pyvenv_cfg(venv: Path) -> dict[str, str]:
    """``key = value`` pairs of ``pyvenv.cfg`` (keys lower-cased), or {} if missing."""
    values: dict[str, str] = {}
    try:
        text = (venv / "pyvenv.cfg").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return values
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip().lower()] = value.strip()
    return values


def is_windows_path(value: str) -> bool:
    """``C:\\...``, ``C:/...`` or a UNC path ``\\\\server\\...``."""
    return bool(re.match(r"^[A-Za-z]:[\\/]", value)) or value.startswith("\\\\")


def cfg_version(cfg: dict[str, str]) -> tuple[int, int] | None:
    """major.minor from ``version_info`` (3.12.3.final.0) or ``version`` (3.12.3)."""
    for key in ("version_info", "version"):
        match = re.match(r"^(\d+)\.(\d+)", cfg.get(key, ""))
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


def _python_runs(python: Path) -> bool:
    try:
        result = common.run([python, "-c", "import sys"], capture=True, check=False)
    except InstallError:
        return False
    return result.returncode == 0


def detect_venv_state(
    venv: Path,
    *,
    windows: bool = common.IS_WINDOWS,
    current: tuple[int, int] | None = None,
    python_runs: Callable[[Path], bool] | None = None,
) -> VenvInfo:
    """What state is ``venv`` in? Doesn't change anything.

    - missing: the folder doesn't exist.
    - other-os: made on Windows while this is Linux/macOS (or the other way round), seen
      from ``home =`` in pyvenv.cfg, or this OS's interpreter is missing while the other
      OS's is there.
    - older-python: pyvenv.cfg says it was made with an older major.minor than ``current``.
    - broken: this OS's interpreter is missing or doesn't start.
    - ok: none of the above.
    """
    current = current or (sys.version_info.major, sys.version_info.minor)
    python_runs = python_runs or _python_runs
    python = common.venv_python(venv, windows=windows)
    if not venv.exists():
        return VenvInfo("missing", python)

    cfg = read_pyvenv_cfg(venv)
    version = cfg_version(cfg)
    home = cfg.get("home", "")
    other_python = common.venv_python(venv, windows=not windows)
    other_label = "Linux or macOS" if windows else "Windows"

    if home and (is_windows_path(home) != windows):
        return VenvInfo("other-os", python, other_label, version)
    if not python.exists() and other_python.exists():
        return VenvInfo("other-os", python, other_label, version)
    if version is not None and version < current:
        return VenvInfo("older-python", python, None, version)
    if not python.exists() or not python_runs(python):
        return VenvInfo("broken", python, None, version)
    return VenvInfo("ok", python, None, version)


def running_inside(venv: Path) -> bool:
    """True if this installer runs with the venv's own Python (it can't delete itself)."""

    def norm(path: Path | str) -> str:
        try:
            resolved = Path(path).resolve()
        except OSError:
            resolved = Path(path).absolute()
        return os.path.normcase(str(resolved))

    return norm(sys.prefix) == norm(venv)


def refuse_inside(venv: Path, why: str) -> InstallError:
    return InstallError(
        f"This installer is running with the Python from {venv}, which {why}.",
        "Run it with your normal Python instead: "
        + ("py install.py" if common.IS_WINDOWS else "python3 install.py"),
    )


# --- Create / remove -----------------------------------------------------------------


def _venv_package_hint() -> str:
    major, minor = sys.version_info[:2]
    if common.IS_LINUX:
        return (
            "On Debian or Ubuntu, install Python's venv package first:\n"
            f"  sudo apt install python{major}.{minor}-venv   (or: sudo apt install "
            "python3-venv)\nthen run install.py again."
        )
    return "Reinstall Python from https://www.python.org/downloads/ and run install.py again."


def remove_tree(path: Path) -> None:
    """Delete a folder, also when files in it are read-only (common on Windows)."""

    def onexc(func: Callable[..., object], name: str, exc: BaseException) -> None:
        if isinstance(exc, FileNotFoundError):
            return
        if not isinstance(exc, PermissionError):
            raise exc
        # Clear the read-only bit on the file (Windows) or its folder (POSIX), then retry.
        target = Path(name)
        for p in (target, target.parent):
            with contextlib.suppress(OSError):
                mode = os.lstat(p).st_mode
                if not stat.S_ISLNK(mode):
                    os.chmod(p, mode | stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        func(name)

    if not path.exists() and not path.is_symlink():
        return
    try:
        shutil.rmtree(path, onexc=onexc)
    except OSError as exc:
        raise InstallError(
            f"Could not delete {path}: {exc}",
            "If Instagraph or a terminal using this folder is open, close it. " + CLOSE_HINT,
        ) from exc


def create_venv(venv: Path, *, builder: Callable[[Path], None] | None = None) -> None:
    """Create ``venv`` with pip, using the running Python.

    If it fails (typically Debian/Ubuntu without the venv package, so ``ensurepip`` is
    missing), the half-made folder is removed and an InstallError explains what to install.
    """

    def default_builder(path: Path) -> None:
        venv_module.EnvBuilder(with_pip=True).create(path)

    build = builder or default_builder
    if builder is None and importlib.util.find_spec("ensurepip") is None:
        raise InstallError("This Python can't create virtual environments.", _venv_package_hint())
    try:
        build(venv)
    except (Exception, SystemExit) as exc:
        # Debian's patched venv module may sys.exit() when ensurepip is unavailable.
        with contextlib.suppress(InstallError):
            remove_tree(venv)
        ui.detail(f"venv creation failed: {exc!r}")
        raise InstallError(
            f"Could not create the Python environment in {venv}.", _venv_package_hint()
        ) from exc
    if not common.venv_python(venv).exists():
        with contextlib.suppress(InstallError):
            remove_tree(venv)
        raise InstallError(
            f"Could not create the Python environment in {venv}.", _venv_package_hint()
        )


def recreate_venv(
    venv: Path, *, keep_node: bool, create: Callable[[Path], None] | None = None
) -> None:
    """Delete and create ``venv`` again.

    With ``keep_node``, the private Node.js in it (same OS, still usable) is moved aside and
    put back, so it isn't downloaded again.
    """
    node_dir = venv / common.PRIVATE_NODE_DIR.name
    parked: Path | None = None
    if keep_node and node_dir.is_dir():
        parked = venv.parent / ".instagraph-node-keep"
        with contextlib.suppress(InstallError):
            remove_tree(parked)
        try:
            os.replace(node_dir, parked)
        except OSError:
            parked = None
    try:
        remove_tree(venv)
        (create or create_venv)(venv)
    finally:
        if parked is not None and parked.is_dir():
            if venv.is_dir() and not node_dir.exists():
                os.replace(parked, node_dir)
            else:
                with contextlib.suppress(InstallError):
                    remove_tree(parked)


# --- Step 2: make sure the venv is usable --------------------------------------------

VenvOutcome = Literal["created", "recreated", "reused"]


def ensure_venv(
    venv: Path,
    *,
    info: VenvInfo | None = None,
    create: Callable[[Path], None] | None = None,
    recreate: Callable[..., None] | None = None,
) -> VenvOutcome:
    """Create the venv if missing, or recreate it (after asking) if it can't be reused."""
    info = info or detect_venv_state(venv)
    create = create or create_venv
    recreate = recreate or recreate_venv
    shown = _display(venv)
    if info.state == "ok":
        ui.info(f"Using the existing {shown}.")
        return "reused"
    if info.state == "missing":
        ui.info(f"Creating {shown} ...")
        create(venv)
        return "created"

    here = common.os_label()
    if info.state == "other-os":
        other = info.created_on or "another system"
        problem = f"{shown} was created on {other} and doesn't work in {here}."
        if running_inside(venv):
            raise refuse_inside(venv, "was made on another system")
        if not ui.interactive():
            raise InstallError(
                f"{problem} It was left alone, because recreating it would break the "
                f"{other} install of this folder.",
                ONE_CLONE_PER_OS + "\nTo recreate it anyway, run install.py without --yes "
                "and answer yes.",
            )
        ui.warn(problem)
        ui.info(ONE_CLONE_PER_OS)
        if not ui.ask_yes_no(
            f"Recreate it? This breaks the {other} install of this folder.", default=False
        ):
            raise InstallError(f"Stopped. {shown} was not changed.", ONE_CLONE_PER_OS)
        ui.info(f"Recreating {shown} ...")
        recreate(venv, keep_node=False)
        return "recreated"

    if info.state == "older-python":
        old = ".".join(map(str, info.version)) if info.version else "an older Python"
        now = f"{sys.version_info.major}.{sys.version_info.minor}"
        question = f"{shown} was made with Python {old}. Recreate it with Python {now}?"
        if not ui.ask_yes_no(question, default=True):
            ui.info(f"Keeping {shown} as it is.")
            return "reused"
    else:  # broken
        if not ui.ask_yes_no(
            f"{shown} is damaged (its Python doesn't start). Recreate it?", default=True
        ):
            raise InstallError(
                f"Stopped. {shown} can't be used as it is.",
                "Run install.py again and answer yes to recreate it.",
            )
    if running_inside(venv):
        raise refuse_inside(venv, "needs to be recreated")
    ui.info(f"Recreating {shown} ...")
    recreate(venv, keep_node=True)
    return "recreated"


def _display(path: Path) -> str:
    try:
        return str(path.relative_to(common.REPO_ROOT))
    except ValueError:
        return str(path)


# --- Step 3: Python packages ---------------------------------------------------------

_FILE_IN_USE = re.compile(r"\[?WinError (?:32|5)\]|being used by another process", re.IGNORECASE)


def is_file_in_use_error(output: str) -> bool:
    """True if pip failed because Windows had a file open (a running Instagraph)."""
    return bool(_FILE_IN_USE.search(output))


def file_in_use_error() -> InstallError:
    return InstallError(
        "The Python packages could not be updated because a file is in use, probably by "
        "a running Instagraph.",
        CLOSE_HINT + "\nThis also repairs a half-finished update.",
    )


def anthropic_installed(python: Path) -> bool:
    """True if the AI assistant's package (``anthropic``) is installed in the venv."""
    code = "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('anthropic') else 1)"
    try:
        return common.run([python, "-c", code], capture=True, check=False).returncode == 0
    except InstallError:
        return False


def choose_ai(
    flag: bool | None,
    already_installed: bool,
    *,
    answered_no: bool = False,
    ask: Callable[..., bool] | None = None,
) -> bool:
    """Install the ``[ai]`` extra?

    ``--ai`` / ``--no-ai`` decide. Without a flag: yes if it is already installed (it stays,
    no question); no if an earlier run recorded "no" (asked once); else ask with default
    No (the assistant sends data to an online API). ``--no-ai`` never uninstalls it.
    """
    if flag is not None:
        return flag
    if already_installed:
        return True
    if answered_no:
        ui.info("The AI assistant is not installed (add --ai to install it).")
        return False
    ui.info(
        "The AI assistant is optional. It sends parts of your data to an online AI service "
        "(Anthropic), so it stays off unless you turn it on."
    )
    return (ask or ui.ask_yes_no)("Install the AI assistant?", default=False)


#: Lines from pip shown without --verbose, so the user sees progress.
_PROGRESS = ("Collecting ", "Installing collected packages", "Successfully installed")


def run_streaming(cmd: Sequence[str | Path], *, cwd: Path, show_all: bool) -> tuple[int, str]:
    """Run a command, print its progress lines, and return (exit code, full output)."""
    ui.detail(f"$ {common.format_cmd(cmd)}   (in {cwd})")
    lines: list[str] = []
    try:
        proc = subprocess.Popen(
            [str(c) for c in cmd],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise InstallError(f"Could not run {cmd[0]}: {exc}") from exc
    assert proc.stdout is not None
    with proc:
        for raw in proc.stdout:
            line = raw.rstrip()
            lines.append(line)
            if show_all or line.startswith(_PROGRESS):
                ui.info(line)
    return proc.returncode, "\n".join(lines)


def _tail(output: str, n: int = 15) -> str:
    return "\n".join(output.strip().splitlines()[-n:])


def changed_packages(output: str) -> list[str]:
    """Packages pip reports as "Successfully installed", without instagraph itself."""
    names: list[str] = []
    for line in output.splitlines():
        if line.startswith("Successfully installed "):
            names += [n for n in line.split()[2:] if not n.lower().startswith("instagraph-")]
    return names


Runner = Callable[..., tuple[int, str]]


def pip_install(
    python: Path,
    *,
    backend_dir: Path,
    repo_root: Path,
    ai: bool,
    fresh: bool,
    runner: Runner | None = None,
) -> str:
    """Upgrade pip, then ``pip install -e backend[ai]``. Returns the summary word."""
    runner = runner or run_streaming
    verbose = ui.verbose()
    pip = [str(python), "-m", "pip"]
    quiet = [] if verbose else ["--quiet"]

    code, out = runner(
        [*pip, "install", "--upgrade", "pip", "--disable-pip-version-check", *quiet],
        cwd=repo_root,
        show_all=verbose,
    )
    if code != 0:
        if is_file_in_use_error(out):
            raise file_in_use_error()
        ui.warn("Could not update pip; continuing with the installed version.")
        if out.strip() and not verbose:
            ui.detail(_tail(out, 5))

    target = f"{backend_dir}[ai]" if ai else str(backend_dir)
    code, out = runner(
        [*pip, "install", "--disable-pip-version-check", "-e", target],
        cwd=repo_root,
        show_all=verbose,
    )
    if code != 0:
        if is_file_in_use_error(out):
            raise file_in_use_error()
        raise InstallError(
            "Installing the Python packages failed.",
            ((_tail(out) + "\n") if out.strip() and not verbose else "")
            + "Check your internet connection and run install.py again "
            "(add --verbose to see everything).",
        )
    if fresh:
        return "installed"
    changed = changed_packages(out)
    if not changed:
        return "up to date"
    return f"updated ({len(changed)} package{'s' if len(changed) != 1 else ''})"
