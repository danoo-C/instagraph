"""API tests for ``/api/chats/{id}/reactions``."""

from fastapi.testclient import TestClient

from instagraph.schemas.reactions import ReactionsResponse


def _get(client: TestClient, chat: int, **params: str) -> ReactionsResponse:
    resp = client.get(f"/api/chats/{chat}/reactions", params=params)
    assert resp.status_code == 200, resp.text
    return ReactionsResponse.model_validate(resp.json())


def test_reactions_one_to_one(client: TestClient) -> None:
    body = _get(client, 0)
    tiles = {t.key: t for t in body.tiles}
    # thread 0: Me 😂 on msg 0 (Jana), Jana ❤ on msg 1 (Me); Jana liked msg 2 (Me)
    assert tiles["total_reactions"].value == 2
    assert tiles["likes"].value == 1
    assert tiles["hearts"].value == 2
    assert tiles["reciprocity"].value == 1.0
    people = {p.person.person_id: p for p in body.people}
    assert people[0].given == 1 and people[0].received == 1
    assert people[0].hearts_received == 2 and people[1].hearts_given == 2
    assert people[1].received_per_100_messages == 100 / 6
    assert body.matrix.values == [[0.0, 1.0], [1.0, 0.0]]


def test_reactions_group(client: TestClient) -> None:
    body = _get(client, 1)
    assert body.most_reacted[0].message.message_id == 7
    assert body.most_reacted[0].count == 2
    assert {r.emoji for r in body.most_reacted[0].reactors} == {"👍", "❤"}
    assert "reciprocity" not in {t.key for t in body.tiles}
    assert body.over_time.x == ["2024-04-01"]


def test_reactions_filters(client: TestClient) -> None:
    full = _get(client, 1)
    early = _get(client, 1, end="2024-04-05")
    assert sum(p.given for p in early.people) < sum(p.given for p in full.people)
    only_eva = _get(client, 1, people="3")
    assert [p.person.person_id for p in only_eva.people] == [3]
    assert only_eva.tiles[0].value == 1


def test_reactions_404(client: TestClient) -> None:
    assert client.get("/api/chats/999/reactions").status_code == 404


def test_reactions_409(empty_client: TestClient) -> None:
    assert empty_client.get("/api/chats/0/reactions").status_code == 409
