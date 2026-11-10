"""Desktop icons on Windows, macOS, Linux and WSL. Owner: shortcuts agent.

See docs/plans/cross-platform-installer.md, "Desktop icon on each OS" and case C.

Every shortcut runs ``"<venv python>" "<repo>/run.py"`` in the repo folder, in a console
or terminal window. Before a shortcut is written, an existing one with the same name is
read (case C): one for this copy is updated, one for a deleted copy (its folder is gone or
has no run.py) is replaced, one for another copy asks "keep both / replace", and anything
else, including one that can't be read, is kept.
"Keep both" writes "Instagraph (<folder>)" next to it.

Uninstall (:func:`remove_shortcuts`) never trusts the install record blindly: it removes
only files that look like our shortcuts and, read again, still start this copy.

The keyword-only parameters of :func:`create_shortcuts` and :func:`remove_shortcuts` exist
for tests: they let a test use temporary folders and a fake ``runner`` instead of the real
desktop and real programs.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from installer import ui
from installer.common import (
    APP_NAME,
    IS_MACOS,
    IS_WINDOWS,
    REPO_ROOT,
    atomic_write_text,
    format_cmd,
    is_wsl,
    venv_python,
)


@dataclass
class ShortcutResult:
    created: list[Path] = field(default_factory=list)  # every file written (for the record)
    messages: list[str] = field(default_factory=list)  # summary lines, e.g. "Desktop icon: updated"


@dataclass
class RemoveResult:
    messages: list[str] = field(default_factory=list)  # one summary line per record entry
    #: Record entries that may still be this copy's shortcut and are still there: made on
    #: another system, or deleting failed. Empty means uninstall removed everything it should.
    failed: list[str] = field(default_factory=list)


Platform = Literal["windows", "macos", "linux", "wsl"]
#: Runs a command and returns it with ``stdout``/``stderr`` captured as text. Must not
#: raise on a non-zero exit code; may raise ``OSError`` if the program is missing.
Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
Which = Callable[[str], str | None]

COMMENT = "Statistics and charts for your Instagram messages"
LINUX_FILE = "instagraph.desktop"
#: File types uninstall deletes (plus this copy's own icon copy, see :func:`wsl_icon_name`).
SHORTCUT_SUFFIXES = (".desktop", ".lnk", ".command")

_DESKTOP = "Desktop icon"
_START_MENU = "Start menu entry"
_APP_MENU = "App menu entry"
_WIN_DESKTOP = "Windows desktop icon"
_WIN_START_MENU = "Windows Start menu entry"


class ShortcutError(Exception):
    """One shortcut could not be read or written. Reported, never fatal."""


# --- Public API ----------------------------------------------------------------------


def _wsl_wants_windows_icon(previous: Iterable[str | Path]) -> bool:
    """On WSL: Windows icon or Linux shortcut? Reuse the earlier choice, else ask."""
    suffixes = {Path(p).suffix.lower() for p in previous}
    if ".lnk" in suffixes:
        return True
    if ".desktop" in suffixes:
        return False
    return ui.ask_yes_no("Create a Windows desktop icon (starts Instagraph in WSL)?", default=True)


def create_shortcuts(
    *,
    repo: Path = REPO_ROOT,
    python: Path | None = None,
    platform: Platform | None = None,
    home: Path | None = None,
    desktop_dir: Path | None = None,
    apps_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
    runner: Runner | None = None,
    which: Which | None = None,
    previous: Iterable[str | Path] = (),
) -> ShortcutResult:
    """Create or update this copy's shortcuts (asks on conflicts, see case C).

    ``previous``: shortcuts from an earlier install (the install record). On WSL they
    decide between a Windows and a Linux icon, so an update doesn't ask again.

    ``repo``/``python``: the copy to start (default: this repo and its venv Python).
    ``platform``: force a code path; default is the OS we run on.
    ``home``/``desktop_dir``/``apps_dir``: folders to use instead of the real ones
    (Linux and macOS; on Windows and WSL the folders come from PowerShell).
    ``env``: environment variables (``XDG_DATA_HOME``, ``WSL_DISTRO_NAME``).
    ``runner``/``which``: replace ``subprocess.run`` and ``shutil.which``.

    With a fake ``runner`` (a test) but no ``home``/``env``, an empty temporary home and
    an empty environment are used, so a test can never write into the real home folder.
    """
    if runner is not None and home is None:
        home = Path(tempfile.mkdtemp(prefix="instagraph-test-home-"))
    if runner is not None and env is None:
        env = {}
    ctx = _make_ctx(repo, platform, home, env, runner, which)
    if python is not None:
        ctx.python = python
    if ctx.plat == "windows":
        _create_windows(ctx)
    elif ctx.plat == "macos":
        _create_macos(ctx, desktop_dir)
    elif ctx.plat == "wsl" and _wsl_wants_windows_icon(previous):
        if not _create_wsl(ctx):
            _create_linux(ctx, desktop_dir, apps_dir)
    else:
        _create_linux(ctx, desktop_dir, apps_dir)
    return ctx.result


def remove_shortcuts(
    paths: Iterable[str | Path],
    *,
    repo: Path = REPO_ROOT,
    platform: Platform | None = None,
    env: Mapping[str, str] | None = None,
    runner: Runner | None = None,
    which: Which | None = None,
) -> list[str]:
    """Remove this copy's shortcuts listed in the install record. Returns summary lines.

    Same as :func:`remove_shortcuts_detailed`, without the list of failures.
    """
    return remove_shortcuts_detailed(
        paths, repo=repo, platform=platform, env=env, runner=runner, which=which
    ).messages


def remove_shortcuts_detailed(
    paths: Iterable[str | Path],
    *,
    repo: Path = REPO_ROOT,
    platform: Platform | None = None,
    env: Mapping[str, str] | None = None,
    runner: Runner | None = None,
    which: Which | None = None,
) -> RemoveResult:
    """Remove this copy's shortcuts listed in the install record.

    The record is only a hint (it may be stale, copied from another folder or edited), so
    each entry is checked first:

    - Only absolute paths ending in ``.desktop``, ``.lnk`` or ``.command``, or this copy's
      own icon copy (``Instagraph/icon-<hash>.ico``), are considered. Others are skipped.
    - A path that can't exist on this OS (a Windows path on Linux, ``/mnt/c/...`` outside
      WSL, ...) is reported as not removed, and listed in ``failed``.
    - A shortcut is read again (as in case C) and deleted only if it starts this copy, or if
      it is broken or unreadable and has one of the names this copy uses. One that now
      starts another copy, or something else, is kept.

    An "Instagraph" folder left empty by deleting the icon copy is deleted too.
    ``repo``/``platform``/``env``/``runner``/``which``: as for :func:`create_shortcuts`.
    """
    ctx = _make_ctx(repo, platform, Path(), env, runner, which)
    result = RemoveResult()
    remover = _Remover(ctx, result)
    for raw in paths:
        remover.remove(str(raw))
    return result


# --- Shared machinery ----------------------------------------------------------------


def _current_platform() -> Platform:
    if IS_WINDOWS:
        return "windows"
    if IS_MACOS:
        return "macos"
    return "wsl" if is_wsl() else "linux"


def _make_ctx(
    repo: Path,
    platform: Platform | None,
    home: Path | None,
    env: Mapping[str, str] | None,
    runner: Runner | None,
    which: Which | None,
) -> _Ctx:
    plat = platform or _current_platform()
    repo = Path(os.path.abspath(repo))
    return _Ctx(
        plat=plat,
        repo=repo,
        python=venv_python(repo / "backend" / ".venv", windows=plat == "windows"),
        home=home or Path.home(),
        env=os.environ if env is None else env,
        runner=runner or _default_runner,
        which=which or shutil.which,
    )


def _default_runner(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
    ui.detail(f"$ {format_cmd(_short_cmd(cmd))}")
    return subprocess.run(
        list(cmd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )


def _short_cmd(cmd: Sequence[str]) -> list[str]:
    """The command for log output, without the long base64 PowerShell script."""
    out = list(cmd)
    if "-EncodedCommand" in out:
        i = out.index("-EncodedCommand") + 1
        if i < len(out):
            out[i] = "<script>"
    return out


class _Decider:
    """Asks "keep both / replace" once per other copy, even for several shortcuts."""

    def __init__(self) -> None:
        self._answers: dict[str, str] = {}

    def keep_both(self, where: str) -> bool:
        if where not in self._answers:
            self._answers[where] = ui.ask_choice(
                f"A desktop icon for another Instagraph copy exists ({where}).",
                {"k": "keep both", "r": "replace it"},
                default="k",
                yes_value="k",
            )
        return self._answers[where] == "k"


@dataclass
class _Ctx:
    plat: Platform
    repo: Path
    python: Path
    home: Path
    env: Mapping[str, str]
    runner: Runner
    which: Which
    decider: _Decider = field(default_factory=_Decider)
    result: ShortcutResult = field(default_factory=ShortcutResult)

    def call(self, cmd: Sequence[str]) -> subprocess.CompletedProcess[str] | None:
        """Run a helper program. None if it couldn't be started or timed out."""
        try:
            return self.runner(cmd)
        except (OSError, subprocess.SubprocessError) as exc:
            ui.detail(f"{cmd[0]}: {exc}")
            return None

    def hash8(self) -> str:
        return _hash8(self.repo)

    def fail(self, label: str, exc: Exception) -> None:
        msg = f"{label}: could not be created ({exc})"
        ui.warn(msg)
        self.result.messages.append(msg)


