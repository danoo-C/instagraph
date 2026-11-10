"""Tests for installer.node: version checks, archive choice, download, verify, unpack."""

from __future__ import annotations

import hashlib
import http.server
import io
import json
import os
import socket
import stat
import tarfile
import threading
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from installer import common, node, ui

POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="fake node is a shell script")


@pytest.fixture(autouse=True)
def _quiet_ui() -> Iterator[None]:
    ui.configure(assume_yes=True)
    yield
    ui.configure()


# --- Versions ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "ok"),
    [
        ((18, 20, 4), False),
        ((20, 0, 0), False),
        ((20, 18, 3), False),
        ((20, 19, 0), True),
        ((20, 30, 1), True),
        ((21, 0, 0), False),
        ((21, 99, 0), False),
        ((22, 11, 9), False),
        ((22, 12, 0), True),
        ((22, 20, 0), True),
        ((23, 0, 0), True),
        ((24, 11, 1), True),
        ((30, 0, 0), True),
    ],
)
def test_node_version_ok(version: tuple[int, int, int], ok: bool) -> None:
    assert node.node_version_ok(version) is ok


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("v24.11.1\n", (24, 11, 1)),
        ("20.19.0", (20, 19, 0)),
        ("  v22.12.3 ", (22, 12, 3)),
        ("garbage", None),
        ("", None),
    ],
)
def test_parse_version(text: str, expected: tuple[int, int, int] | None) -> None:
    assert node.parse_version(text) == expected


# --- Archive choice ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("os_name", "arch", "expected"),
    [
        ("windows", "x64", ("win-x64", "zip")),
        ("windows", "arm64", ("win-arm64", "zip")),
        ("macos", "arm64", ("darwin-arm64", "tar.gz")),
        ("macos", "x64", ("darwin-x64", "tar.gz")),
        ("linux", "x64", ("linux-x64", "tar.xz")),
        ("linux", "arm64", ("linux-arm64", "tar.xz")),
    ],
)
def test_archive_platform(os_name: str, arch: str, expected: tuple[str, str]) -> None:
    got = node.archive_platform(
        windows=os_name == "windows",
        macos=os_name == "macos",
        linux=os_name == "linux",
        arch=arch,
        musl=False,
        is_32bit=False,
    )
    assert got == expected
    assert node.archive_name("24.11.1", *got) == f"node-v24.11.1-{got[0]}.{got[1]}"


def test_archive_platform_uses_common_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "IS_WINDOWS", True)
    monkeypatch.setattr(common, "IS_MACOS", False)
    monkeypatch.setattr(common, "cpu_arch", lambda: "arm64")
    assert node.archive_platform(is_32bit=False) == ("win-arm64", "zip")


@pytest.mark.parametrize(
    ("kwargs", "words"),
    [
        ({"linux": True, "arch": "x64", "musl": True}, "Alpine"),
        ({"linux": True, "arch": "x64", "is_32bit": True}, "32-bit"),
        ({"windows": True, "arch": "x64", "is_32bit": True}, "32-bit"),
        ({"linux": True, "arch": "other"}, "processor"),
        ({"macos": True, "arch": "other"}, "processor"),
        ({"arch": "x64"}, "operating system"),
    ],
)
def test_archive_platform_unsupported(kwargs: dict[str, Any], words: str) -> None:
    args: dict[str, Any] = {
        "windows": False,
        "macos": False,
        "linux": False,
        "musl": False,
        "is_32bit": False,
        **kwargs,
    }
    with pytest.raises(common.InstallError) as info:
        node.archive_platform(**args)
    assert words in info.value.message
    assert info.value.hint is not None and "20.19" in info.value.hint


# --- index.json and SHASUMS ----------------------------------------------------------

INDEX = [
    {"version": "v25.1.0", "lts": False},
    {"version": "v24.11.1", "lts": "Krypton"},
    {"version": "v24.9.0", "lts": False},
    {"version": "v24.11.0", "lts": "Krypton"},
    {"version": "v2.4.0"},
    {"version": "v240.0.0"},
    {"nope": 1},
    "junk",
    {"version": "v22.21.0", "lts": "Jod"},
]


def test_pick_version_newest_of_major() -> None:
    assert node.pick_version(INDEX, 24) == "24.11.1"
    assert node.pick_version(INDEX, 22) == "22.21.0"


@pytest.mark.parametrize("index", [INDEX, [], {"version": "v24.1.0"}, None])
def test_pick_version_missing_major(index: Any) -> None:
    with pytest.raises(common.InstallError, match=r"no Node\.js 99"):
        node.pick_version(index, 99)


