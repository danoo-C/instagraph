"""Test helpers: build a :class:`Dataset` from compact dicts, without the importer.

Example::

    ds = build_dataset(
        people=[{"display_name": "Me", "is_owner": True}, {"display_name": "Jana"}],
        threads=[{"title": "Jana", "participants": [0, 1]}],
        messages=[
            {"thread_id": 0, "sender_id": 1, "ts": "2024-03-01T10:00:00Z", "text": "ahoj 😂"},
            {"thread_id": 0, "sender_id": 0, "ts": "2024-03-01T10:05:00Z", "n_photos": 1},
        ],
        reactions=[{"message_id": 0, "actor_id": 0, "emoji": "❤"}],
    )

Defaults:

* ``person_id`` / ``thread_id`` / ``message_id`` / ``media_id`` = position in the list.
  Messages must be given in chronological order when ids are implicit (ids must be
  ascending by time, like the real importer guarantees) — a ``ValueError`` otherwise.
* ``ts`` accepts ISO strings (``Z``/offset; naive = UTC), ``datetime`` or epoch ms.
* Missing ``type`` is inferred with the §4.1 priority rule from the given fields.
* ``links``, ``word_count``, ``char_count``, ``emojis`` are computed from ``text`` (via
  :mod:`instagraph.textproc`), ``session_id``/``is_session_start``/``turn_id``/
  ``reply_latency_ms`` with a simple per-thread loop (gap/window configurable) that
  follows the contract in :mod:`instagraph.store.dataset`.
* Thread ``folder`` = ``thread_<id>``, ``category`` = inbox, ``is_group`` =
  ``len(participants) > 2``, ``first_ts``/``last_ts``/``message_count`` from messages
  (message_count counts all rows of the thread).
"""

import itertools
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
from fastapi.testclient import TestClient

from instagraph import textproc
from instagraph.store.dataset import (
    MESSAGES_SCHEMA,
    NON_COUNTED_TYPES,
    Dataset,
)

Row = Mapping[str, Any]


def to_ms(ts: str | datetime | int) -> int:
    """Epoch ms from an ISO string / datetime (naive = UTC) / int ms."""
    if isinstance(ts, int):
        return ts
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return int(ts.timestamp() * 1000)


def infer_type(m: Row) -> str:
    """Main message type from the present parts (priority rule §4.1)."""
    if m.get("call_duration_s") is not None or m.get("call_kind") is not None:
        return "call"
    if m.get("is_unsent"):
        return "unsent"
    if m.get("system_event") is not None:
        return "system"
    if m.get("share_link") or m.get("share_kind"):
        return "share"
    for col, typ in (
        ("n_videos", "video"),
        ("n_photos", "photo"),
        ("n_audios", "audio"),
        ("n_gifs", "gif"),
        ("n_stickers", "sticker"),
    ):
        if m.get(col):
            return typ
    text = m.get("text")
    if text and textproc.extract_urls(text):
        return "link"
    if text:
        return "text"
    return "unknown"


def derive_conversation_fields(
    messages: list[dict[str, Any]],
    *,
    session_gap_min: float = 60,
    reply_window_h: float = 12,
) -> None:
    """Fill ``session_id``, ``is_session_start``, ``turn_id``, ``reply_latency_ms`` in place.

    Reference (naive) implementation of the §6.4 contract. ``messages`` must be sorted
    by ``(thread_id, ts_ms)``-compatible order within each thread (we sort by message_id).
    Only counted messages (type not in system/like) take part; others get nulls.
    """
    gap_ms = int(session_gap_min * 60_000)
    window_ms = int(reply_window_h * 3_600_000)
    by_thread: dict[int, list[dict[str, Any]]] = {}
    for m in sorted(messages, key=lambda r: r["message_id"]):
        by_thread.setdefault(m["thread_id"], []).append(m)
    next_session = 0
    next_turn = 0
    for tid in sorted(by_thread):
        prev: dict[str, Any] | None = None
        for m in by_thread[tid]:
            m["session_id"] = None
            m["turn_id"] = None
            m["is_session_start"] = False
            m["reply_latency_ms"] = None
            if m["type"] in NON_COUNTED_TYPES:
                continue
            gap = None if prev is None else m["ts_ms"] - prev["ts_ms"]
            if prev is None or gap is None or gap > gap_ms:
                m["session_id"] = next_session
                m["is_session_start"] = True
                next_session += 1
            else:
                m["session_id"] = prev["session_id"]
            if prev is not None and prev["sender_id"] == m["sender_id"]:
                m["turn_id"] = prev["turn_id"]
            else:
                m["turn_id"] = next_turn
                next_turn += 1
                if prev is not None and gap is not None and gap <= window_ms:
                    m["reply_latency_ms"] = gap
            prev = m