@dataclass
class _Pointer:
    """Where an existing shortcut points."""

    display: str  # the repo path as written in the shortcut, for messages
    local: Path | None  # that path on this computer, or None if it can't be checked
    instagraph: bool  # does it start some run.py at all?


@dataclass
class _Verdict:
    #: missing: no such file; same: starts this copy; gone: starts a copy whose folder is
    #: gone or has no run.py; other: starts another copy; foreign: not Instagraph;
    #: unreadable: couldn't be read or parsed (treated like foreign, never overwritten).
    kind: Literal["missing", "same", "gone", "other", "foreign", "unreadable"]
    note: str = ""  # for "gone": "it pointed to X, which no longer exists"
    where: str = ""  # for "other": the other copy's folder


def _hash8(repo: Path) -> str:
    """Short hash of the repo path: tells this copy's files apart from other copies'."""
    return hashlib.sha1(str(repo).encode("utf-8"), usedforsecurity=False).hexdigest()[:8]


def _same_path(a: Path, b: Path, *, fold_case: bool) -> bool:
    def norm(p: Path) -> str:
        s = os.path.normcase(os.path.realpath(p))
        return s.casefold() if fold_case else s

    return norm(a) == norm(b)


def _judge(path: Path, read: Callable[[Path], _Pointer | None], ctx: _Ctx) -> _Verdict:
    if not (path.exists() or path.is_symlink()):
        return _Verdict("missing")
    try:
        pointer = read(path)
    except (OSError, ShortcutError, ValueError) as exc:
        # Possibly a passing PowerShell/COM hiccup: never treat the file as broken.
        ui.detail(f"Could not read {path}: {exc}")
        return _Verdict("unreadable")
    if pointer is None:
        return _Verdict("unreadable")
    if not pointer.instagraph:
        return _Verdict("foreign")
    if pointer.local is None:
        return _Verdict("other", where=pointer.display)
    if _same_path(pointer.local, ctx.repo, fold_case=ctx.plat == "windows"):
        return _Verdict("same")
    if not pointer.local.is_dir():
        return _Verdict("gone", note=f"it pointed to {pointer.display}, which no longer exists")
    if not (pointer.local / "run.py").is_file():
        return _Verdict(
            "gone", note=f"it pointed to {pointer.display}, which no longer has Instagraph"
        )
    return _Verdict("other", where=pointer.display)


