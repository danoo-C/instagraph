"""Anonymize mode: NO GET endpoint may return a real participant name, thread title,
account name or message text (spec: screenshots-safe mode)."""

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from instagraph.store.dataset import Dataset
from instagraph.testing import make_client

#: Real strings of the ``small_dataset`` fixture (names, titles, folders, texts, account).
LEAKS = [
    "Jana",
    "Nováková",
    "Peter",
    "Eva",
    "Spammer",
    "Partia",
    "jananovakova",
    "partia_2",
    "spammer_3",
    "Ahoj",
    "ako sa",
    "Dobre",
    "pozri",
    "super",
    "haha",
    "Čau",
    "ideme",
    "vikend",
    "Áno",
    "Buy followers",
    "Ďakujem",
    "veľmi",
    "nemáš",
    "dobré ráno",
    '"funny"',
    "example.com/x",
    "instagram.com/reel",
    # lower-cased tokens (word lists, mentions, hashtags)
    "ahoj",
    "dobre",
    "ďakujem",
    "followers",
    "čau",
    "áno",
    "peter",
    "jana",
    "nováková",
]

#: Path parameter values for the fixture dataset.
PARAMS = {
    "chat_id": ["0", "1", "2"],
    "person_id": ["1", "2"],
    "message_id": ["0", "7"],
    "conv_id": ["0" * 32],  # no conversations in the fixture (404); see tests/ai/test_api.py
}
#: Not JSON / not data-bearing in the fixture.
SKIP_PREFIXES = ("/api/media/", "/api/import/jobs/")


@pytest.fixture
def anon_client(tmp_path: Path, small_dataset: Dataset) -> Iterator[TestClient]:
    small_dataset.import_report.update(
        {
            "unmatched_system_like": [{"text": "Jana Nováková did something", "count": 1}],
            "sources": ["/home/jana/export.zip"],
            "owner": {"name": "Me", "auto_name": "Me", "method": "auto"},
        }
    )
    with make_client(tmp_path / "data", small_dataset) as c:
        resp = c.put(
            "/api/settings",
            json={"anonymize": True, "ai_enabled": True, "ai_confirmed_notice": True},
        )
        assert resp.status_code == 200, resp.text
        yield c


def _get_paths(client: TestClient) -> Iterator[str]:
    spec = client.get("/openapi.json").json()
    for path, ops in spec["paths"].items():
        if "get" not in ops or path.startswith(SKIP_PREFIXES):
            continue
        names = re.findall(r"{(\w+)}", path)
        if not names:
            yield path
            continue
        if names == ["scope"]:
            for scope in ("all", "chat-0", "chat-1"):
                yield path.replace("{scope}", scope)
            continue
        (name,) = names
        for value in PARAMS[name]:
            yield path.replace(f"{{{name}}}", value)


EXTRA_QUERIES: dict[str, list[dict[str, Any]]] = {
    "/api/chats/2/terms": [{"q": "buy"}],  # the query itself is echoed (user input)
    "/api/messages": [{"q": "ahoj"}, {"thread_id": 1}],
    "/api/compare": [{"people": "1,2"}, {"chats": "0,1"}],
    "/api/wrapped": [{"year": 2024}, {"chat": 0, "year": 2024}],
    "/api/export/all.json": [{"include_messages": True}],
    "/api/export/chat-0.json": [{"include_messages": True}],
    "/api/people": [{"q": "a"}],
}


def test_no_real_names_or_texts_in_any_get(anon_client: TestClient) -> None:
    checked = 0
    leaks: list[str] = []
    for path in _get_paths(anon_client):
        for params in [{}, *EXTRA_QUERIES.get(path, [])]:
            resp = anon_client.get(path, params={"include_requests": True, **params})
            if resp.status_code in (400, 404, 422):
                continue  # e.g. group tab of a 1:1 chat, compare without ids
            assert resp.status_code == 200, (path, params, resp.text)
            body = json.dumps(resp.json(), ensure_ascii=False)
            found = [s for s in LEAKS if s in body]
            if found:
                leaks.append(f"{path} {params} leaks {found}")
            checked += 1
    assert not leaks, "\n".join(leaks)
    assert checked > 40
