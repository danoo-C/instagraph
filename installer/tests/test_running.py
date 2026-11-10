"""Tests for installer.running (case A: Instagraph is running during an update)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from installer import common, running, ui
from installer.common import InstallError


@pytest.fixture(autouse=True)
def _reset_ui() -> Iterator[None]:
    ui.configure()
    yield
    ui.configure()


class World:
    """Fake processes and ports."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.alive: set[int] = set()
        self.ports: dict[int, common.ProbeResult] = {}
        self.probed: list[int] = []
        monkeypatch.setattr(common, "pid_alive", lambda pid: pid in self.alive)
        monkeypatch.setattr(common, "probe_instagraph", self.probe)

    def probe(self, port: int, *a: object, **kw: object) -> common.ProbeResult:
        self.probed.append(port)
        return self.ports.get(port, "none")


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    return World(monkeypatch)


def write_run(path: Path, pid: int = 4242, port: int = 9000) -> Path:
    path.write_text(json.dumps({"pid": pid, "port": port, "started": "x"}), encoding="utf-8")
    return path


def test_run_file_alive_and_answering(world: World, tmp_path: Path) -> None:
    rf = write_run(tmp_path / "run.json")
    world.alive.add(4242)
    world.ports[9000] = "instagraph"
    server = running.find_running(rf, [8000])
    assert server == running.RunningServer(9000, this_copy=True)
    assert server.message() == "Instagraph is running (port 9000)."
    assert rf.exists()


@pytest.mark.parametrize("case", ["dead", "other-port-owner"])
def test_stale_run_file_is_deleted(world: World, tmp_path: Path, case: str) -> None:
    rf = write_run(tmp_path / "run.json")
    if case == "other-port-owner":
        world.alive.add(4242)
        world.ports[9000] = "other"
    assert running.find_running(rf, [8000]) is None
    assert not rf.exists()
    assert 8000 in world.probed


def test_invalid_run_file_is_deleted(world: World, tmp_path: Path) -> None:
    rf = tmp_path / "run.json"
    rf.write_text("{not json")
    assert running.find_running(rf, [8000]) is None
    assert not rf.exists()


def test_port_probe_without_run_file(world: World, tmp_path: Path) -> None:
    world.ports[8000] = "instagraph"
    server = running.find_running(tmp_path / "run.json", [8000, 8000])
    assert server == running.RunningServer(8000, this_copy=False)
    assert server.message() == "An Instagraph server is running on port 8000."
    assert world.probed == [8000]  # the same port isn't probed twice


def test_configured_port_probed(world: World, tmp_path: Path) -> None:
    world.ports[8123] = "instagraph"
    world.ports[8000] = "other"  # something else on 8000 doesn't count
    server = running.find_running(tmp_path / "run.json", [8000, 8123])
    assert server is not None and server.port == 8123


def test_yes_mode_stops(world: World, tmp_path: Path) -> None:
    ui.configure(assume_yes=True)
    world.ports[8000] = "instagraph"
    with pytest.raises(InstallError) as exc:
        running.ensure_not_running(tmp_path / "run.json", [8000], wait=lambda _: True)
    assert "An Instagraph server is running on port 8000" in exc.value.message
    assert "Close" in (exc.value.hint or "")


def test_no_terminal_stops(world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui, "interactive", lambda: False)
    world.ports[8000] = "instagraph"
    with pytest.raises(InstallError):
        running.ensure_not_running(tmp_path / "run.json", [8000])


def test_interactive_waits_until_closed(
    world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ui, "interactive", lambda: True)
    world.ports[8000] = "instagraph"
    prompts: list[str] = []

    def wait(prompt: str) -> bool:
        prompts.append(prompt)
        if len(prompts) == 2:  # the user closes the server after the second prompt
            world.ports[8000] = "none"
        return True

    running.ensure_not_running(tmp_path / "run.json", [8000], wait=wait)
    assert len(prompts) == 2
    assert "Ctrl+C" in prompts[0]


def test_interactive_cancel(world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui, "interactive", lambda: True)
    world.ports[8000] = "instagraph"
    with pytest.raises(running.Cancelled):
        running.ensure_not_running(tmp_path / "run.json", [8000], wait=lambda _: False)


def test_nothing_running(world: World, tmp_path: Path) -> None:
    running.ensure_not_running(tmp_path / "run.json", [8000], wait=lambda _: False)
