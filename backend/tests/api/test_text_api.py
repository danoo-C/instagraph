"""API tests for /api/chats/{id}/words|emojis|terms and /api/overview/emojis.

Uses the shared ``small_dataset`` (see tests/conftest.py): thread 0 = Me ↔ Jana,
thread 1 = group "Partia", thread 2 = request.
"""

from typing import Any

from fastapi.testclient import TestClient

from instagraph.schemas.emojis import EmojisResponse, OverviewEmojisResponse
from instagraph.schemas.words import TermsResponse, WordsResponse
from instagraph.store.app_settings import AppSettings, get_settings_store


def _person(rows: list[dict[str, Any]], pid: int) -> dict[str, Any]:
    return next(r for r in rows if r["person"]["person_id"] == pid)


def test_words(client: TestClient) -> None:
    r = client.get("/api/chats/0/words")
    assert r.status_code == 200
    body = WordsResponse.model_validate(r.json())
    assert body.chat_id == 0 and not body.text_hidden
    me = _person(r.json()["people"], 0)
    jana = _person(r.json()["people"], 1)
    # Me: "Dobre! A ty?", "pozri https://…", "nemáš začo" → 3 text msgs, 6 words
    assert (me["text_messages"], me["words"], me["links"]) == (3, 6, 1)
    assert me["question_rate"] == 1 / 3 and me["exclamation_rate"] == 1 / 3
    # Jana: 6 text messages; laughter in "Ahoj… 😂" and "haha 😂😂"
    assert (jana["text_messages"], jana["laugh_messages"]) == (6, 2)
    assert jana["laughs_per_100"] == 2 / 6 * 100
    terms = {t["term"] for t in r.json()["top_words"]["overall"]}
    assert {"ahoj", "dobre", "pozri", "super"} <= terms
    assert not {"ako", "sa", "máš", "a", "ty"} & terms  # stop-words
    assert body.swear is None
    assert [m.message_id for m in body.longest_messages[1].messages][:1] == [0]
    assert body.longest_messages[1].messages[0].text == "Ahoj, ako sa máš? 😂"


def test_words_group_mentions_and_drilldowns(client: TestClient) -> None:
    body = client.get("/api/chats/1/words").json()
    assert [m["term"] for m in body["top_mentions"]] == ["peter"]
    assert [h["term"] for h in body["top_hashtags"]] == ["vikend"]
    w = body["top_words"]["overall"][0]
    assert w["drilldown"]["thread_id"] == 1 and w["drilldown"]["text_mode"] == "word"


def test_words_filters_change_results(client: TestClient) -> None:
    only_jana = client.get("/api/chats/0/words", params={"people": "1"}).json()
    assert [p["person"]["person_id"] for p in only_jana["people"]] == [1]
    early = client.get("/api/chats/0/words", params={"end": "2024-03-30"}).json()
    assert _person(early["people"], 1)["text_messages"] == 2


def test_words_accent_insensitive(client: TestClient) -> None:
    body = client.get("/api/chats/0/words", params={"accent_insensitive": "true"}).json()
    assert body["accent_insensitive"] is True
    counts = {t["term"]: t["count"] for t in body["top_words"]["overall"]}
    # "Dobre!" and "dobré ráno" merge into one key
    assert counts.get("dobre", 0) + counts.get("dobré", 0) == 2
    assert not ("dobre" in counts and "dobré" in counts)


def test_words_anonymized_hides_word_lists(client: TestClient) -> None:
    get_settings_store().update(AppSettings(anonymize=True))
    body = client.get("/api/chats/0/words").json()
    assert body["text_hidden"] is True
    assert body["top_words"]["overall"] == []
    assert all(p["terms"] == [] for p in body["top_words"]["by_person"])
    assert body["repeated_messages"] == [] and body["top_mentions"] == []
    assert all(m["text"] is None for pm in body["longest_messages"] for m in pm["messages"])
    assert _person(body["people"], 1)["person"]["name"] != "Jana Nováková"
    assert _person(body["people"], 1)["text_messages"] == 6  # numbers stay


def test_words_swear_enabled(client: TestClient) -> None:
    get_settings_store().update(AppSettings(swear_words_enabled=True, swear_words=["super"]))
    body = client.get("/api/chats/0/words").json()
    assert body["swear"]["top"][0]["term"] == "super"
    assert _person(body["people"], 1)["swear_words"] == 1