def _place(
    ctx: _Ctx,
    label: str,
    names: Callable[[str], Path],
    read: Callable[[Path], _Pointer | None],
    write: Callable[[Path, str], None],
) -> Path | None:
    """Write one shortcut, applying case C. Returns the written file, or None on error.

    ``names(display_name)`` gives the file for a display name; ``write(path, name)``
    writes it.
    """
    primary_name = APP_NAME
    alt_name = f"{APP_NAME} ({_safe_name(ctx.repo.name, ctx)})"
    primary, alt = names(primary_name), names(alt_name)
    try:
        verdict = _judge(primary, read, ctx)
        if verdict.kind in ("missing", "same", "gone"):
            # A "keep both" copy from an earlier run is updated instead of asking again.
            if verdict.kind != "same" and alt != primary and _judge(alt, read, ctx).kind == "same":
                write(alt, alt_name)
                ctx.result.messages.append(f"{label}: updated ({alt})")
                return alt
            write(primary, primary_name)
            if verdict.kind == "missing":
                msg = f"created ({primary})"
            elif verdict.kind == "same":
                msg = "updated"
            else:
                msg = f"replaced ({verdict.note})"
            ctx.result.messages.append(f"{label}: {msg}")
            return primary
        if _judge(alt, read, ctx).kind == "same":
            write(alt, alt_name)
            ctx.result.messages.append(f"{label}: updated ({alt})")
            return alt
        if verdict.kind == "other" and not ctx.decider.keep_both(verdict.where):
            write(primary, primary_name)
            ctx.result.messages.append(
                f"{label}: replaced (it pointed to another copy at {verdict.where})"
            )
            return primary
        # Keep both: write "Instagraph (<folder>)" next to the existing one.
        if alt != primary and _judge(alt, read, ctx).kind in ("other", "foreign", "unreadable"):
            alt_name = f"{APP_NAME} ({_safe_name(ctx.repo.name, ctx)} {ctx.hash8()})"
            alt = names(alt_name)
        write(alt, alt_name)
        if verdict.kind == "other":
            kept = f"another copy's icon ({verdict.where})"
        elif verdict.kind == "unreadable":
            kept = f"the existing {primary.name} (it could not be read)"
        else:
            kept = f"the existing {primary.name}"
        ctx.result.messages.append(f'{label}: created as "{alt_name}" ({alt}); kept {kept}')
        return alt
    except (OSError, ShortcutError) as exc:
        ctx.fail(label, exc)
        return None


def _safe_name(name: str, ctx: _Ctx) -> str:
    """A folder name usable in a Windows file name (WSL folders may contain ``:`` etc.)."""
    if ctx.plat in ("windows", "wsl"):
        return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(". ") or "copy"
    return name.replace("/", "_")


# --- Linux ---------------------------------------------------------------------------


def _desktop_escape(value: str) -> str:
    """Escape a Desktop Entry string value (``\\``, newline, tab, carriage return)."""
    return (
        value.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r")
    )


def _desktop_unescape(value: str) -> str:
    table = {"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}
    return re.sub(r"\\(.)", lambda m: table.get(m.group(1), m.group(0)), value)


def _exec_arg(arg: str) -> str:
    """Quote one ``Exec`` argument (before the general string escaping)."""
    return '"' + re.sub(r'(["`$\\])', r"\\\1", arg).replace("%", "%%") + '"'


def desktop_entry(ctx: _Ctx, name: str) -> str:
    """The content of an ``instagraph.desktop`` file."""
    exec_line = " ".join(_exec_arg(str(p)) for p in (ctx.python, ctx.repo / "run.py"))
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Version=1.0",
        f"Name={_desktop_escape(name)}",
        f"Comment={COMMENT}",
        f"Exec={_desktop_escape(exec_line)}",
        f"Path={_desktop_escape(str(ctx.repo))}",
        f"Icon={_desktop_escape(str(ctx.repo / 'assets' / 'icon.png'))}",
        "Terminal=true",
        "Categories=Utility;",
        "",
    ]
    return "\n".join(lines)


