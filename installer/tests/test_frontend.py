"""Tests for installer.frontend: staleness check (like ./launch) and the npm commands."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from installer import common, frontend, ui
from installer.node import NodeTool

T0 = 1_700_000_000  # inputs
T_INSTALL = T0 + 100  # node_modules/.package-lock.json
T_BUILD = T0 + 200  # dist/index.html


@pytest.fixture(autouse=True)
def _quiet_ui() -> Iterator[None]:
    ui.configure(assume_yes=True)
    yield
    ui.configure()


def _touch(path: Path, mtime: int, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, (mtime, mtime))
    return path


def _set_mtime(path: Path, mtime: int) -> None:
    os.utime(path, (mtime, mtime))


@pytest.fixture
def fe(tmp_path: Path) -> Path:
    """A fake, freshly built frontend/ for linux-x64."""
    root = tmp_path / "frontend"
    for rel in (
        "src/main.tsx",
        "src/app/Layout.tsx",
        "public/favicon.svg",
        "index.html",
        "package.json",
        "package-lock.json",
        "vite.config.ts",
        "tailwind.config.js",
        "postcss.config.js",
        "tsconfig.json",
        "tsconfig.node.json",
        "README.md",  # not an input
    ):
        _touch(root / rel, T0)
    _touch(root / "node_modules" / ".package-lock.json", T_INSTALL)
    (root / "node_modules" / "@rolldown" / "binding-linux-x64-gnu").mkdir(parents=True)
    (root / "node_modules" / "@rolldown" / "pluginutils").mkdir()
    (root / "node_modules" / "rolldown").mkdir()
    _touch(root / "dist" / "index.html", T_BUILD)
    return root


def _state(fe: Path, **kwargs: Any) -> frontend.FrontendState:
    args: dict[str, Any] = {"frontend_dir": fe, "plat": "linux", "arch": "x64", **kwargs}
    return frontend.frontend_state(**args)


# --- frontend_state ------------------------------------------------------------------


def test_up_to_date(fe: Path) -> None:
    assert _state(fe) == frontend.FrontendState(False, False, "up to date")


def test_rebuild_requested(fe: Path) -> None:
    assert _state(fe, rebuild=True) == frontend.FrontendState(False, True, "rebuild requested")


def test_no_build(fe: Path) -> None:
    (fe / "dist" / "index.html").unlink()
    assert _state(fe) == frontend.FrontendState(False, True, "no build found")


@pytest.mark.parametrize(
    "rel",
    [
        "src/app/Layout.tsx",
        "public/favicon.svg",
        "index.html",
        "package.json",
        "vite.config.ts",
        "tailwind.config.js",
        "postcss.config.js",
        "tsconfig.node.json",
    ],
)
def test_input_newer_than_build(fe: Path, rel: str) -> None:
    _set_mtime(fe / rel, T_BUILD + 5)
    state = _state(fe)
    assert state == frontend.FrontendState(False, True, f"{rel} changed since the last build")


def test_new_file_in_src(fe: Path) -> None:
    _touch(fe / "src" / "pages" / "New.tsx", T_BUILD + 1)
    assert _state(fe).reason == "src/pages/New.tsx changed since the last build"


def test_reports_newest_input(fe: Path) -> None:
    _set_mtime(fe / "src" / "main.tsx", T_BUILD + 1)
    _set_mtime(fe / "src" / "app" / "Layout.tsx", T_BUILD + 9)
    assert _state(fe).reason == "src/app/Layout.tsx changed since the last build"


def test_non_inputs_ignored(fe: Path) -> None:
    _set_mtime(fe / "README.md", T_BUILD + 5)
    _touch(fe / "e2e" / "test.spec.ts", T_BUILD + 5)
    _touch(fe / "node_modules" / "x" / "index.js", T_BUILD + 5)
    assert not _state(fe).needs_build


def test_equal_mtime_is_not_newer(fe: Path) -> None:
    _set_mtime(fe / "src" / "main.tsx", T_BUILD)
    assert not _state(fe).needs_build


def test_node_modules_missing(tmp_path: Path, fe: Path) -> None:
    import shutil

    shutil.rmtree(fe / "node_modules")
    state = _state(fe)
    assert state == frontend.FrontendState(True, True, "node_modules is missing")


def test_lock_newer_than_installed_lock(fe: Path) -> None:
    _set_mtime(fe / "package-lock.json", T_INSTALL + 1)
    state = _state(fe)
    assert state.needs_install and state.needs_build
    assert "package-lock.json" in state.reason


def test_installed_lock_missing(fe: Path) -> None:
    (fe / "node_modules" / ".package-lock.json").unlink()
    assert _state(fe).needs_install


def test_install_beats_up_to_date_build(fe: Path) -> None:
    """Even with a fresh dist/, node_modules from another OS means npm ci + build."""
    (fe / "node_modules" / "@rolldown" / "binding-linux-x64-gnu").rmdir()
    (fe / "node_modules" / "@rolldown" / "binding-win32-x64-msvc").mkdir()
    state = _state(fe)
    assert state == frontend.FrontendState(
        True, True, "node_modules was installed on another system (Windows)"
    )


def test_binding_from_macos(fe: Path) -> None:
    (fe / "node_modules" / "@rolldown" / "binding-linux-x64-gnu").rmdir()
    (fe / "node_modules" / "@rolldown" / "binding-darwin-arm64").mkdir()
    assert _state(fe).reason == "node_modules was installed on another system (macOS)"


def test_binding_for_other_cpu(fe: Path) -> None:
    assert (
        _state(fe, arch="arm64").reason == "node_modules was installed for another processor type"
    )


def test_windows_sees_linux_node_modules(fe: Path) -> None:
    state = _state(fe, plat="win32", arch="x64")
    assert state.needs_install
    assert state.reason == "node_modules was installed on another system (Linux)"


def test_windows_binding_ok(fe: Path) -> None:
    (fe / "node_modules" / "@rolldown" / "binding-win32-arm64-msvc").mkdir()
    assert not _state(fe, plat="win32", arch="arm64").needs_install


def test_musl_binding_counts(fe: Path) -> None:
    (fe / "node_modules" / "@rolldown" / "binding-linux-x64-gnu").rmdir()
    (fe / "node_modules" / "@rolldown" / "binding-linux-x64-musl").mkdir()
    assert not _state(fe).needs_install


def test_no_rolldown_folder(fe: Path) -> None:
    import shutil

    shutil.rmtree(fe / "node_modules" / "@rolldown")
    state = _state(fe)
    assert state.needs_install and state.needs_build
    assert "rolldown" in state.reason


def test_without_rolldown_package_nothing_to_check(fe: Path) -> None:
    # A future Vite without rolldown: node_modules must not look broken forever.
    import shutil

    shutil.rmtree(fe / "node_modules" / "@rolldown")
    (fe / "node_modules" / "rolldown").rmdir()
    assert _state(fe).reason == "up to date"


def test_no_binding_at_all(fe: Path) -> None:
    (fe / "node_modules" / "@rolldown" / "binding-linux-x64-gnu").rmdir()
    assert _state(fe).reason == "node_modules has no rolldown binding for this system"


def test_defaults_use_common(fe: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "IS_WINDOWS", True)
    monkeypatch.setattr(common, "IS_MACOS", False)
    monkeypatch.setattr(common, "cpu_arch", lambda: "x64")
    state = frontend.frontend_state(frontend_dir=fe)
    assert state.reason == "node_modules was installed on another system (Linux)"


def test_inputs_match_launch(fe: Path) -> None:
    names = {p.relative_to(fe).as_posix() for p in frontend.frontend_inputs(fe)}
    assert names == {
        "src/main.tsx",
        "src/app/Layout.tsx",
        "public/favicon.svg",
        "index.html",
        "package.json",
        "package-lock.json",
        "vite.config.ts",
        "tailwind.config.js",
        "postcss.config.js",
        "tsconfig.json",
        "tsconfig.node.json",
    }


# --- build_frontend ------------------------------------------------------------------


class _Recorder:
    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[tuple[list[str], Path | None, Mapping[str, str] | None]] = []
        self.fail_on = fail_on

    def __call__(
        self,
        cmd: Sequence[str | Path],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        capture: bool = False,
    ) -> Any:
        args = [str(c) for c in cmd]
        self.calls.append((args, cwd, env))
        if self.fail_on is not None and self.fail_on in args:
            raise common.CommandError("boom", returncode=2, output="err")
        return None


def _tool(tmp_path: Path, *, private: bool = True) -> NodeTool:
    bin_dir = tmp_path / "nodebin"
    return NodeTool(
        node=bin_dir / "node",
        npm=bin_dir / "npm",
        bin_dir=bin_dir,
        version=(24, 11, 1),
        private=private,
    )


def _path_of(env: Mapping[str, str] | None) -> str:
    assert env is not None
    return next(v for k, v in env.items() if k.upper() == "PATH")


def test_build_with_install(fe: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _Recorder()
    monkeypatch.setattr(common, "run", rec)
    tool = _tool(tmp_path)
    state = frontend.FrontendState(True, True, "node_modules is missing")
    summary = frontend.build_frontend(tool, state, frontend_dir=fe)
    assert [c[0][1:3] for c in rec.calls] == [["ci", "--no-audit"], ["run", "build"]]
    for args, cwd, env in rec.calls:
        assert args[0] == str(tool.npm)
        assert cwd == fe
        assert _path_of(env).split(os.pathsep)[0] == str(tool.bin_dir)
    assert summary.startswith("Frontend: installed packages and built with Node 24.11.1 (private)")
    assert "\n" not in summary


def test_build_only(fe: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _Recorder()
    monkeypatch.setattr(common, "run", rec)
    state = frontend.FrontendState(False, True, "src/a.tsx changed since the last build")
    summary = frontend.build_frontend(_tool(tmp_path, private=False), state, frontend_dir=fe)
    assert [c[0][1:] for c in rec.calls] == [["run", "build"]]
    assert summary == (
        "Frontend: built with Node 24.11.1 (system), because src/a.tsx changed since the last build"
    )


def test_nothing_to_do(fe: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _Recorder()
    monkeypatch.setattr(common, "run", rec)
    state = frontend.FrontendState(False, False, "up to date")
    assert (
        frontend.build_frontend(_tool(tmp_path), state, frontend_dir=fe) == "Frontend: up to date"
    )
    assert rec.calls == []


@pytest.mark.parametrize(("fail_on", "words"), [("ci", "npm ci"), ("build", "npm run build")])
def test_failures_have_hints(
    fe: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_on: str, words: str
) -> None:
    monkeypatch.setattr(common, "run", _Recorder(fail_on=fail_on))
    state = frontend.FrontendState(True, True, "node_modules is missing")
    with pytest.raises(common.CommandError) as info:
        frontend.build_frontend(_tool(tmp_path), state, frontend_dir=fe)
    assert words in info.value.message
    assert info.value.returncode == 2
    assert info.value.output == "err"
    assert info.value.hint


def test_default_dir_is_repo_frontend() -> None:
    """Only reads: the real frontend/ is inspected, never changed."""
    state = frontend.frontend_state()
    assert isinstance(state.reason, str) and state.reason
