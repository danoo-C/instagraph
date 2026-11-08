"""API tests for ``/api/chats/{id}/dynamics`` and ``/api/overview/reply-times``."""

from fastapi.testclient import TestClient

from instagraph.schemas.dynamics import DynamicsResponse, ReplyTimesOverviewResponse


def test_dynamics_valid(client: TestClient) -> None:
    resp = client.get("/api/chats/0/dynamics")
    assert resp.status_code == 200
    body = DynamicsResponse.model_validate(resp.json())
    assert body.chat_id == 0 and body.session_gap_ms == 3_600_000
    # thread 0 sessions (60 min gap, a gap of exactly 60 min stays in the session):
    # [0,1,2,4] / [5,6] / [12] / [14,15,16,17] / [18]
    assert body.sessions.count == 5
    assert sum(p.sessions_started for p in body.people) == 5
    assert abs(sum(p.initiation_rate for p in body.people) - 1) < 1e-9
    assert [p.person.name for p in body.people] == ["Me", "Jana Nováková"]
    assert body.longest_wait is not None and body.longest_wait.latency_ms == 38_700_000
    assert len(body.reply_histogram.categories) == len(body.reply_histogram_edges_ms)
    assert body.reply_histogram_edges_ms[-1] == 43_200_000
    assert len(body.reply_median_by_hour.categories) == 24
    assert body.initiators_over_time.x == ["2024-03-01", "2024-04-01"]
    assert {e.person.person_id for e in body.effort} == {0, 1}
    assert all(len(e.parts) == 4 for e in body.effort)
    assert sum(t.messages for t in body.turns) == 12  # counted messages in thread 0


def test_dynamics_group_and_request_chat(client: TestClient) -> None:
    group = DynamicsResponse.model_validate(client.get("/api/chats/1/dynamics").json())
    assert {p.person.person_id for p in group.people} == {0, 1, 2, 3}
    # request chats are opened explicitly, so category filters don't hide them
    req = DynamicsResponse.model_validate(client.get("/api/chats/2/dynamics").json())
    assert req.sessions.count == 1


def test_dynamics_filters_change_results(client: TestClient) -> None:
    full = DynamicsResponse.model_validate(client.get("/api/chats/0/dynamics").json())
    part = DynamicsResponse.model_validate(
        client.get("/api/chats/0/dynamics", params={"end": "2024-03-31"}).json()
    )
    assert part.sessions.count < full.sessions.count
    only_jana = DynamicsResponse.model_validate(
        client.get("/api/chats/0/dynamics", params={"people": "1"}).json()
    )
    assert [p.person.person_id for p in only_jana.people] == [1]
    # structure still uses both sides: Jana's reply stats are unchanged
    jana_full = next(r for r in full.replies if r.person.person_id == 1)
    assert only_jana.replies[0] == jana_full


def test_dynamics_starters(client: TestClient) -> None:
    body = client.get("/api/chats/0/dynamics").json()
    texts = [s["text"] for s in body["starters"]]
    assert "Ahoj, ako sa máš? 😂" in texts and "dobré ráno" in texts


def test_dynamics_404(client: TestClient) -> None:
    resp = client.get("/api/chats/999/dynamics")
    assert resp.status_code == 404 and resp.json()["error"]["code"] == "not_found"


def test_dynamics_409(empty_client: TestClient) -> None:
    resp = empty_client.get("/api/chats/0/dynamics")
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "no_data"
    assert empty_client.get("/api/overview/reply-times").status_code == 409


def test_overview_reply_times(client: TestClient) -> None:
    body = ReplyTimesOverviewResponse.model_validate(client.get("/api/overview/reply-times").json())
    assert body.owner is not None and body.owner.is_owner
    me = body.over_time.series[0]
    assert me.key == "me" and body.over_time.x == ["2024-03-01", "2024-04-01"]
    # my replies: 2 min, 10.75 h (Mar) / 1 h 59 min (group), 27 min (Apr)
    assert me.values[0] == (120_000 + 38_700_000) / 2
    weekly = ReplyTimesOverviewResponse.model_validate(
        client.get("/api/overview/reply-times", params={"bucket": "week"}).json()
    )
    assert weekly.over_time.bucket == "week" and weekly.over_time.x == ["2024-03-25", "2024-04-01"]
    no_groups = ReplyTimesOverviewResponse.model_validate(
        client.get("/api/overview/reply-times", params={"include_groups": "false"}).json()
    )
    assert no_groups.tiles[0].value != body.tiles[0].value