def build_dataset(
    people: Sequence[Row],
    threads: Sequence[Row],
    messages: Sequence[Row],
    reactions: Sequence[Row] = (),
    media: Sequence[Row] = (),
    *,
    session_gap_min: float = 60,
    reply_window_h: float = 12,
    import_report: dict[str, Any] | None = None,
    version: int = 1,
) -> Dataset:
    """Build an in-memory :class:`Dataset` from compact dicts (see module docstring)."""
    people_rows = []
    for i, p in enumerate(people):
        row = dict(p)
        row.setdefault("person_id", i)
        row.setdefault("aliases", [row["display_name"]])
        people_rows.append(row)

    msg_rows: list[dict[str, Any]] = []
    for i, m in enumerate(messages):
        row = dict(m)
        row.setdefault("message_id", i)
        row["ts_ms"] = to_ms(row.pop("ts"))
        row.setdefault("type", infer_type(row))
        text = row.get("text")
        row.setdefault("links", textproc.extract_urls(text) if text else [])
        counted_text = text if row["type"] not in NON_COUNTED_TYPES else None
        row.setdefault("word_count", textproc.word_count(counted_text))
        row.setdefault("char_count", textproc.grapheme_count(counted_text))
        row.setdefault("emojis", textproc.extract_emojis(counted_text))
        msg_rows.append(row)
    ordered = sorted(msg_rows, key=lambda r: r["message_id"])
    for a, b in itertools.pairwise(ordered):
        if b["ts_ms"] < a["ts_ms"]:
            raise ValueError(
                f"message_id {b['message_id']} is earlier than message_id {a['message_id']}; "
                "give messages in chronological order (ids must ascend with time)"
            )
    derive_conversation_fields(
        msg_rows, session_gap_min=session_gap_min, reply_window_h=reply_window_h
    )

    thread_rows = []
    for i, t in enumerate(threads):
        row = dict(t)
        row.setdefault("thread_id", i)
        tid = row["thread_id"]
        row.setdefault("folder", f"thread_{tid}")
        row.setdefault("title", row["folder"])
        row.setdefault("participants", [])
        row.setdefault("is_group", len(row["participants"]) > 2)
        own = [m["ts_ms"] for m in msg_rows if m["thread_id"] == tid]
        row.setdefault("message_count", len(own))
        if own:
            row.setdefault("first_ts", datetime.fromtimestamp(min(own) / 1000, UTC))
            row.setdefault("last_ts", datetime.fromtimestamp(max(own) / 1000, UTC))
        thread_rows.append(row)

    for m in msg_rows:
        m["ts"] = datetime.fromtimestamp(m.pop("ts_ms") / 1000, UTC)
        m.pop("is_unsent", None)

    reaction_rows = []
    for r in reactions:
        row = dict(r)
        if row.get("ts") is not None:
            row["ts"] = datetime.fromtimestamp(to_ms(row["ts"]) / 1000, UTC)
        reaction_rows.append(row)

    media_rows = []
    for i, md in enumerate(media):
        row = dict(md)
        row.setdefault("media_id", i)
        row.setdefault("uri", f"media/{i}")
        row.setdefault("source", "")
        media_rows.append(row)

    return Dataset.from_frames(
        people=_frame(people_rows),
        threads=_frame(thread_rows),
        messages=_frame(msg_rows, MESSAGES_SCHEMA),
        reactions=_frame(reaction_rows) if reaction_rows else None,
        media=_frame(media_rows) if media_rows else None,
        import_report=import_report or {},
        version=version,
    )


def _frame(
    rows: list[dict[str, Any]], schema: dict[str, pl.DataType] | None = None
) -> pl.DataFrame:
    """Rows → DataFrame; string columns stay strings so ``conform`` can cast Enums."""
    if not rows:
        return pl.DataFrame()
    keys: list[str] = []
    for r in rows:
        keys.extend(k for k in r if k not in keys)
    data = {k: [r.get(k) for r in rows] for k in keys}
    overrides: dict[str, pl.DataType] = {}
    if schema is not None:
        overrides = {k: v for k, v in schema.items() if k in data and not isinstance(v, pl.Enum)}
    return pl.DataFrame(data, schema_overrides=overrides, strict=False)


# --------------------------------------------------------------------------------------
# API test client
# --------------------------------------------------------------------------------------


@contextmanager
def make_client(data_dir: Path, dataset: Dataset | None = None) -> Iterator[TestClient]:
    """A ``TestClient`` for a fresh app on ``data_dir`` with ``dataset`` loaded (in memory).

    Usage::

        with make_client(tmp_path, small_dataset) as client:
            assert client.get("/api/status").json()["has_data"]
    """
    from instagraph.config import Settings
    from instagraph.main import create_app
    from instagraph.store.dataset import get_store

    # Never serve a (possibly built) frontend in tests: results must not depend on it.
    app = create_app(
        Settings(
            data_dir=data_dir,
            cors_origins=[],
            frontend_dist=data_dir / "_no_frontend",
            allowed_hosts=["testserver"],  # TestClient's default Host header
        )
    )
    with TestClient(app) as client:
        if dataset is not None:
            get_store().replace(dataset, persist=False)
        yield client