def _split_exec(value: str) -> list[str]:
    """Split an (already unescaped) ``Exec`` value into arguments; field codes dropped."""
    args: list[str] = []
    i, n = 0, len(value)
    while i < n:
        if value[i] == " ":
            i += 1
            continue
        buf: list[str] = []
        if value[i] == '"':
            i += 1
            while i < n and value[i] != '"':
                if value[i] == "\\" and i + 1 < n:
                    i += 1
                buf.append(value[i])
                i += 1
            if i >= n:
                raise ValueError("unterminated quote in Exec")
            i += 1
        else:
            while i < n and value[i] != " ":
                buf.append(value[i])
                i += 1
        arg = "".join(buf)
        if re.fullmatch(r"%[a-zA-Z]", arg):
            continue
        args.append(arg.replace("%%", "%"))
    return args


def _read_desktop_entry(path: Path) -> _Pointer | None:
    values: dict[str, str] = {}
    group = ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("["):
            group = line
        elif group == "[Desktop Entry]" and "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values.setdefault(key.strip(), _desktop_unescape(value.strip()))
    if "Exec" not in values:
        return None
    args = _split_exec(values["Exec"])
    run_py = next((a for a in args if a.replace("\\", "/").rsplit("/", 1)[-1] == "run.py"), None)
    if run_py is None:
        return _Pointer(display="", local=None, instagraph=False)
    folder = Path(run_py).parent
    if not Path(run_py).is_absolute():
        if not values.get("Path"):
            return None
        folder = Path(values["Path"]) / folder
    return _Pointer(display=str(folder), local=folder, instagraph=True)


def _linux_desktop_dir(ctx: _Ctx) -> Path | None:
    if ctx.which("xdg-user-dir"):
        out = ctx.call(["xdg-user-dir", "DESKTOP"])
        if out is not None and out.returncode == 0 and out.stdout.strip():
            found = Path(out.stdout.strip())
            if not _same_path(found, ctx.home, fold_case=False) and found.is_dir():
                return found
    fallback = ctx.home / "Desktop"
    return fallback if fallback.is_dir() else None


def _linux_apps_dir(ctx: _Ctx) -> Path:
    data_home = ctx.env.get("XDG_DATA_HOME") or str(ctx.home / ".local" / "share")
    return Path(data_home) / "applications"


def _create_linux(ctx: _Ctx, desktop_dir: Path | None, apps_dir: Path | None) -> None:
    apps = apps_dir or _linux_apps_dir(ctx)
    hashed = f"instagraph-{ctx.hash8()}.desktop"

    def names_in(folder: Path) -> Callable[[str], Path]:
        return lambda name: folder / (LINUX_FILE if name == APP_NAME else hashed)

    def write(path: Path, name: str) -> None:
        atomic_write_text(path, desktop_entry(ctx, name))
        path.chmod(0o755)

    written = _place(ctx, _APP_MENU, names_in(apps), _read_desktop_entry, write)
    if written is not None:
        ctx.result.created.append(written)
        if ctx.which("update-desktop-database"):
            ctx.call(["update-desktop-database", str(apps)])

    desktop = desktop_dir or _linux_desktop_dir(ctx)
    if desktop is None:
        ctx.result.messages.append(
            f"{_DESKTOP}: skipped (no desktop folder found; Instagraph is in the app menu)"
        )
        return
    written = _place(ctx, _DESKTOP, names_in(desktop), _read_desktop_entry, write)
    if written is not None:
        ctx.result.created.append(written)
        if ctx.which("gio"):
            # GNOME shows "Untrusted application launcher" without this. Non-fatal.
            out = ctx.call(["gio", "set", str(written), "metadata::trusted", "true"])
            if out is None or out.returncode != 0:
                ui.detail("gio could not mark the desktop icon as trusted.")


# --- macOS ---------------------------------------------------------------------------


def command_script(ctx: _Ctx) -> str:
    """The content of ``Instagraph.command``."""
    q = shlex.quote
    return (
        "#!/bin/bash\n"
        "# Starts Instagraph. Created by install.py; running install.py again updates it.\n"
        f"cd {q(str(ctx.repo))} || exit 1\n"
        f"exec {q(str(ctx.python))} {q(str(ctx.repo / 'run.py'))}\n"
    )


def _read_command(path: Path) -> _Pointer | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    cd_dir: str | None = None
    run_py: str | None = None
    for line in text.splitlines():
        try:
            tokens = shlex.split(line, comments=True)
        except ValueError:
            continue
        if len(tokens) >= 2 and tokens[0] == "cd":
            cd_dir = tokens[1]
        for tok in tokens:
            if re.split(r"[\\/]", tok)[-1] == "run.py":
                run_py = tok
    if not text.strip():
        return None
    if run_py is None:
        return _Pointer(display="", local=None, instagraph=False)
    folder = Path(run_py).parent
    if not Path(run_py).is_absolute():
        if cd_dir is None:
            return None
        folder = Path(cd_dir) / folder
    return _Pointer(display=str(folder), local=folder, instagraph=True)


