"""Decide whether the frontend needs npm ci / a build, and run them. Owner: node agent.

See docs/plans/cross-platform-installer.md, "install.py flow" step 4 and case B.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from installer import common, ui
from installer.node import NodeTool, format_version

#: Files in frontend/ that feed into the build, besides src/ and public/ (same as ./launch).
_INPUT_FILES = (
    "index.html",
    "package.json",
    "package-lock.json",
    "vite.config.ts",
    "tailwind.config.js",
    "postcss.config.js",
)
_OS_NAMES = {"win32": "Windows", "darwin": "macOS", "linux": "Linux"}


@dataclass(frozen=True)
class FrontendState:
    needs_install: bool  # run npm ci (node_modules missing, outdated, or from another OS)
    needs_build: bool  # run npm run build
    reason: str  # human-readable, e.g. "src/app/Layout.tsx changed since the last build"


def _node_platform() -> str:
    """Node's name for this OS (``process.platform``): win32, darwin or linux."""
    if common.IS_WINDOWS:
        return "win32"
    if common.IS_MACOS:
        return "darwin"
    return "linux"


def _mtime_ns(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def frontend_inputs(frontend_dir: Path) -> Iterator[Path]:
    """Every file that feeds into the build (the same set as ./launch)."""
    for sub in ("src", "public"):
        for root, dirs, files in os.walk(frontend_dir / sub):
            dirs.sort()
            for name in sorted(files):
                yield Path(root) / name
    for name in _INPUT_FILES:
        path = frontend_dir / name
        if path.exists():
            yield path
    yield from sorted(frontend_dir.glob("tsconfig*.json"))


def _install_reason(frontend_dir: Path, plat: str, arch: str) -> str | None:
    """Why ``npm ci`` is needed, or None if node_modules looks fine for this computer."""
    node_modules = frontend_dir / "node_modules"
    if not node_modules.is_dir():
        return "node_modules is missing"
    lock = _mtime_ns(frontend_dir / "package-lock.json")
    installed_lock = _mtime_ns(node_modules / ".package-lock.json")
    # Like bash's -nt: true if the lock exists and the installed copy is missing or older.
    if lock is not None and (installed_lock is None or lock > installed_lock):
        return "package-lock.json changed since the last npm install"
    # Vite 8 builds with rolldown, which ships one native binding per OS and CPU. If a
    # future Vite drops rolldown, there is nothing OS-specific to check here.
    if not (node_modules / "rolldown").is_dir():
        return None
    rolldown = node_modules / "@rolldown"
    if not rolldown.is_dir():
        return "node_modules is incomplete (no rolldown binding)"
    pattern = f"binding-{plat}-{arch}*" if arch in ("x64", "arm64") else f"binding-{plat}-*"
    if any(p.is_dir() for p in rolldown.glob(pattern)):
        return None
    others = sorted(
        {
            _OS_NAMES.get(p.name.split("-")[1], p.name.split("-")[1])
            for p in rolldown.glob("binding-*-*")
            if p.is_dir()
        }
    )
    if others == [_OS_NAMES.get(plat, plat)]:
        return "node_modules was installed for another processor type"
    if others:
        return f"node_modules was installed on another system ({', '.join(others)})"
    return "node_modules has no rolldown binding for this system"


def frontend_state(
    *,
    rebuild: bool = False,
    frontend_dir: Path | None = None,
    plat: str | None = None,
    arch: str | None = None,
) -> FrontendState:
    """Inspect frontend/ without running anything."""
    frontend_dir = common.FRONTEND_DIR if frontend_dir is None else frontend_dir
    plat = _node_platform() if plat is None else plat
    arch = common.cpu_arch() if arch is None else arch

    install_reason = _install_reason(frontend_dir, plat, arch)
    if install_reason is not None:
        return FrontendState(needs_install=True, needs_build=True, reason=install_reason)
    if rebuild:
        return FrontendState(needs_install=False, needs_build=True, reason="rebuild requested")
    built = _mtime_ns(frontend_dir / "dist" / "index.html")
    if built is None:
        return FrontendState(needs_install=False, needs_build=True, reason="no build found")
    newest: tuple[int, Path] | None = None
    for path in frontend_inputs(frontend_dir):
        mtime = _mtime_ns(path)
        if mtime is not None and mtime > built and (newest is None or mtime > newest[0]):
            newest = (mtime, path)
    if newest is not None:
        name = newest[1].relative_to(frontend_dir).as_posix()
        return FrontendState(
            needs_install=False, needs_build=True, reason=f"{name} changed since the last build"
        )
    return FrontendState(needs_install=False, needs_build=False, reason="up to date")


def build_frontend(
    node: NodeTool, state: FrontendState, *, frontend_dir: Path | None = None
) -> str:
    """Run npm ci (if state.needs_install) and npm run build. Returns a one-line summary."""
    frontend_dir = common.FRONTEND_DIR if frontend_dir is None else frontend_dir
    if not state.needs_install and not state.needs_build:
        return "Frontend: up to date"
    env = common.env_with_path(node.bin_dir)
    kind = "private" if node.private else "system"
    label = f"Node {format_version(node.version)} ({kind})"

    if state.needs_install:
        ui.info(f"Installing frontend packages with npm ci ({state.reason})…")
        try:
            common.run([node.npm, "ci", "--no-audit", "--no-fund"], cwd=frontend_dir, env=env)
        except common.CommandError as exc:
            raise common.CommandError(
                "Installing the frontend packages (npm ci) failed.",
                returncode=exc.returncode,
                output=exc.output,
                hint="The output above shows why. npm downloads packages from "
                "registry.npmjs.org, so check your internet connection (and HTTPS_PROXY). "
                "If another program uses frontend/node_modules, close it. Then run the "
                "installer again.",
            ) from exc

    ui.info(f"Building the frontend with {label}…")
    try:
        common.run([node.npm, "run", "build"], cwd=frontend_dir, env=env)
    except common.CommandError as exc:
        raise common.CommandError(
            "Building the frontend (npm run build) failed.",
            returncode=exc.returncode,
            output=exc.output,
            hint="The output above shows why. If frontend/node_modules looks broken, delete "
            "that folder and run the installer again; it reinstalls the packages.",
        ) from exc
    verb = "installed packages and built" if state.needs_install else "built"
    return f"Frontend: {verb} with {label}, because {state.reason}"
