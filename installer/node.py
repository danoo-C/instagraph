"""Find Node.js, or download a private copy into backend/.venv/node. Owner: node agent.

See docs/plans/cross-platform-installer.md, section "Private Node.js".
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import http.client
import json
import os
import platform
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, Any

from installer import common, ui

#: Major version downloaded when no suitable system Node exists (current LTS).
NODE_MAJOR = 24
#: Node 24 needs macOS 13.5 or newer; older macOS gets this (still LTS) major instead.
NODE_MAJOR_OLD_MACOS = 22
_NODE_MAJOR_MIN_MACOS = (13, 5)

#: Where official Node.js builds, index.json and SHASUMS256.txt live.
NODE_DIST_URL = "https://nodejs.org/dist"

_VERSION_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
_DOWNLOAD_PROMPT = (
    "Node.js is needed to build the app's interface. Download it (about 30 MB) just for "
    "Instagraph? It won't change anything else on your computer."
)
_INSTALL_NODE_HINT = (
    "You can also install Node.js 20.19 or newer yourself from https://nodejs.org "
    "and run the installer again."
)
_DISK_HINT_WINDOWS = (
    "Make sure the disk isn't full. A long folder path is another common cause: move "
    "Instagraph to a shorter folder path (for example C:\\Instagraph), or enable long paths "
    "in Windows. Then run the installer again."
)
_DISK_HINT_OTHER = (
    "Make sure the disk isn't full and that you can write to this folder, then run the "
    "installer again."
)
_TIMEOUT = 30.0  # seconds, per network read
_CHUNK = 256 * 1024
_DOWNLOAD_ATTEMPTS = 2  # connections do get cut off now and then

#: Opens a URL and returns a readable response (see :func:`_default_opener`; it honours
#: HTTPS_PROXY). Tests inject their own.
Opener = Callable[[str], IO[bytes]]


@dataclass(frozen=True)
class NodeTool:
    node: Path  # the node executable
    npm: Path  # the npm executable (npm.cmd on Windows)
    bin_dir: Path  # folder to put first on PATH when running npm
    version: tuple[int, int, int]
    private: bool  # True if it is the copy in backend/.venv/node


# --- Versions ------------------------------------------------------------------------


def parse_version(text: str) -> tuple[int, int, int] | None:
    """``(major, minor, patch)`` from ``v24.11.1`` or ``24.11.1``, else None."""
    match = _VERSION_RE.search(text.strip())
    if match is None:
        return None
    major, minor, patch = (int(g) for g in match.groups())
    return major, minor, patch


def format_version(version: tuple[int, int, int]) -> str:
    return ".".join(str(n) for n in version)


def node_version_ok(version: tuple[int, int, int]) -> bool:
    """True if Vite can use this Node: ^20.19.0 || >=22.12.0 (frontend/node_modules/vite)."""
    major, minor, _patch = version
    if major == 20:
        return minor >= 19
    if major == 22:
        return minor >= 12
    return major >= 23


def node_major(*, macos: bool | None = None, mac_version: str | None = None) -> int:
    """The Node major to download: NODE_MAJOR, or NODE_MAJOR_OLD_MACOS before macOS 13.5."""
    macos = common.IS_MACOS if macos is None else macos
    if not macos:
        return NODE_MAJOR
    raw = platform.mac_ver()[0] if mac_version is None else mac_version
    try:
        parts = tuple(int(p) for p in raw.split(".")[:2])
    except ValueError:
        return NODE_MAJOR
    if parts and parts < _NODE_MAJOR_MIN_MACOS:
        return NODE_MAJOR_OLD_MACOS
    return NODE_MAJOR


def _node_version(node: Path) -> tuple[int, int, int] | None:
    """The version that ``node --version`` prints, or None if it doesn't run."""
    try:
        out = subprocess.run(
            [str(node), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return parse_version(out.stdout)


# --- Finding Node --------------------------------------------------------------------


def _system_node_candidate() -> tuple[Path, tuple[int, int, int] | None] | None:
    """Node on PATH and its version (None if it doesn't run), or None if there is none."""
    found = shutil.which("node")
    if found is None:
        return None
    node = Path(found)
    return node, _node_version(node)


def find_system_node() -> NodeTool | None:
    """Node on PATH, if its version is OK."""
    candidate = _system_node_candidate()
    if candidate is None:
        return None
    node, version = candidate
    if version is None or not node_version_ok(version):
        return None
    npm = _find_npm(node)
    if npm is None:
        ui.detail(f"Node {format_version(version)} found at {node}, but npm is missing.")
        return None
    return NodeTool(node=node, npm=npm, bin_dir=node.parent, version=version, private=False)


def _find_npm(node: Path, *, windows: bool | None = None) -> Path | None:
    """npm for the Node at ``node``.

    On Windows, ``shutil.which("npm")`` can return the extensionless shell script (Python
    3.12.0), which Popen can't run (WinError 193). So prefer ``npm.cmd`` next to node.exe,
    then ``npm.cmd`` on PATH, then an ``npm`` with a runnable extension (e.g. Volta's npm.exe).
    """
    windows = common.IS_WINDOWS if windows is None else windows
    if not windows:
        found = shutil.which("npm")
        return Path(found) if found else None
    beside = node.parent / "npm.cmd"
    if beside.is_file():
        return beside
    found = shutil.which("npm.cmd")
    if found:
        return Path(found)
    found = shutil.which("npm")
    if found and Path(found).suffix.lower() in (".cmd", ".exe", ".bat"):
        return Path(found)
    return None


def _private_layout(node_dir: Path, *, windows: bool) -> tuple[Path, Path, Path]:
    """``(node, npm, bin_dir)`` inside an unpacked Node folder."""
    if windows:
        return node_dir / "node.exe", node_dir / "npm.cmd", node_dir
    bin_dir = node_dir / "bin"
    return bin_dir / "node", bin_dir / "npm", bin_dir


def find_private_node(
    *, node_dir: Path | None = None, windows: bool | None = None
) -> NodeTool | None:
    """The private Node in backend/.venv/node, if present, working and of :func:`node_major`."""
    node_dir = common.PRIVATE_NODE_DIR if node_dir is None else node_dir
    windows = common.IS_WINDOWS if windows is None else windows
    node, npm, bin_dir = _private_layout(node_dir, windows=windows)
    try:
        recorded = parse_version((node_dir / "VERSION").read_text(encoding="utf-8"))
    except OSError:
        recorded = None
    if recorded is None or not node.is_file() or not npm.exists():
        return None
    version = _node_version(node)
    if version is None or version[0] != node_major():
        return None
    return NodeTool(node=node, npm=npm, bin_dir=bin_dir, version=version, private=True)


# --- Which build fits this computer ----------------------------------------------------


def _is_musl() -> bool:
    """True on musl-based Linux (Alpine), which has no official Node.js builds."""
    lib, _ver = platform.libc_ver()
    if lib == "glibc":
        return False
    if "musl" in lib:
        return True
    if Path("/etc/alpine-release").exists():
        return True
    try:
        out = subprocess.run(["ldd", "--version"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return "musl" in (out.stdout + out.stderr).lower()


def _unsupported(what: str) -> common.InstallError:
    return common.InstallError(
        f"nodejs.org has no official Node.js build for {what}, so the installer can't "
        "download one.",
        hint="Install Node.js 20.19 or newer yourself (for example 'apk add nodejs npm' on "
        "Alpine, or from https://nodejs.org), then run the installer again.",
    )


def archive_platform(
    *,
    windows: bool | None = None,
    macos: bool | None = None,
    linux: bool | None = None,
    arch: str | None = None,
    musl: bool | None = None,
    is_32bit: bool | None = None,
) -> tuple[str, str]:
    """``(platform, extension)`` of the official archive, e.g. ``("linux-x64", "tar.xz")``.

    Raises InstallError on systems without an official build (musl, 32-bit, other CPUs).
    Every argument defaults to this computer.
    """
    windows = common.IS_WINDOWS if windows is None else windows
    macos = common.IS_MACOS if macos is None else macos
    linux = common.IS_LINUX if linux is None else linux
    arch = common.cpu_arch() if arch is None else arch
    if is_32bit is None:
        # 32-bit Python on 64-bit Windows is fine (cpu_arch sees the real CPU); elsewhere a
        # 32-bit Python means a 32-bit system. armv8l is 32-bit ARM on a 64-bit kernel.
        is_32bit = (not windows and sys.maxsize <= 2**32) or platform.machine() == "armv8l"
    if is_32bit:
        raise _unsupported("32-bit systems")
    if arch not in ("x64", "arm64"):
        raise _unsupported(f"this processor ({platform.machine() or 'unknown'})")
    if windows:
        return f"win-{arch}", "zip"
    if macos:
        return f"darwin-{arch}", "tar.gz"
    if linux:
        if musl if musl is not None else _is_musl():
            raise _unsupported("musl-based Linux such as Alpine")
        return f"linux-{arch}", "tar.xz"
    raise _unsupported(f"this operating system ({platform.system() or sys.platform})")


def archive_name(version: str, plat: str, ext: str) -> str:
    """``node-v24.11.1-linux-x64.tar.xz`` (``version`` without the leading v)."""
    return f"node-v{version}-{plat}.{ext}"


def pick_version(index: Any, major: int = NODE_MAJOR) -> str:
    """The newest ``X.Y.Z`` of ``major`` in nodejs.org's ``index.json`` (without the v)."""
    best: tuple[int, int, int] | None = None
    if isinstance(index, list):
        for entry in index:
            raw = entry.get("version") if isinstance(entry, dict) else None
            if not isinstance(raw, str) or not raw.startswith(f"v{major}."):
                continue
            version = parse_version(raw)
            if version is not None and (best is None or version > best):
                best = version
    if best is None:
        raise common.InstallError(
            f"nodejs.org lists no Node.js {major} release.", hint=_INSTALL_NODE_HINT
        )
    return format_version(best)


def expected_sha256(shasums: str, filename: str) -> str:
    """The checksum of ``filename`` in a ``SHASUMS256.txt`` file."""
    for line in shasums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == filename:
            digest = parts[0].lower()
            if re.fullmatch(r"[0-9a-f]{64}", digest):
                return digest
    raise common.InstallError(
        f"nodejs.org has no checksum for {filename}.", hint=_INSTALL_NODE_HINT
    )


def verify_sha256(path: Path, expected: str) -> None:
    """Delete ``path`` and raise InstallError if its SHA-256 isn't ``expected``."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected.lower():
        with contextlib.suppress(OSError):
            path.unlink()
        raise common.InstallError(
            f"The downloaded Node.js ({path.name}) failed its checksum check and was deleted.",
            hint="The download may have been damaged or changed on the way. Try again; if it "
            "keeps failing, install Node.js 20.19 or newer yourself from https://nodejs.org.",
        )


# --- Downloading ---------------------------------------------------------------------


def _check_url(url: str) -> None:
    """Only HTTPS, except to this computer (tests)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https":
        return
    if parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost", "::1"):
        return
    raise common.InstallError(f"Refusing to download over an insecure connection: {url}")


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Follows redirects only to URLs that :func:`_check_url` accepts."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        _check_url(urllib.parse.urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@functools.cache
def _https_context() -> ssl.SSLContext:
    return common.https_context()


def _default_opener(url: str) -> IO[bytes]:
    """``urlopen`` with the macOS certificate fallback and HTTPS-only redirects.

    Built per call, so it picks up the current HTTPS_PROXY (build_opener adds ProxyHandler).
    """
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=_https_context()), _HttpsOnlyRedirects()
    )
    response: IO[bytes] = opener.open(url, timeout=_TIMEOUT)
    return response


def _network_error(url: str, exc: BaseException) -> common.InstallError:
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(exc, urllib.error.HTTPError):
        reason = f"HTTP {exc.code} {exc.reason}"
    if common.is_certificate_error(exc):
        return common.InstallError(
            f"Could not download {url}: {reason}.", hint=common.CERTIFICATE_HINT
        )
    return common.InstallError(
        f"Could not download {url}: {reason}.",
        hint="Check your internet connection (and HTTPS_PROXY if you are behind a proxy). "
        + _INSTALL_NODE_HINT,
    )


def _disk_hint() -> str:
    return _DISK_HINT_WINDOWS if common.IS_WINDOWS else _DISK_HINT_OTHER


_NETWORK_ERRORS = (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError)


def _fetch_bytes(url: str, opener: Opener, limit: int = 20_000_000) -> bytes:
    _check_url(url)
    try:
        with opener(url) as resp:
            data = resp.read(limit + 1)
    except _NETWORK_ERRORS as exc:
        raise _network_error(url, exc) from exc
    if len(data) > limit:
        raise common.InstallError(f"Unexpectedly large response from {url}.")
    return data


class _Progress:
    """``Downloading x… 42%``, updated in place on a terminal, one line otherwise."""

    def __init__(self, label: str, total: int | None) -> None:
        self.label = label
        self.total = total if total and total > 0 else None
        self.tty = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
        self.last = -1
        if not self.tty:
            ui.info(f"Downloading {label}…")

    def update(self, done: int) -> None:
        if not self.tty:
            return
        if self.total:
            pct = min(100, done * 100 // self.total)
            if pct == self.last:
                return
            self.last = pct
            text = f"{pct:3d}%"
        else:
            text = f"{done / 1_000_000:.1f} MB"
        sys.stdout.write(f"\r    Downloading {self.label}… {text}")
        sys.stdout.flush()

    def finish(self) -> None:
        if self.tty:
            sys.stdout.write("\n")
            sys.stdout.flush()


def _download(url: str, dest: Path, opener: Opener, label: str) -> None:
    """Stream ``url`` into ``dest`` (which the caller deletes on failure)."""
    _check_url(url)
    total: int | None = None
    done = 0
    with dest.open("wb") as out:
        try:
            with opener(url) as resp:
                headers = getattr(resp, "headers", None)
                length = headers.get("Content-Length") if headers is not None else None
                total = int(length) if length and str(length).isdigit() else None
                progress = _Progress(label, total)
                try:
                    while chunk := resp.read(_CHUNK):
                        try:
                            out.write(chunk)
                        except OSError as exc:
                            raise common.InstallError(
                                f"Could not save the download to {dest}: {exc}"
                            ) from exc
                        done += len(chunk)
                        progress.update(done)
                finally:
                    progress.finish()
        except _NETWORK_ERRORS as exc:
            raise _network_error(url, exc) from exc
    if total is not None and done != total:
        raise common.InstallError(
            f"The download of {url} was cut off ({done} of {total} bytes).",
            hint="Try again. " + _INSTALL_NODE_HINT,
        )


# --- Unpacking -----------------------------------------------------------------------


def _unsafe(name: str) -> common.InstallError:
    return common.InstallError(f"Unsafe path in the Node.js archive: {name!r}")


def _member_parts(name: str) -> tuple[str, ...]:
    return PurePosixPath(name.replace("\\", "/")).parts


def _check_member(name: str) -> None:
    """Refuse absolute paths, drive letters and ``..`` in an archive entry name."""
    norm = name.replace("\\", "/")
    parts = PurePosixPath(norm).parts
    if norm.startswith("/") or (parts and ":" in parts[0]) or ".." in parts:
        raise _unsafe(name)


def _check_tar_links(members: list[tarfile.TarInfo]) -> None:
    """Refuse links that point outside the archive's top folder, or that go through links.

    ``filter="data"`` checks this too, but has known bypasses before Python 3.12.11 /
    3.13.4. Paths are resolved by name only, so nothing may pass through another link
    (where the real location would differ from the name).
    """
    links = {_member_parts(m.name): m for m in members if m.issym() or m.islnk()}
    for parts in (_member_parts(m.name) for m in members):
        if any(parts[:i] in links for i in range(1, len(parts))):
            raise common.InstallError(
                f"Unsafe path in the Node.js archive: {'/'.join(parts)!r} is inside a link."
            )
    for parts, member in links.items():
        target = member.linkname.replace("\\", "/")
        if not target or target.startswith("/") or ":" in target.split("/")[0]:
            raise _unsafe(f"{member.name} -> {member.linkname}")
        # Symlinks are relative to their folder, hard links to the archive root.
        stack = list(parts[:-1]) if member.issym() else []
        target_parts = PurePosixPath(target).parts
        for i, part in enumerate(target_parts):
            if part == "..":
                if not stack:
                    raise _unsafe(f"{member.name} -> {member.linkname}")
                stack.pop()
                continue
            stack.append(part)
            # Pointing at another link is fine (it is checked too); going through one isn't.
            if i < len(target_parts) - 1 and tuple(stack) in links:
                raise _unsafe(f"{member.name} -> {member.linkname}")
        if not stack or stack[0] != parts[0]:
            raise _unsafe(f"{member.name} -> {member.linkname}")


def safe_unpack(archive: Path, dest: Path) -> None:
    """Unpack a .zip / .tar.gz / .tar.xz into ``dest``, refusing paths that escape it."""
    try:
        if archive.name.endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                for info in zf.infolist():
                    _check_member(info.filename)
                zf.extractall(dest)
        else:
            with tarfile.open(archive, "r:*") as tf:
                members = tf.getmembers()
                for member in members:
                    _check_member(member.name)
                _check_tar_links(members)
                # "data" also refuses absolute paths, "..", links leaving dest and device
                # files, and keeps the owner's exec bits.
                tf.extractall(dest, filter="data")
    except (tarfile.TarError, zipfile.BadZipFile, EOFError) as exc:
        raise common.InstallError(
            f"Could not unpack the Node.js archive ({archive.name}): {exc}",
            hint=_INSTALL_NODE_HINT,
        ) from exc
    except OSError as exc:
        raise common.InstallError(
            f"Could not unpack the Node.js archive into {dest}: {exc}", hint=_disk_hint()
        ) from exc


def _single_top_dir(folder: Path) -> Path:
    entries = list(folder.iterdir())
    if len(entries) != 1 or not entries[0].is_dir():
        raise common.InstallError(
            "The Node.js archive doesn't have the expected layout (one top-level folder).",
            hint=_INSTALL_NODE_HINT,
        )
    return entries[0]


def replace_dir(new: Path, target: Path) -> None:
    """Put folder ``new`` at ``target``, replacing an old one with two renames.

    The old folder is renamed aside first and deleted only after ``new`` is in place, so
    there is never a moment with a half-filled ``target``.
    """
    old: Path | None = None
    if target.exists() or target.is_symlink():
        old = target.with_name(f".{target.name}-old-{os.getpid()}-{time.time_ns()}")
        # Retried: on Windows antivirus or indexing briefly locks freshly unpacked files.
        common.rename_with_retry(target, old)
    try:
        common.rename_with_retry(new, target)
    except BaseException:
        if old is not None:
            common.rename_with_retry(old, target)
        raise
    if old is not None:
        with contextlib.suppress(OSError):
            common.remove_tree(old)  # else removed on the next run


def _clean_leftovers(node_dir: Path) -> None:
    """Remove temp folders of an earlier, cancelled download or a replaced old copy."""
    parent = node_dir.parent
    if not parent.is_dir():
        return
    for kind in ("download", "unpack", "old"):
        for path in parent.glob(f".{node_dir.name}-{kind}-*"):
            with contextlib.suppress(OSError):
                common.remove_tree(path)


def download_private_node(
    *,
    node_dir: Path | None = None,
    dist_url: str = NODE_DIST_URL,
    opener: Opener | None = None,
    major: int | None = None,
    plat: tuple[str, str] | None = None,
) -> str:
    """Download, verify and unpack the newest Node ``major`` into ``node_dir``.

    ``major`` defaults to :func:`node_major`. Returns the installed version (``24.11.1``).
    Doesn't ask; see :func:`ensure_node`.
    """
    node_dir = common.PRIVATE_NODE_DIR if node_dir is None else node_dir
    major = node_major() if major is None else major
    opener = _default_opener if opener is None else opener
    plat_name, ext = archive_platform() if plat is None else plat
    dist_url = dist_url.rstrip("/")

    raw_index = _fetch_bytes(f"{dist_url}/index.json", opener)
    try:
        index = json.loads(raw_index.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise common.InstallError(
            "nodejs.org sent an unreadable release list (index.json).", hint=_INSTALL_NODE_HINT
        ) from exc
    version = pick_version(index, major)
    filename = archive_name(version, plat_name, ext)
    shasums = _fetch_bytes(f"{dist_url}/v{version}/SHASUMS256.txt", opener)
    expected = expected_sha256(shasums.decode("utf-8", errors="replace"), filename)

    parent = node_dir.parent
    _clean_leftovers(node_dir)
    download_dir: Path | None = None
    staging: Path | None = None
    try:
        parent.mkdir(parents=True, exist_ok=True)
        download_dir = Path(tempfile.mkdtemp(prefix=f".{node_dir.name}-download-", dir=parent))
        staging = Path(tempfile.mkdtemp(prefix=f".{node_dir.name}-unpack-", dir=parent))
        archive = download_dir / filename
        url = f"{dist_url}/v{version}/{filename}"
        for attempt in range(_DOWNLOAD_ATTEMPTS):
            try:
                _download(url, archive, opener, f"Node.js {version}")
                break
            except common.InstallError:
                if attempt + 1 == _DOWNLOAD_ATTEMPTS:
                    raise
                ui.info("The download was interrupted. Trying again…")
        verify_sha256(archive, expected)
        ui.info(f"Checksum OK. Unpacking into {node_dir}…")
        safe_unpack(archive, staging)
        top = _single_top_dir(staging)
        (top / "VERSION").write_text(version + "\n", encoding="utf-8")
        try:
            replace_dir(top, node_dir)
        except OSError as exc:
            raise common.InstallError(
                f"Could not move the new Node.js into {node_dir}: {exc}",
                hint="Close programs that might use it and run the installer again.",
            ) from exc
    except OSError as exc:
        # Creating folders, saving the archive or writing VERSION: a full disk, missing
        # permissions or (on Windows) a path over 260 characters.
        raise common.InstallError(
            f"Could not set up Node.js in {parent}: {exc}", hint=_disk_hint()
        ) from exc
    finally:
        for folder in (download_dir, staging):
            if folder is not None:
                shutil.rmtree(folder, ignore_errors=True)
    return version


def ensure_node(
    *,
    force_private: bool = False,
    node_dir: Path | None = None,
    dist_url: str = NODE_DIST_URL,
    opener: Opener | None = None,
) -> NodeTool:
    """A usable Node: system (unless force_private), else private, else download (asks first).

    Raises InstallError if the user declines or no official build exists for this system.
    """
    node_dir = common.PRIVATE_NODE_DIR if node_dir is None else node_dir
    _clean_leftovers(node_dir)  # cheap; a cancelled download can leave up to ~180 MB
    if not force_private:
        system = find_system_node()
        if system is not None:
            ui.info(f"Using Node.js {format_version(system.version)} ({system.node}).")
            return system

    private = find_private_node(node_dir=node_dir)
    if private is not None:
        ui.info(f"Using the private Node.js {format_version(private.version)} ({node_dir}).")
        return private

    if not force_private:
        candidate = _system_node_candidate()
        if candidate is None:
            ui.info("Node.js was not found.")
        elif candidate[1] is None:
            ui.info(f"Node.js at {candidate[0]} doesn't run.")
        elif not node_version_ok(candidate[1]):
            ui.info(
                f"Node.js {format_version(candidate[1])} is too old; the build needs 20.19 "
                "or newer."
            )
        else:
            ui.info("Node.js was found, but npm is missing.")

    plat = archive_platform()  # fail before asking if there is nothing to download
    if not ui.ask_yes_no(_DOWNLOAD_PROMPT, default=True):
        raise common.InstallError(
            "Node.js is needed to build the app's interface.",
            hint="Install Node.js 20.19 or newer from https://nodejs.org, then run the "
            "installer again.",
        )
    version = download_private_node(node_dir=node_dir, dist_url=dist_url, opener=opener, plat=plat)
    tool = find_private_node(node_dir=node_dir)
    if tool is None:
        raise common.InstallError(
            f"The downloaded Node.js {version} in {node_dir} doesn't run.",
            hint=_INSTALL_NODE_HINT,
        )
    ui.info(f"Node.js {version} is ready ({node_dir}).")
    return tool