def osascript_icon_cmd(icns: Path, target: Path) -> list[str]:
    """The ``osascript`` command that gives ``target`` the icon from ``icns``."""
    script = (
        "ObjC.import('AppKit');"
        f"var img = $.NSImage.alloc.initWithContentsOfFile({json.dumps(str(icns))});"
        "if (!img || img.isNil()) throw new Error('icon not readable');"
        "var ok = $.NSWorkspace.sharedWorkspace.setIconForFileOptions("
        f"img, {json.dumps(str(target))}, 0);"
        "if (!ok) throw new Error('setIcon failed');"
        "'ok'"
    )
    return ["osascript", "-l", "JavaScript", "-e", script]


def _create_macos(ctx: _Ctx, desktop_dir: Path | None) -> None:
    desktop = desktop_dir or ctx.home / "Desktop"
    if not desktop.is_dir():
        ctx.result.messages.append(f"{_DESKTOP}: skipped (no desktop folder at {desktop})")
        return

    def write(path: Path, name: str) -> None:
        atomic_write_text(path, command_script(ctx))
        path.chmod(0o755)

    written = _place(ctx, _DESKTOP, lambda name: desktop / f"{name}.command", _read_command, write)
    if written is None:
        return
    ctx.result.created.append(written)
    out = ctx.call(osascript_icon_cmd(ctx.repo / "assets" / "icon.icns", written))
    if out is None or out.returncode != 0:
        ui.detail(f"osascript: {(out.stderr if out else '').strip()}")
        ctx.result.messages.append(
            f"{_DESKTOP}: the custom icon could not be set (the shortcut still works)"
        )


# --- Windows (native and from WSL) ---------------------------------------------------

_PS_PRELUDE = """\
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
function Out-Ascii([string]$s) {
  [regex]::Replace($s, '[^\\x00-\\x7F]', { param($m) '\\u{0:x4}' -f [int][char]$m.Value })
}
try {
"""
_PS_EPILOGUE = """
} catch {
  Write-Output ('ERROR: ' + (Out-Ascii $_.Exception.Message))
  exit 1
}
"""
#: Characters PowerShell accepts as single quotes; doubling one escapes it.
_PS_QUOTES = "'\u2018\u2019\u201a\u201b"


def ps_quote(value: str) -> str:
    """A PowerShell single-quoted string literal."""
    return "'" + "".join(c * 2 if c in _PS_QUOTES else c for c in value) + "'"