def test_expected_sha256() -> None:
    a, b = "a" * 64, "B" * 64
    text = f"{a}  node-v24.11.1-linux-x64.tar.xz\n{b}  node-v24.11.1-win-x64.zip\n"
    assert node.expected_sha256(text, "node-v24.11.1-linux-x64.tar.xz") == a
    assert node.expected_sha256(text, "node-v24.11.1-win-x64.zip") == b.lower()
    with pytest.raises(common.InstallError, match="no checksum"):
        node.expected_sha256(text, "node-v24.11.1-darwin-arm64.tar.gz")


def test_verify_sha256(tmp_path: Path) -> None:
    f = tmp_path / "archive.tar.xz"
    f.write_bytes(b"hello")
    node.verify_sha256(f, hashlib.sha256(b"hello").hexdigest())
    assert f.exists()
    with pytest.raises(common.InstallError, match="checksum"):
        node.verify_sha256(f, "0" * 64)
    assert not f.exists()


def test_check_url() -> None:
    node._check_url("https://nodejs.org/dist/index.json")
    node._check_url("http://127.0.0.1:1234/index.json")
    with pytest.raises(common.InstallError, match="insecure"):
        node._check_url("http://nodejs.org/dist/index.json")


# --- Safe unpack ---------------------------------------------------------------------


