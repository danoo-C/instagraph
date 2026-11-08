"""Shared fixtures.

``small_dataset`` people / threads (ids are list positions):

* people: 0 Me (owner), 1 Jana Nováková, 2 Peter, 3 Eva, 4 Spammer
* thread 0: 1:1 Me ↔ Jana (inbox), 12 rows incl. a like, a call, a photo
* thread 1: group "Partia" Me, Jana, Peter, Eva (inbox), 7 rows incl. a rename
* thread 2: request from Spammer (category "request"), 1 row
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from instagraph.store.dataset import Dataset
from instagraph.testing import build_dataset, make_client

PEOPLE: list[dict[str, Any]] = [
    {"display_name": "Me", "is_owner": True},
    {"display_name": "Jana Nováková", "aliases": ["Jana", "Jana Nováková"]},
    {"display_name": "Peter"},
    {"display_name": "Eva"},
    {"display_name": "Spammer"},
]

THREADS: list[dict[str, Any]] = [
    {"title": "Jana Nováková", "folder": "jananovakova_1", "participants": [0, 1]},
    {"title": "Partia", "folder": "partia_2", "participants": [0, 1, 2, 3]},
    {"title": "Spammer", "folder": "spammer_3", "participants": [0, 4], "category": "request"},
]

# Chronological order (message_id = list index).
MESSAGES: list[dict[str, Any]] = [
    # thread 0, day 1 (session 1)
    {"thread_id": 0, "sender_id": 1, "ts": "2024-03-30T21:00:00Z", "text": "Ahoj, ako sa máš? 😂"},
    {"thread_id": 0, "sender_id": 0, "ts": "2024-03-30T21:02:00Z", "text": "Dobre! A ty?"},
    {
        "thread_id": 0,
        "sender_id": 0,
        "ts": "2024-03-30T21:05:00Z",
        "text": "pozri https://example.com/x",
    },
    {"thread_id": 0, "sender_id": 1, "ts": "2024-03-30T21:10:00Z", "type": "like"},
    {"thread_id": 0, "sender_id": 1, "ts": "2024-03-30T21:15:00Z", "text": "super ❤️👍🏽"},
    # thread 0, day 2 (new session; crosses DST change in Europe on 2024-03-31)
    {"thread_id": 0, "sender_id": 0, "ts": "2024-03-31T08:00:00Z", "n_photos": 2},
    {"thread_id": 0, "sender_id": 1, "ts": "2024-03-31T09:00:00Z", "text": "haha 😂😂"},
    # group thread
    {"thread_id": 1, "sender_id": 2, "ts": "2024-04-01T10:00:00Z", "text": "Čau všetci 👋"},
    {
        "thread_id": 1,
        "sender_id": 0,
        "ts": "2024-04-01T10:01:00Z",
        "type": "system",
        "system_event": "rename",
        "system_payload": "Partia",
        "text": None,
    },
    {"thread_id": 1, "sender_id": 3, "ts": "2024-04-01T10:03:00Z", "text": "🇸🇰👨‍👩‍👧"},
    {"thread_id": 1, "sender_id": 1, "ts": "2024-04-01T10:04:00Z", "text": "@peter ideme? #vikend"},
    {"thread_id": 1, "sender_id": 0, "ts": "2024-04-01T12:00:00Z", "text": "Áno"},
    # thread 0 call
    {
        "thread_id": 0,
        "sender_id": 0,
        "ts": "2024-04-02T19:00:00Z",
        "call_duration_s": 312,
        "call_kind": "audio",
    },
    # request
    {"thread_id": 2, "sender_id": 4, "ts": "2024-04-03T12:00:00Z", "text": "Buy followers!"},
    # thread 0 more
    {"thread_id": 0, "sender_id": 1, "ts": "2024-04-05T18:00:00Z", "text": "Ďakujem"},
    {"thread_id": 0, "sender_id": 1, "ts": "2024-04-05T18:03:00Z", "text": "veľmi"},
    {"thread_id": 0, "sender_id": 0, "ts": "2024-04-05T18:30:00Z", "text": "nemáš začo"},
    {"thread_id": 0, "sender_id": 0, "ts": "2024-04-05T18:31:00Z", "is_unsent": True},
    {"thread_id": 0, "sender_id": 1, "ts": "2024-04-06T08:00:00Z", "text": "dobré ráno"},
    {
        "thread_id": 1,
        "sender_id": 2,
        "ts": "2024-04-06T09:00:00Z",
        "share_link": "https://www.instagram.com/reel/abc",
        "share_kind": "reel",
        "share_owner": "funny",
    },
]

REACTIONS: list[dict[str, Any]] = [
    {"message_id": 0, "actor_id": 0, "emoji": "😂", "ts": "2024-03-30T21:01:00Z"},
    {"message_id": 1, "actor_id": 1, "emoji": "❤"},
    {"message_id": 7, "actor_id": 3, "emoji": "👍"},
    {"message_id": 7, "actor_id": 0, "emoji": "❤"},
    {"message_id": 19, "actor_id": 0, "emoji": "😂"},
]


@pytest.fixture
def small_dataset() -> Dataset:
    return build_dataset(PEOPLE, THREADS, MESSAGES, REACTIONS)


@pytest.fixture
def client(tmp_path: Path, small_dataset: Dataset) -> Iterator[TestClient]:
    with make_client(tmp_path / "data", small_dataset) as c:
        yield c


@pytest.fixture
def empty_client(tmp_path: Path) -> Iterator[TestClient]:
    with make_client(tmp_path / "data") as c:
        yield c