def encode_ps(script: str) -> str:
    """``-EncodedCommand`` form: base64 of UTF-16LE, so non-ASCII paths survive."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def decode_ps(encoded: str) -> str:
    return base64.b64decode(encoded).decode("utf-16-le")


def _find_powershell(ctx: _Ctx) -> str | None:
    if ctx.plat == "windows":
        found = ctx.which("powershell.exe") or ctx.which("powershell")
        if found:
            return found
        root = ctx.env.get("SystemRoot", r"C:\Windows")
        candidate = Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        return str(candidate) if candidate.exists() else None
    found = ctx.which("powershell.exe")
    if found:
        return found
    candidate = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    return str(candidate) if candidate.exists() else None


def _powershell(ctx: _Ctx, exe: str, body: str) -> str:
    """Run a PowerShell script body. Returns stdout; raises :class:`ShortcutError`."""
    script = _PS_PRELUDE + body + _PS_EPILOGUE
    cmd = [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
    out = ctx.call([*cmd, "-EncodedCommand", encode_ps(script)])
    if out is None:
        raise ShortcutError("PowerShell could not be started")
    stdout = (out.stdout or "").replace("\ufeff", "").strip()
    if out.returncode != 0:
        errors = [ln[7:] for ln in stdout.splitlines() if ln.startswith("ERROR: ")]
        errors += (out.stderr or "").strip().splitlines()[-1:]
        detail = errors[0] if errors else f"exit code {out.returncode}"
        raise ShortcutError(f"PowerShell failed: {_unescape_ascii(detail)}")
    return stdout


def _unescape_ascii(text: str) -> str:
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)


def _ps_json(ctx: _Ctx, exe: str, body: str) -> dict[str, str]:
    stdout = _powershell(ctx, exe, body)
    last = stdout.splitlines()[-1] if stdout else ""
    try:
        data = json.loads(last)
    except ValueError as exc:
        raise ShortcutError(f"unexpected PowerShell output: {last[:200]!r}") from exc
    if not isinstance(data, dict):
        raise ShortcutError(f"unexpected PowerShell output: {last[:200]!r}")
    return {str(k): "" if v is None else str(v) for k, v in data.items()}


def _query_folders(ctx: _Ctx, exe: str) -> dict[str, str]:
    """Desktop (OneDrive-aware), Start menu Programs, LocalAppData and SystemRoot."""
    body = (
        "$r = @{\n"
        "  desktop = [Environment]::GetFolderPath('Desktop')\n"
        "  programs = [Environment]::GetFolderPath('Programs')\n"
        "  localappdata = [Environment]::GetFolderPath('LocalApplicationData')\n"
        "  systemroot = [string]$env:SystemRoot\n"
        "}\n"
        "Write-Output (Out-Ascii ($r | ConvertTo-Json -Compress))\n"
    )
    return _ps_json(ctx, exe, body)


def lnk_write_script(
    lnk: str, target: str, arguments: str, workdir: str, icon: str, description: str = COMMENT
) -> str:
    """PowerShell body that (re)creates a ``.lnk`` file. All values are Windows paths."""
    return (
        f"$p = {ps_quote(lnk)}\n"
        "if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Force }\n"
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($p)\n"
        f"$s.TargetPath = {ps_quote(target)}\n"
        f"$s.Arguments = {ps_quote(arguments)}\n"
        f"$s.WorkingDirectory = {ps_quote(workdir)}\n"
        f"$s.IconLocation = {ps_quote(icon + ',0')}\n"
        f"$s.Description = {ps_quote(description)}\n"
        "$s.Save()\n"
        "Write-Output 'OK'\n"
    )


def lnk_read_script(lnk: str) -> str:
    return (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({ps_quote(lnk)})\n"
        "$r = @{ target = [string]$s.TargetPath; args = [string]$s.Arguments;"
        " workdir = [string]$s.WorkingDirectory }\n"
        "Write-Output (Out-Ascii ($r | ConvertTo-Json -Compress))\n"
    )


def _win_tokens(args: str) -> list[str]:
    """Split a Windows command line roughly (quotes group, no escapes; enough for paths)."""
    return [
        m.group(1) if m.group(1) is not None else m.group(2)
        for m in re.finditer(r'"([^"]*)"|(\S+)', args)
    ]


def _looks_windows(path: str) -> bool:
    return bool(re.match(r"^[a-zA-Z]:[\\/]|^\\\\", path))


class _WinSide:
    """Windows shortcuts, from native Windows or from WSL through interop."""

    def __init__(self, ctx: _Ctx, exe: str) -> None:
        self.ctx = ctx
        self.exe = exe

    def to_local(self, win_path: str) -> Path:
        """A Windows path as a path we can open here (``wslpath -u`` under WSL)."""
        if self.ctx.plat != "wsl":
            return Path(win_path)
        out = self.ctx.call(["wslpath", "-u", win_path])
        if out is None or out.returncode != 0 or not out.stdout.strip():
            raise ShortcutError(f"wslpath could not convert {win_path}")
        return Path(out.stdout.strip())

    def to_windows(self, local: Path) -> str:
        """A local path as Windows sees it (``wslpath -w`` under WSL)."""
        if self.ctx.plat == "windows":
            return str(local)
        if self.ctx.plat != "wsl":
            raise ShortcutError(f"can't read a Windows shortcut on {self.ctx.plat}")
        out = self.ctx.call(["wslpath", "-w", str(local)])
        if out is None or out.returncode != 0 or not out.stdout.strip():
            raise ShortcutError(f"wslpath could not convert {local}")
        return out.stdout.strip()

    def read(self, win_path_of: Callable[[Path], str]) -> Callable[[Path], _Pointer | None]:
        def read(local: Path) -> _Pointer | None:
            data = _ps_json(self.ctx, self.exe, lnk_read_script(win_path_of(local)))
            return self._pointer(data.get("target", ""), data.get("args", ""), data)

        return read

    def _pointer(self, target: str, args: str, data: Mapping[str, str]) -> _Pointer | None:
        if not target and not args:
            return None
        tokens = _win_tokens(args)
        if PureWindowsPath(target).name.lower() == "wsl.exe":
            if not any(t.rsplit("/", 1)[-1] == "run.py" for t in tokens):
                return _Pointer(display="", local=None, instagraph=False)
            cd = _flag_value(tokens, ("--cd",))
            distro = _flag_value(tokens, ("-d", "--distribution"))
            if not cd:
                return None
            here = self.ctx.env.get("WSL_DISTRO_NAME")
            if self.ctx.plat == "wsl" and (distro is None or distro == here):
                return _Pointer(display=cd, local=Path(cd), instagraph=True)
            return _Pointer(
                display=f"{cd} in WSL {distro or ''}".strip(), local=None, instagraph=True
            )
        run_py = next((t for t in tokens if re.split(r"[\\/]", t)[-1].lower() == "run.py"), None)
        if run_py is None:
            return _Pointer(display="", local=None, instagraph=False)
        folder = re.sub(r"[\\/]?[^\\/]*$", "", run_py)  # drop "run.py"
        if not (_looks_windows(folder) or folder.startswith("/")):  # relative
            workdir = data.get("workdir", "")
            if not workdir:
                return None
            folder = str(PureWindowsPath(workdir) / folder) if folder else workdir
        if self.ctx.plat == "wsl" and _looks_windows(folder):
            try:
                return _Pointer(display=folder, local=self.to_local(folder), instagraph=True)
            except ShortcutError:
                return _Pointer(display=folder, local=None, instagraph=True)
        return _Pointer(display=folder, local=Path(folder), instagraph=True)

    def place(
        self,
        label: str,
        win_dir: str,
        local_dir: Path,
        target: str,
        arguments: str,
        workdir: str,
        icon: str,
    ) -> Path | None:
        def win_path_of(local: Path) -> str:
            if self.ctx.plat == "windows":
                return str(local)  # already a Windows path
            return str(PureWindowsPath(win_dir) / local.name)

        def write(local: Path, name: str) -> None:
            body = lnk_write_script(win_path_of(local), target, arguments, workdir, icon)
            _powershell(self.ctx, self.exe, body)

        def names(name: str) -> Path:
            return local_dir / f"{name}.lnk"

        return _place(self.ctx, label, names, self.read(win_path_of), write)


def _flag_value(tokens: list[str], flags: tuple[str, ...]) -> str | None:
    for i, tok in enumerate(tokens[:-1]):
        if tok == "--":
            break
        if tok in flags:
            return tokens[i + 1]
    return None


def _create_windows(ctx: _Ctx) -> None:
    exe = _find_powershell(ctx)
    if exe is None:
        ctx.fail(_DESKTOP, ShortcutError("PowerShell was not found"))
        return
    try:
        folders = _query_folders(ctx, exe)
    except ShortcutError as exc:
        ctx.fail(_DESKTOP, exc)
        return
    side = _WinSide(ctx, exe)
    repo = str(ctx.repo)
    common = {
        "target": str(ctx.python),
        "arguments": f'"{ctx.repo / "run.py"}"',
        "workdir": repo,
        "icon": str(ctx.repo / "assets" / "icon.ico"),
    }
    for label, key in ((_DESKTOP, "desktop"), (_START_MENU, "programs")):
        win_dir = folders.get(key, "")
        if not win_dir:
            ctx.result.messages.append(f"{label}: skipped (Windows reported no such folder)")
            continue
        written = side.place(label, win_dir, Path(win_dir), **common)
        if written is not None:
            ctx.result.created.append(written)


def wsl_icon_name(repo: Path) -> str:
    """This copy's icon file in ``%LOCALAPPDATA%\\Instagraph`` (one per copy, see WSL)."""
    return f"icon-{_hash8(repo)}.ico"


