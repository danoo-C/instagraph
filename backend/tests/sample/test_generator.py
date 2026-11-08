"""Tests for the fake Instagram export generator."""

from __future__ import annotations

import hashlib
from pathlib import Path

import orjson
import pytest

from instagraph.sample import generate_export, zip_export
from instagraph.sample.__main__ import main
from instagraph.sample.generator import MESSAGES_PER_FILE, mojibake


def _fix(s: str) -> str:
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def _files(root: Path) -> list[Path]:
    return sorted(root.rglob("message_*.json"))


def _digest(root: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(root.rglob("*")):
        if f.is_file():
            h.update(f.relative_to(root).as_posix().encode())
            h.update(f.read_bytes())
    return h.hexdigest()


def test_deterministic(tmp_path: Path) -> None:
    a = generate_export(tmp_path / "a", messages=3000, seed=7)
    b = generate_export(tmp_path / "b", messages=3000, seed=7)
    c = generate_export(tmp_path / "c", messages=3000, seed=8)
    assert _digest(a) == _digest(b)
    assert _digest(a) != _digest(c)


@pytest.mark.parametrize("layout", ["new", "old"])
def test_layout_and_structure(tmp_path: Path, layout: str) -> None:
    root = generate_export(tmp_path, messages=5000, layout=layout)  # type: ignore[arg-type]
    base = root / ("your_instagram_activity/messages" if layout == "new" else "messages")
    for cat in ("inbox", "message_requests", "archived_threads"):
        assert list((base / cat).glob("*/message_1.json")), cat
    total = 0
    for f in _files(root):
        assert f.parent.parent.parent == base
        doc = orjson.loads(f.read_bytes())
        assert doc["participants"] and "messages" in doc
        total += len(doc["messages"])
    assert total == 5000
    pi = root / "personal_information/personal_information/personal_information.json"
    data = orjson.loads(pi.read_bytes())
    assert data["profile_user"][0]["string_map_data"]["Name"]["value"] == "Daniel"


def test_mojibake_round_trip(tmp_path: Path) -> None:
    text = "čšťžýáíéúäôň 👩‍❤️‍👨 🇸🇰 👍🏽"
    assert _fix(mojibake(text)) == text
    root = generate_export(tmp_path, messages=4000, owner="Ďurica Ľubo")
    raw = b"".join(f.read_bytes() for f in _files(root))
    assert raw.isascii()  # escaped like real exports
    contents: set[str] = set()
    for f in _files(root):
        for m in orjson.loads(f.read_bytes())["messages"]:
            if m.get("content"):
                contents.add(_fix(m["content"]))
    joined = " ".join(contents)
    assert any(ch in joined for ch in "čšťžľô")
    assert "😂" in joined
    pi = root / "personal_information/personal_information/personal_information.json"
    name = orjson.loads(pi.read_bytes())["profile_user"][0]["string_map_data"]["Name"]["value"]
    assert name != "Ďurica Ľubo" and _fix(name) == "Ďurica Ľubo"


def test_newest_first_and_split(tmp_path: Path) -> None:
    root = generate_export(tmp_path, messages=80_000, seed=1)
    split = [d for d in {f.parent for f in _files(root)} if (d / "message_2.json").exists()]
    assert split
    for d in split:
        n = len(list(d.glob("message_*.json")))
        stamps: list[int] = []
        for i in range(1, n + 1):
            msgs = orjson.loads((d / f"message_{i}.json").read_bytes())["messages"]
            assert len(msgs) <= MESSAGES_PER_FILE
            if i < n:
                assert len(msgs) == MESSAGES_PER_FILE
            stamps += [m["timestamp_ms"] for m in msgs]
        assert stamps == sorted(stamps, reverse=True)
    for f in _files(root):
        ts = [m["timestamp_ms"] for m in orjson.loads(f.read_bytes())["messages"]]
        assert ts == sorted(ts, reverse=True)


def test_features_present(tmp_path: Path) -> None:
    root = generate_export(tmp_path, messages=20_000)
    raw = _fix(b"".join(f.read_bytes() for f in _files(root)).decode())
    for needle in (
        '"reactions"', '"photos"', '"videos"', '"audio_files"', '"gifs"', '"sticker"',
        '"original_content_owner"', '"call_duration"', '"is_unsent"', "Liked a message",
        "named the group", "to the group", "left the group", "Instagram User",
    ):  # fmt: skip
        assert needle in raw, needle
    inbox = root / "your_instagram_activity/messages/inbox"
    prefixes = [d.name.rsplit("_", 1)[0] for d in inbox.iterdir()]
    assert len(prefixes) != len(set(prefixes))  # same person in an old + new thread


def test_media_files_and_zip_cli(tmp_path: Path) -> None:
    root = generate_export(tmp_path / "m", messages=2000, with_media_files=True)
    assert list(root.rglob("*.jpg"))
    z = zip_export(root, tmp_path / "m.zip")
    assert z.stat().st_size > 0
    main(["--messages", "500", "--out", str(tmp_path / "cli"), "--zip"])
    assert (tmp_path / "cli.zip").exists()


@pytest.mark.parametrize("messages", [30, 100, 400, 1500])
def test_small_sizes_do_not_crash(tmp_path: Path, messages: int) -> None:
    for seed in range(6):
        root = generate_export(tmp_path / f"{messages}_{seed}", messages=messages, seed=seed)
        files = _files(root)
        assert files
        for f in files:
            data = orjson.loads(f.read_bytes())
            ts = [m["timestamp_ms"] for m in data["messages"]]
            assert ts == sorted(ts, reverse=True)
