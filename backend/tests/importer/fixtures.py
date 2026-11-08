"""Reusable builders for tiny fake Instagram exports (folders and zips).

Imported by tests as ``importer_fixtures`` (registered by this folder's conftest).

Typical use::

    t = thread(["Jana", "Me"], [msg("Jana", 1000, "ahoj")])
    files = export_files({("inbox", "jana_1"): [t]})
    root = write_tree(tmp_path / "export", files)  # or make_zip(tmp_path / "x.zip", files)
"""

import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import orjson

CATEGORY_DIR = {"inbox": "inbox", "request": "message_requests", "archived": "archived_threads"}
LAYOUT_PREFIX = {"new": "your_instagram_activity/messages", "old": "messages"}


def mojibake(s: str) -> str:
    """Instagram's broken encoding: UTF-8 bytes read as Latin-1."""
    return s.encode("utf-8").decode("latin-1")


def mojibake_tree(node: Any) -> Any:
    if isinstance(node, str):
        return mojibake(node)
    if isinstance(node, dict):
        return {k: mojibake_tree(v) for k, v in node.items()}
    if isinstance(node, list):
        return [mojibake_tree(v) for v in node]
    return node


def msg(sender: str, ts_ms: int, content: str | None = None, **extra: Any) -> dict[str, Any]:
    d: dict[str, Any] = {"sender_name": sender, "timestamp_ms": ts_ms}
    if content is not None:
        d["content"] = content
    d.update(extra)
    return d


def thread(
    participants: Sequence[str],
    messages: Sequence[Mapping[str, Any]],
    *,
    title: str | None = None,
    newest_first: bool = True,
    **extra: Any,
) -> dict[str, Any]:
    """A thread file dict. ``messages`` are given in chronological order and stored
    newest first (like Instagram) unless ``newest_first=False``."""
    ms = list(messages)
    if newest_first:
        # Stable ascending sort, then reverse: ties also end up newest (= later) first.
        ms.sort(key=lambda m: m.get("timestamp_ms", 0))
        ms.reverse()
    d: dict[str, Any] = {
        "participants": [{"name": p} for p in participants],
        "messages": ms,
        "title": title if title is not None else (participants[0] if participants else ""),
        "is_still_participant": True,
    }
    d.update(extra)
    return d


def export_files(
    threads: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
    *,
    layout: str = "new",
    owner: str | None = None,
    media: Sequence[str] = (),
    encode: bool = True,
) -> dict[str, bytes]:
    """Relative path → bytes of a fake export.

    ``threads`` maps ``(category, folder)`` to the list of file dicts (``message_1.json``
    first). ``encode`` applies Instagram's mojibake to all strings. ``media`` are paths
    relative to the ``messages`` folder (e.g. ``"inbox/jana_1/photos/1.jpg"``).
    """
    prefix = LAYOUT_PREFIX[layout]
    out: dict[str, bytes] = {}
    for (category, folder), files in threads.items():
        for i, data in enumerate(files, start=1):
            payload = mojibake_tree(dict(data)) if encode else dict(data)
            out[f"{prefix}/{CATEGORY_DIR[category]}/{folder}/message_{i}.json"] = orjson.dumps(
                payload
            )
    for m in media:
        out[f"{prefix}/{m}"] = b"\x89PNG fake"
    if owner is not None:
        info = {
            "profile_user": [
                {
                    "media_map_data": {},
                    "string_map_data": {
                        "Email": {"href": "", "value": "x@example.com", "timestamp": 0},
                        "Name": {"href": "", "value": mojibake(owner), "timestamp": 0},
                        "Username": {"href": "", "value": "owner_handle", "timestamp": 0},
                    },
                }
            ]
        }
        out["personal_information/personal_information/personal_information.json"] = orjson.dumps(
            info
        )
    return out


def write_tree(root: Path, files: Mapping[str, bytes]) -> Path:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


def make_zip(path: Path, files: Mapping[str, bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel, data in files.items():
            zf.writestr(rel, data)
    return path