def wsl_arguments(distro: str | None, repo: Path, python: Path) -> str:
    """Arguments for ``wsl.exe`` that start this copy in its WSL distro."""
    parts = [f"-d {_win_quote(distro)}"] if distro else []
    parts.append(f'--cd "{repo}" -- "{python}" "{repo / "run.py"}"')
    return " ".join(parts)


def _win_quote(value: str) -> str:
    return f'"{value}"' if re.search(r"\s", value) else value


#: Characters we don't put into a WSL shortcut's command line: wsl.exe hands it to the
#: Linux shell (``$``, backquote, ``"``, ``\``) and Windows may expand ``%VAR%``.
_WSL_UNSAFE = re.compile(r'["$`\\%\n\r]')


def _create_wsl(ctx: _Ctx) -> bool:
    """Windows shortcuts that start Instagraph in WSL. False: fall back to Linux ones."""
    for p in (ctx.repo, ctx.python):
        if _WSL_UNSAFE.search(str(p)):
            ui.warn(
                f"The path {p} contains characters a Windows shortcut can't pass to WSL;"
                " creating a Linux shortcut instead."
            )
            return False
    exe = _find_powershell(ctx)
    if exe is None or not ctx.which("wslpath"):
        ui.warn(
            "Windows programs can't be started from this WSL (interop is off);"
            " creating a Linux shortcut instead."
        )
        return False
    side = _WinSide(ctx, exe)
    try:
        folders = _query_folders(ctx, exe)
        if not folders.get("localappdata"):
            raise ShortcutError("Windows reported no LocalAppData folder")
        # One icon file per copy: uninstalling one copy must not break another's icon.
        icon_name = wsl_icon_name(ctx.repo)
        icon_win = str(PureWindowsPath(folders["localappdata"]) / APP_NAME / icon_name)
        icon_local = side.to_local(folders["localappdata"]) / APP_NAME / icon_name
        icon_local.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ctx.repo / "assets" / "icon.ico", icon_local)
        locals_ = {
            key: side.to_local(folders[key]) for key in ("desktop", "programs") if folders.get(key)
        }
    except (ShortcutError, OSError) as exc:
        ui.warn(f"Could not reach Windows from WSL ({exc}); creating a Linux shortcut instead.")
        return False

    distro = ctx.env.get("WSL_DISTRO_NAME") or None
    if distro is None:
        ui.warn("WSL_DISTRO_NAME is not set; the icon will start the default WSL distro.")
    root = folders.get("systemroot") or r"C:\Windows"
    common = {
        "target": str(PureWindowsPath(root) / "System32" / "wsl.exe"),
        "arguments": wsl_arguments(distro, ctx.repo, ctx.python),
        "workdir": str(PureWindowsPath(folders.get("localappdata") or root)),
        "icon": icon_win,
    }
    lnks: list[Path] = []
    for label, key in ((_WIN_DESKTOP, "desktop"), (_WIN_START_MENU, "programs")):
        if key not in locals_:
            ctx.result.messages.append(f"{label}: skipped (Windows reported no such folder)")
            continue
        written = side.place(label, folders[key], locals_[key], **common)
        if written is not None:
            lnks.append(written)
    if not lnks:
        # The record must never hold just the icon copy: clean it up, use Linux instead.
        try:
            _remove_icon_copy(icon_local)
        except OSError as exc:
            ui.detail(f"Could not delete {icon_local}: {exc}")
        ui.warn("No Windows shortcut could be created; creating a Linux shortcut instead.")
        return False
    ctx.result.created.extend([icon_local, *lnks])
    return True


