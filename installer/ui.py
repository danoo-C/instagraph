"""Console output and questions for ``install.py`` and ``run.py``.

Standard library only. Call :func:`configure` once at startup.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
from typing import TextIO

_assume_yes = False
_verbose = False
_color = False


def configure(*, assume_yes: bool = False, verbose: bool = False) -> None:
    """Set global options. ``assume_yes`` answers every question with its default."""
    global _assume_yes, _verbose, _color
    _assume_yes = assume_yes
    _verbose = verbose
    # On Windows, output redirected to a file or pipe uses the ANSI code page with strict
    # errors, so printing a character it lacks (Vite's "✓", a name in a path) would crash.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            with contextlib.suppress(Exception):
                stream.reconfigure(errors="replace")
    _color = _supports_color(sys.stdout)


def assume_yes() -> bool:
    return _assume_yes


def verbose() -> bool:
    return _verbose


def _supports_color(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR") or not hasattr(stream, "isatty") or not stream.isatty():
        return False
    if os.name == "nt":
        return _enable_windows_vt()
    return True


def _enable_windows_vt() -> bool:
    """Switch on ANSI escape codes in a Windows 10+ console. False if that's not possible."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        enable_virtual_terminal_processing = 0x0004
        return bool(
            kernel32.SetConsoleMode(handle, mode.value | enable_virtual_terminal_processing)
        )
    except (AttributeError, OSError):
        return False


def _paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _color else text


def step(message: str) -> None:
    """A main step: ``==> message``."""
    print(f"{_paint('1;36', '==>')} {message}", flush=True)


def info(message: str) -> None:
    """A line under a step."""
    print(f"    {message}", flush=True)


def detail(message: str) -> None:
    """Only shown with ``--verbose``."""
    if _verbose:
        print(f"    {_paint('2', message)}", flush=True)


def warn(message: str) -> None:
    print(f"{_paint('1;33', '!!')}  {message}", file=sys.stderr, flush=True)


def error(message: str, hint: str | None = None) -> None:
    print(f"{_paint('1;31', 'error:')} {message}", file=sys.stderr, flush=True)
    if hint:
        for line in hint.splitlines():
            print(f"       {line}", file=sys.stderr, flush=True)


def interactive() -> bool:
    """True when we can ask questions (stdin is a terminal and ``--yes`` wasn't given)."""
    return not _assume_yes and sys.stdin is not None and sys.stdin.isatty()


def ask_yes_no(question: str, *, default: bool, yes_value: bool | None = None) -> bool:
    """Ask a yes/no question.

    With ``--yes`` (or no terminal) it returns ``yes_value`` if given, else ``default``,
    and prints the question with the answer so the log shows what was decided.
    """
    hint = "[Y/n]" if default else "[y/N]"
    if not interactive():
        answer = default if yes_value is None else yes_value
        info(f"{question} {hint} {'yes' if answer else 'no'} (automatic)")
        return answer
    while True:
        try:
            reply = input(f"{_paint('1;36', '?')}   {question} {hint} ").strip().lower()
        except EOFError:
            return default
        if not reply:
            return default
        if reply in ("y", "yes"):
            return True
        if reply in ("n", "no"):
            return False
        info("Please answer y or n.")


def ask_choice(
    question: str, choices: dict[str, str], *, default: str, yes_value: str | None = None
) -> str:
    """Ask for one of several single-letter choices, e.g. ``{"k": "keep both", "r": "replace"}``.

    Returns the chosen key. Without a terminal or with ``--yes`` it returns ``yes_value``
    (or ``default``).
    """
    labels = " / ".join(
        f"[{key.upper()}] {label}" if key == default else f"[{key}] {label}"
        for key, label in choices.items()
    )
    if not interactive():
        answer = default if yes_value is None else yes_value
        info(f"{question} {labels}: {choices[answer]} (automatic)")
        return answer
    while True:
        try:
            reply = input(f"{_paint('1;36', '?')}   {question} {labels} ").strip().lower()
        except EOFError:
            return default
        if not reply:
            return default
        if reply in choices:
            return reply
        info(f"Please answer one of: {', '.join(choices)}.")


def wait_for_enter(prompt: str) -> bool:
    """Wait for Enter. Returns False on Ctrl+C / Ctrl+D (the user wants to cancel)."""
    try:
        input(f"{_paint('1;36', '?')}   {prompt} ")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return True


def pause_before_exit() -> None:
    """Keep a console window open (started from a desktop icon) so the message can be read."""
    if sys.stdin is not None and sys.stdin.isatty():
        wait_for_enter("Press Enter to close this window.")