def _add_tar_file(tf: tarfile.TarFile, name: str, data: bytes, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    tf.addfile(info, io.BytesIO(data))


def _make_tar(path: Path, files: dict[str, tuple[bytes, int]], mode: str = "w:xz") -> None:
    with tarfile.open(path, mode) as tf:  # type: ignore[call-overload]
        for name, (data, perms) in files.items():
            _add_tar_file(tf, name, data, perms)


def _make_zip(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)


def test_unpack_tar_xz_keeps_exec_bit(tmp_path: Path) -> None:
    archive = tmp_path / "node-v1.2.3-linux-x64.tar.xz"
    _make_tar(
        archive,
        {
            "node-v1.2.3-linux-x64/bin/node": (b"#!/bin/sh\n", 0o755),
            "node-v1.2.3-linux-x64/README.md": (b"hi", 0o644),
        },
    )
    dest = tmp_path / "out"
    dest.mkdir()
    node.safe_unpack(archive, dest)
    exe = dest / "node-v1.2.3-linux-x64" / "bin" / "node"
    assert exe.read_bytes() == b"#!/bin/sh\n"
    if os.name != "nt":
        assert exe.stat().st_mode & stat.S_IXUSR


def test_unpack_tar_gz(tmp_path: Path) -> None:
    archive = tmp_path / "node-v1.2.3-darwin-arm64.tar.gz"
    _make_tar(archive, {"top/bin/node": (b"x", 0o755)}, mode="w:gz")
    dest = tmp_path / "out"
    dest.mkdir()
    node.safe_unpack(archive, dest)
    assert (dest / "top" / "bin" / "node").exists()


@pytest.mark.parametrize("evil", ["../evil", "top/../../evil", "/tmp/evil-abs"])
def test_unpack_tar_rejects_escaping_paths(tmp_path: Path, evil: str) -> None:
    archive = tmp_path / "bad.tar.xz"
    _make_tar(archive, {"top/ok": (b"ok", 0o644), evil: (b"evil", 0o644)})
    dest = tmp_path / "a" / "b"
    dest.mkdir(parents=True)
    with pytest.raises(common.InstallError, match="Unsafe path"):
        node.safe_unpack(archive, dest)
    assert list(dest.iterdir()) == []
    assert not (tmp_path / "a" / "evil").exists()
    assert not (tmp_path / "evil").exists()


def test_unpack_tar_rejects_link_outside(tmp_path: Path) -> None:
    archive = tmp_path / "bad.tar.xz"
    with tarfile.open(archive, "w:xz") as tf:
        info = tarfile.TarInfo("top/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "../../../etc/passwd"
        tf.addfile(info)
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(common.InstallError):
        node.safe_unpack(archive, dest)


def test_unpack_zip(tmp_path: Path) -> None:
    archive = tmp_path / "node-v1.2.3-win-x64.zip"
    _make_zip(archive, {"node-v1.2.3-win-x64/node.exe": b"MZ", "node-v1.2.3-win-x64/npm.cmd": b""})
    dest = tmp_path / "out"
    dest.mkdir()
    node.safe_unpack(archive, dest)
    assert (dest / "node-v1.2.3-win-x64" / "node.exe").read_bytes() == b"MZ"


@pytest.mark.parametrize(
    "evil", ["../evil", "top/../../evil", "/abs/evil", "C:/evil", "..\\evil", "top\\..\\..\\x"]
)
def test_unpack_zip_rejects_escaping_paths(tmp_path: Path, evil: str) -> None:
    archive = tmp_path / "bad.zip"
    _make_zip(archive, {"top/ok": b"ok", evil: b"evil"})
    dest = tmp_path / "a" / "b"
    dest.mkdir(parents=True)
    with pytest.raises(common.InstallError, match="Unsafe path"):
        node.safe_unpack(archive, dest)
    # Checked before anything is written.
    assert list(dest.iterdir()) == []
    assert not (tmp_path / "a" / "evil").exists()


def test_unpack_corrupt_archive(tmp_path: Path) -> None:
    archive = tmp_path / "broken.tar.xz"
    archive.write_bytes(b"not an archive")
    with pytest.raises(common.InstallError, match="Could not unpack"):
        node.safe_unpack(archive, tmp_path)


# --- Atomic replace ------------------------------------------------------------------


def test_replace_dir_new_target(tmp_path: Path) -> None:
    new = tmp_path / "staging"
    (new / "bin").mkdir(parents=True)
    target = tmp_path / "node"
    node.replace_dir(new, target)
    assert (target / "bin").is_dir()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["node"]


def test_replace_dir_replaces_old(tmp_path: Path) -> None:
    target = tmp_path / "node"
    target.mkdir()
    (target / "old.txt").write_text("old")
    new = tmp_path / "staging"
    new.mkdir()
    (new / "new.txt").write_text("new")
    node.replace_dir(new, target)
    assert sorted(p.name for p in target.iterdir()) == ["new.txt"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["node"]


def test_replace_dir_failure_restores_old(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "node"
    target.mkdir()
    (target / "old.txt").write_text("old")
    new = tmp_path / "staging"
    new.mkdir()
    real_replace = os.replace
    calls = []

    def flaky_replace(src: Any, dst: Any) -> None:
        calls.append((src, dst))
        if len(calls) == 2:  # moving the new folder in
            raise KeyboardInterrupt
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky_replace)
    with pytest.raises(KeyboardInterrupt):
        node.replace_dir(new, target)
    monkeypatch.setattr(os, "replace", real_replace)
    assert (target / "old.txt").read_text() == "old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["node", "staging"]


# --- find_private_node ---------------------------------------------------------------


def _fake_node_dir(root: Path, version: str, *, write_version: bool = True) -> Path:
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True)
    exe = bin_dir / "node"
    exe.write_text(f"#!/bin/sh\necho v{version}\n")
    exe.chmod(0o755)
    npm = bin_dir / "npm"
    npm.write_text("#!/bin/sh\necho 10.0.0\n")
    npm.chmod(0o755)
    if write_version:
        (root / "VERSION").write_text(version + "\n")
    return root


@POSIX_ONLY
def test_find_private_node_ok(tmp_path: Path) -> None:
    d = _fake_node_dir(tmp_path / "node", f"{node.NODE_MAJOR}.3.4")
    tool = node.find_private_node(node_dir=d, windows=False)
    assert tool is not None
    assert tool.version == (node.NODE_MAJOR, 3, 4)
    assert tool.private
    assert tool.node == d / "bin" / "node"
    assert tool.npm == d / "bin" / "npm"
    assert tool.bin_dir == d / "bin"


@POSIX_ONLY
def test_find_private_node_wrong_major(tmp_path: Path) -> None:
    d = _fake_node_dir(tmp_path / "node", f"{node.NODE_MAJOR - 2}.0.0")
    assert node.find_private_node(node_dir=d, windows=False) is None


@POSIX_ONLY
def test_find_private_node_without_version_file(tmp_path: Path) -> None:
    d = _fake_node_dir(tmp_path / "node", f"{node.NODE_MAJOR}.0.0", write_version=False)
    assert node.find_private_node(node_dir=d, windows=False) is None


@POSIX_ONLY
def test_find_private_node_broken_binary(tmp_path: Path) -> None:
    d = _fake_node_dir(tmp_path / "node", f"{node.NODE_MAJOR}.0.0")
    (d / "bin" / "node").write_text("#!/bin/sh\nexit 3\n")
    assert node.find_private_node(node_dir=d, windows=False) is None


def test_find_private_node_missing(tmp_path: Path) -> None:
    assert node.find_private_node(node_dir=tmp_path / "nothing") is None


def test_private_layout_windows(tmp_path: Path) -> None:
    exe, npm, bin_dir = node._private_layout(tmp_path, windows=True)
    assert (exe, npm, bin_dir) == (tmp_path / "node.exe", tmp_path / "npm.cmd", tmp_path)


# --- find_system_node ----------------------------------------------------------------


@POSIX_ONLY
@pytest.mark.parametrize(("version", "found"), [("22.12.0", True), ("18.19.1", False)])
def test_find_system_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str, found: bool
) -> None:
    d = _fake_node_dir(tmp_path / "sys", version)
    monkeypatch.setenv("PATH", str(d / "bin"))
    tool = node.find_system_node()
    if not found:
        assert tool is None
        return
    assert tool is not None
    assert tool.version == (22, 12, 0)
    assert tool.bin_dir == d / "bin"
    assert not tool.private


def test_find_system_node_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    assert node.find_system_node() is None


# --- Full download flow against a local server ---------------------------------------

FAKE_VERSION = f"{node.NODE_MAJOR}.99.1"
PLAT = ("linux-x64", "tar.xz")


class _Server:
    def __init__(self, root: Path) -> None:
        self.requests: list[str] = []
        requests = self.requests

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, directory=str(root), **kwargs)

            def do_GET(self) -> None:
                requests.append(self.path)
                super().do_GET()

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def _publish(root: Path, *, bad_sha: bool = False) -> str:
    """Lay out a fake nodejs.org/dist with one release; returns the archive name."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.json").write_text(
        json.dumps([{"version": "v99.0.0"}, {"version": f"v{FAKE_VERSION}"}])
    )
    rel = root / f"v{FAKE_VERSION}"
    rel.mkdir()
    name = node.archive_name(FAKE_VERSION, *PLAT)
    top = f"node-v{FAKE_VERSION}-{PLAT[0]}"
    _make_tar(
        rel / name,
        {
            f"{top}/bin/node": (f"#!/bin/sh\necho v{FAKE_VERSION}\n".encode(), 0o755),
            f"{top}/bin/npm": (b"#!/bin/sh\necho 11.0.0\n", 0o755),
            f"{top}/LICENSE": (b"MIT", 0o644),
        },
    )
    digest = "0" * 64 if bad_sha else hashlib.sha256((rel / name).read_bytes()).hexdigest()
    (rel / "SHASUMS256.txt").write_text(f"{'1' * 64}  other.zip\n{digest}  {name}\n")
    return name


@pytest.fixture
def dist(tmp_path: Path) -> Iterator[tuple[_Server, Path]]:
    root = tmp_path / "dist"
    root.mkdir()
    server = _Server(root)
    yield server, root
    server.close()


@pytest.fixture
def no_system_node(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(node, "find_system_node", lambda: None)
    monkeypatch.setattr(node, "_system_node_candidate", lambda: None)
    monkeypatch.setattr(node, "archive_platform", lambda: PLAT)


@POSIX_ONLY
@pytest.mark.usefixtures("no_system_node")
def test_ensure_node_downloads_and_reuses(dist: tuple[_Server, Path], tmp_path: Path) -> None:
    server, root = dist
    name = _publish(root)
    venv = tmp_path / "venv"
    node_dir = venv / "node"
    # A broken old copy and leftovers of a cancelled run get replaced / cleaned up.
    (node_dir / "bin").mkdir(parents=True)
    (venv / ".node-download-stale").mkdir()
    (venv / ".node-unpack-stale").mkdir()

    tool = node.ensure_node(node_dir=node_dir, dist_url=server.url)

    assert tool.private
    assert tool.version == node.parse_version(FAKE_VERSION)
    assert tool.node == node_dir / "bin" / "node"
    assert (node_dir / "VERSION").read_text().strip() == FAKE_VERSION
    assert (node_dir / "LICENSE").exists()
    assert sorted(p.name for p in venv.iterdir()) == ["node"]
    assert server.requests == [
        "/index.json",
        f"/v{FAKE_VERSION}/SHASUMS256.txt",
        f"/v{FAKE_VERSION}/{name}",
    ]

    # Second run: reuses the private copy, no network.
    server.requests.clear()
    again = node.ensure_node(node_dir=node_dir, dist_url=server.url)
    assert again == tool
    assert server.requests == []


@POSIX_ONLY
def test_ensure_node_force_private_skips_system(
    dist: tuple[_Server, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, root = dist
    _publish(root)
    monkeypatch.setattr(node, "archive_platform", lambda: PLAT)

    def system_node() -> node.NodeTool:
        raise AssertionError("system Node must not be used")

    monkeypatch.setattr(node, "find_system_node", system_node)
    tool = node.ensure_node(
        force_private=True, node_dir=tmp_path / "v" / "node", dist_url=server.url
    )
    assert tool.private


def test_ensure_node_prefers_system(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    system = node.NodeTool(
        node=tmp_path / "node",
        npm=tmp_path / "npm",
        bin_dir=tmp_path,
        version=(22, 12, 0),
        private=False,
    )
    monkeypatch.setattr(node, "find_system_node", lambda: system)
    assert node.ensure_node(node_dir=tmp_path / "unused") is system


@pytest.mark.usefixtures("no_system_node")
def test_ensure_node_sha_mismatch(dist: tuple[_Server, Path], tmp_path: Path) -> None:
    server, root = dist
    _publish(root, bad_sha=True)
    venv = tmp_path / "venv"
    with pytest.raises(common.InstallError, match="checksum"):
        node.ensure_node(node_dir=venv / "node", dist_url=server.url)
    assert list(venv.iterdir()) == []


@pytest.mark.usefixtures("no_system_node")
def test_ensure_node_declined(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    asked: list[str] = []

    def ask(question: str, *, default: bool, yes_value: bool | None = None) -> bool:
        asked.append(question)
        assert default is True
        return False

    monkeypatch.setattr(ui, "ask_yes_no", ask)
    with pytest.raises(common.InstallError) as info:
        node.ensure_node(node_dir=tmp_path / "node", dist_url="http://127.0.0.1:9")
    assert info.value.hint is not None and "https://nodejs.org" in info.value.hint
    assert asked and "about 30 MB" in asked[0]
    assert not (tmp_path / "node").exists()


@pytest.mark.usefixtures("no_system_node")
def test_ensure_node_network_error(tmp_path: Path) -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    # Nothing listens on that port now.
    with pytest.raises(common.InstallError) as info:
        node.ensure_node(node_dir=tmp_path / "node", dist_url=f"http://127.0.0.1:{port}")
    assert "Could not download" in info.value.message
    assert info.value.hint is not None and "nodejs.org" in info.value.hint


@pytest.mark.usefixtures("no_system_node")
def test_ensure_node_http_404(dist: tuple[_Server, Path], tmp_path: Path) -> None:
    server, _root = dist  # nothing published
    with pytest.raises(common.InstallError, match="HTTP 404"):
        node.ensure_node(node_dir=tmp_path / "node", dist_url=server.url)


def test_ensure_node_unsupported_does_not_ask(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(node, "find_system_node", lambda: None)
    monkeypatch.setattr(node, "_system_node_candidate", lambda: None)

    def unsupported() -> tuple[str, str]:
        raise node._unsupported("musl-based Linux such as Alpine")

    def ask(*_a: Any, **_k: Any) -> bool:
        raise AssertionError("must not ask")

    monkeypatch.setattr(node, "archive_platform", unsupported)
    monkeypatch.setattr(ui, "ask_yes_no", ask)
    with pytest.raises(common.InstallError, match="Alpine"):
        node.ensure_node(node_dir=tmp_path / "node")


@POSIX_ONLY
@pytest.mark.usefixtures("no_system_node")
def test_download_cut_off(tmp_path: Path) -> None:
    """A response shorter than its Content-Length is an error, and nothing is left behind."""
    archive = tmp_path / "a.tar.xz"
    _make_tar(archive, {"top/bin/node": (b"x", 0o755)})
    data = archive.read_bytes()
    name = node.archive_name(FAKE_VERSION, *PLAT)
    shasums = f"{hashlib.sha256(data).hexdigest()}  {name}\n".encode()
    index = json.dumps([{"version": f"v{FAKE_VERSION}"}]).encode()

    class Resp(io.BytesIO):
        def __init__(self, body: bytes, length: int) -> None:
            super().__init__(body)
            self.headers = {"Content-Length": str(length)}

    archive_requests: list[str] = []

    def opener(url: str) -> Resp:
        if url.endswith("index.json"):
            return Resp(index, len(index))
        if url.endswith("SHASUMS256.txt"):
            return Resp(shasums, len(shasums))
        archive_requests.append(url)
        return Resp(data[:10], len(data))

    venv = tmp_path / "venv"
    with pytest.raises(common.InstallError, match="cut off"):
        node.ensure_node(node_dir=venv / "node", dist_url="https://example.invalid", opener=opener)
    assert list(venv.iterdir()) == []
    assert len(archive_requests) == 2  # retried once