def _remove_icon_copy(icon: Path) -> None:
    """Delete this copy's icon file, and its "Instagraph" folder if that is now empty."""
    icon.unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        icon.parent.rmdir()  # only succeeds when empty


# --- Uninstall -----------------------------------------------------------------------

_MNT_DRIVE = re.compile(r"^/mnt/[a-zA-Z](/|$)")


def _parts(raw: str) -> list[str]:
    """Path components, splitting on both separators (record paths may be from any OS)."""
    return [part for part in re.split(r"[\\/]", raw) if part]


def _made_elsewhere(raw: str, plat: Platform) -> bool:
    """True if ``raw`` can't be a path on this system (written by another OS or by WSL)."""
    if plat == "windows":
        return raw.startswith("/") and not raw.startswith("//")
    if _looks_windows(raw):
        return True
    return plat != "wsl" and bool(_MNT_DRIVE.match(raw))


class _Remover:
    """Checks one install-record entry at a time and removes it if it is still ours."""

    def __init__(self, ctx: _Ctx, result: RemoveResult) -> None:
        self.ctx = ctx
        self.result = result
        self._side: _WinSide | None = None
        h = ctx.hash8()
        folders = {_safe_name(ctx.repo.name, ctx), ctx.repo.name.replace("/", "_")}
        stems = {APP_NAME} | {f"{APP_NAME} ({f})" for f in folders}
        stems |= {f"{APP_NAME} ({f} {h})" for f in folders}
        names = {f"{stem}{suffix}" for stem in stems for suffix in (".lnk", ".command")}
        names |= {LINUX_FILE, f"instagraph-{h}.desktop"}
        #: File names this copy gives its shortcuts (case-insensitive: Windows, macOS).
        self.own_names = {n.casefold() for n in names}
        self.icon_name = wsl_icon_name(ctx.repo).casefold()

    def _say(self, line: str, *, failed: str | None = None) -> None:
        self.result.messages.append(line)
        if failed is not None:
            self.result.failed.append(failed)

    def remove(self, raw: str) -> None:
        parts = _parts(raw)
        name = parts[-1].casefold() if parts else ""
        is_icon = name == self.icon_name and len(parts) >= 2 and parts[-2] == APP_NAME
        if not (is_icon or name.endswith(SHORTCUT_SUFFIXES)):
            self._say(f"Skipped {raw} (not an Instagraph shortcut)")
            return
        if _made_elsewhere(raw, self.ctx.plat):
            self._say(f"Not removed: {raw} (made on another system; remove it there)", failed=raw)
            return
        pure = PureWindowsPath(raw) if self.ctx.plat == "windows" else PurePosixPath(raw)
        if not pure.is_absolute():
            self._say(f"Skipped {raw} (not an Instagraph shortcut)")
            return
        path = Path(raw)
        if not (path.exists() or path.is_symlink()):
            self._say(f"Shortcut already gone: {path}")
            return
        if is_icon:
            self._delete(path, icon=True)
            return
        verdict = _judge(path, self._reader(path), self.ctx)
        if verdict.kind == "same":
            self._delete(path)
        elif verdict.kind == "other":
            self._say(f"Kept {path} (it now belongs to another Instagraph copy: {verdict.where})")
        elif verdict.kind == "foreign":
            self._say(f"Skipped {path} (not an Instagraph shortcut)")
        elif name in self.own_names:  # broken, unreadable or for a deleted copy
            self._delete(path)
        else:
            self._say(f"Kept {path} (it doesn't start this copy and isn't named like its icon)")

    def _delete(self, path: Path, *, icon: bool = False) -> None:
        try:
            if icon:
                _remove_icon_copy(path)
            else:
                path.unlink()
        except FileNotFoundError:
            self._say(f"Shortcut already gone: {path}")
        except OSError as exc:
            msg = f"Could not remove {path}: {exc.strerror or exc}"
            ui.warn(msg)
            self._say(msg, failed=str(path))
        else:
            self._say(f"Removed {path}")

    def _reader(self, path: Path) -> Callable[[Path], _Pointer | None]:
        suffix = path.suffix.lower()
        if suffix == ".desktop":
            return _read_desktop_entry
        if suffix == ".command":
            return _read_command
        return self._read_lnk

    def _read_lnk(self, path: Path) -> _Pointer | None:
        if self._side is None:
            exe = _find_powershell(self.ctx) if self.ctx.plat in ("windows", "wsl") else None
            if exe is None:
                raise ShortcutError("PowerShell is not available")
            self._side = _WinSide(self.ctx, exe)
        return self._side.read(self._side.to_windows)(path)