def test_emojis(client: TestClient) -> None:
    r = client.get("/api/chats/0/emojis")
    assert r.status_code == 200
    body = EmojisResponse.model_validate(r.json())
    # Jana: 😂, ❤, 👍(🏽 merged), 😂😂 → 5 emojis, 3 unique
    assert (body.total, body.unique) == (5, 3)
    assert body.top[0].emoji == "😂" and body.top[0].count == 3
    assert body.top[0].drilldown is not None and body.top[0].drilldown.emoji == "😂"
    jana = next(p for p in body.people if p.person.person_id == 1)
    assert jana.with_emoji_rate == 0.5 and jana.per_message == 5 / 6
    assert body.longest_chain is not None and body.longest_chain.length == 2
    assert sum(sum(v or 0 for v in s.values) for s in body.hearts_over_time.series) == 1
    assert sum(sum(v or 0 for v in s.values) for s in body.usage_over_time.series) == 5


def test_emojis_group_and_skin_tone_setting(client: TestClient) -> None:
    body = client.get("/api/chats/1/emojis").json()
    emojis = {e["emoji"] for e in body["top"]}
    assert {"🇸🇰", "👨‍👩‍👧", "👋"} == emojis
    assert body["emoji_only_messages"] == 1
    get_settings_store().update(AppSettings(emoji_merge_skin_tones=False))
    body0 = client.get("/api/chats/0/emojis").json()
    assert "👍🏽" in {e["emoji"] for e in body0["top"]}


def test_terms(client: TestClient) -> None:
    r = client.get("/api/chats/0/terms", params={"q": "ahoj,dobre", "accent_insensitive": "true"})
    assert r.status_code == 200
    body = TermsResponse.model_validate(r.json())
    by_term = {t.term: t for t in body.terms}
    assert by_term["ahoj"].occurrences == 1
    assert by_term["dobre"].occurrences == 2  # "Dobre!" + "dobré ráno"
    assert by_term["dobre"].first is not None and by_term["dobre"].first.message_id == 1
    assert by_term["dobre"].last is not None and by_term["dobre"].last.message_id == 18
    assert {pc.person.person_id: pc.count for pc in by_term["dobre"].by_person} == {0: 1, 1: 1}
    assert sum(sum(v or 0 for v in s.values) for s in by_term["dobre"].timeline.series) == 2
    strict = client.get("/api/chats/0/terms", params=[("q", "dobre"), ("q", "haha")]).json()
    assert [t["occurrences"] for t in strict["terms"]] == [1, 1]


def test_terms_regex_and_errors(client: TestClient) -> None:
    body = client.get("/api/chats/0/terms", params={"q": "m[aá][sš]", "mode": "regex"}).json()
    assert body["terms"][0]["occurrences"] == 2  # máš, nemáš
    bad = client.get("/api/chats/0/terms", params={"q": "(", "mode": "regex"})
    assert bad.status_code == 422 and bad.json()["error"]["code"]


def test_terms_default_to_saved_terms(client: TestClient) -> None:
    get_settings_store().update(AppSettings(custom_terms={"jananovakova_1": ["super"]}))
    body = client.get("/api/chats/0/terms").json()
    assert [t["term"] for t in body["terms"]] == ["super"]
    words = client.get("/api/chats/0/words").json()
    assert words["saved_terms"] == ["super"]


def test_unknown_chat(client: TestClient) -> None:
    for path in ("words", "emojis", "terms?q=x"):
        assert client.get(f"/api/chats/999/{path}").status_code == 404


def test_no_data(empty_client: TestClient) -> None:
    for path in ("words", "emojis", "terms?q=x"):
        assert empty_client.get(f"/api/chats/0/{path}").status_code == 409
    assert empty_client.get("/api/overview/emojis").status_code == 409


def test_overview_emojis(client: TestClient) -> None:
    r = client.get("/api/overview/emojis")
    assert r.status_code == 200
    body = OverviewEmojisResponse.model_validate(r.json())
    assert body.top[0].emoji == "😂" and body.top[0].count == 3
    assert body.mine == []  # the owner sent no emojis
    assert body.total == 8  # request thread excluded by default
    with_requests = client.get("/api/overview/emojis", params={"include_requests": "true"})
    assert with_requests.json()["total"] == 8
