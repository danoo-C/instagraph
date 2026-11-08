"""Group tab + social graph analysis with hand-calculated values."""

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from instagraph.analysis import group as g
from instagraph.analysis.group import normalize_name, social_graph
from instagraph.api.filters import FilterParams, filtered_messages, filtered_reactions
from instagraph.schemas.common import PersonRef
from instagraph.store.dataset import Dataset
from instagraph.testing import build_dataset, make_client

PEOPLE = [
    {"display_name": "Me", "is_owner": True},
    {"display_name": "Ann Smith"},
    {"display_name": "Bob"},
    {"display_name": "Cid"},
    {"display_name": "Dan Novák"},
]
THREADS = [{"title": "Crew", "participants": [0, 1, 2, 4], "is_group": True}]
T: dict[str, Any] = {"thread_id": 0}
MESSAGES: list[dict[str, Any]] = [
    {**T, "sender_id": 1, "ts": "2024-05-01T10:00:00Z", "text": "hi all"},  # 0
    {**T, "sender_id": 2, "ts": "2024-05-01T10:01:00Z", "text": "hey @ann"},  # 1 Bob→Ann
    {**T, "sender_id": 2, "ts": "2024-05-01T10:02:00Z", "text": "you there?"},  # 2
    {**T, "sender_id": 1, "ts": "2024-05-01T10:30:00Z", "text": "yes @bob @dan.novak"},  # 3
    {  # 4 join
        **T,
        "sender_id": 0,
        "ts": "2024-05-01T11:00:00Z",
        "system_event": "join",
        "system_payload": "Dan Novak",
    },
    {**T, "sender_id": 4, "ts": "2024-05-01T11:05:00Z", "text": "thanks"},  # 5 Dan→Ann
    {  # 6 rename
        **T,
        "sender_id": 0,
        "ts": "2024-05-03T09:00:00Z",
        "system_event": "rename",
        "system_payload": "New name",
    },
    {**T, "sender_id": 3, "ts": "2024-05-03T09:01:00Z", "text": "ok"},  # 7 (no reply: >12h)
    {  # 8 Cid leaves
        **T,
        "sender_id": 3,
        "ts": "2024-06-10T12:00:00Z",
        "system_event": "leave",
        "system_payload": "Cid",
    },
    {**T, "sender_id": 1, "ts": "2024-06-11T12:00:00Z", "text": "@bob"},  # 9
]
REACTIONS = [
    {"message_id": 0, "actor_id": 2, "emoji": "❤"},
    {"message_id": 0, "actor_id": 3, "emoji": "😂"},
    {"message_id": 3, "actor_id": 2, "emoji": "👍"},
    {"message_id": 5, "actor_id": 1, "emoji": "❤"},
]


def ref(pid: int) -> PersonRef:
    return PersonRef(person_id=pid, name=f"P{pid}", color_index=pid)


@pytest.fixture
def ds() -> Dataset:
    return build_dataset(PEOPLE, THREADS, MESSAGES, REACTIONS)


def test_members_and_lurkers(ds: Dataset) -> None:
    f = FilterParams()
    members = g.member_stats(
        filtered_messages(ds, f), filtered_reactions(ds, f), ref, 0, [0, 1, 2, 4]
    )
    assert [m.person.person_id for m in members] == [1, 2, 3, 4, 0]
    assert [m.messages for m in members] == [3, 2, 1, 1, 0]
    by = {m.person.person_id: m for m in members}
    assert by[1].share == pytest.approx(3 / 7)
    assert by[1].reactions_received == 3 and by[4].reactions_received == 1
    assert by[3].words == 1 and by[4].words == 1
    assert [m.person.person_id for m in g.lurkers(members)] == [0]
    chart = g.leaderboard_chart(members)
    assert chart.series[0].values == [3, 2, 1, 1, 0]


def test_reply_and_reaction_matrices(ds: Dataset) -> None:
    f = FilterParams()
    pairs = g.reply_pairs(filtered_messages(ds, f))
    assert pairs == {(2, 1): 1, (1, 2): 1, (4, 1): 1}
    mat = g.interaction_matrix(pairs, [1, 2, 3, 4, 0], ref)
    assert mat.matrix.values[1][0] == 1  # Bob → Ann
    assert mat.matrix.values[3][0] == 1  # Dan → Ann
    assert sum(map(sum, mat.matrix.values)) == 3
    assert {(link.source, link.target) for link in mat.links} == {(2, 1), (1, 2), (4, 1)}
    only_bob = g.interaction_matrix(pairs, [1, 2], ref, people_filter=[2])
    assert {(link.source, link.target) for link in only_bob.links} == {(2, 1), (1, 2)}
    assert g.reaction_pairs(filtered_reactions(ds, f)) == {(2, 1): 2, (3, 1): 1, (1, 4): 1}


def test_events_membership_and_history(ds: Dataset) -> None:
    events_lf = filtered_messages(ds, FilterParams(exclude_system=False))
    names = {1: ["Ann Smith"], 2: ["Bob"], 3: ["Cid"], 4: ["Dan Novák"]}
    index = g.name_index(names)
    membership = g.build_events(
        g.system_events(events_lf, g.MEMBERSHIP_EVENTS), ref, lambda s: s, index, True
    )
    assert [(e.event, e.member and e.member.person_id) for e in membership] == [
        ("join", 4),
        ("leave", 3),
    ]
    timeline = {
        t.person.person_id: t.intervals for t in g.membership_intervals(membership, [1, 3, 4], ref)
    }
    assert timeline[1][0].start is None and timeline[1][0].end is None
    assert timeline[4][0].start == datetime.fromisoformat("2024-05-01T11:00:00+00:00")
    assert timeline[4][0].end is None
    assert timeline[3][0].start is None
    assert timeline[3][0].end == datetime.fromisoformat("2024-06-10T12:00:00+00:00")
    history = g.build_events(
        g.system_events(events_lf, g.HISTORY_EVENTS), ref, lambda s: None, index, False
    )
    assert [(e.event, e.actor.person_id, e.value) for e in history] == [("rename", 0, None)]


def test_mentions(ds: Dataset) -> None:
    names = {0: ["Me"], 1: ["Ann Smith"], 2: ["Bob"], 3: ["Cid"], 4: ["Dan Novák"]}
    counts = g.mention_counts(filtered_messages(ds, FilterParams()), g.mention_index(names))
    assert counts == {2: 2, 1: 1, 4: 1}
    chart = g.mentions_chart(counts, ref, 0)
    assert chart.categories == ["P2", "P1", "P4"]
    assert normalize_name("Dan Novák") == "dannovak"


def test_mention_first_name_ambiguous() -> None:
    idx = g.mention_index({1: ["Jana Kova"], 2: ["Jana Bela"]})
    assert "jana" not in idx and idx["janakova"] == 1


def test_activity_stream(ds: Dataset) -> None:
    ts = g.activity_stream(filtered_messages(ds, FilterParams()), ref, "month", top_n=2)
    assert ts.x == ["2024-05-01", "2024-06-01"]
    assert [(s.key, s.values) for s in ts.series] == [
        ("1", [2, 1]),
        ("2", [2, 0]),
        ("others", [2, 0]),
    ]


def test_group_endpoint(tmp_path: Path, ds: Dataset) -> None:
    with make_client(tmp_path, ds) as client:
        r = client.get("/api/chats/0/group").json()
        assert [m["person"]["person_id"] for m in r["members"]] == [1, 2, 3, 4, 0]
        assert {e["event"] for e in r["membership_events"]} == {"join", "leave"}
        assert r["history"][0]["value"] == "New name"
        assert r["mentions"]["series"][0]["values"] == [2, 1, 1]
        r = client.get("/api/chats/0/group", params={"people": "2"}).json()
        assert {(x["source"], x["target"]) for x in r["replies"]["links"]} == {(2, 1), (1, 2)}
        assert client.get("/api/chats/7/group").status_code == 404


GRAPH_THREADS = [
    {"title": "G1", "participants": [0, 1, 2, 3]},
    {"title": "G2", "participants": [0, 1, 2]},
    {"title": "Dan", "participants": [0, 4]},
]
GRAPH_MESSAGES = [
    {"thread_id": 0, "sender_id": 1, "ts": "2024-01-01T10:00:00Z", "text": "a"},
    {"thread_id": 0, "sender_id": 1, "ts": "2024-01-01T10:01:00Z", "text": "b"},
    {"thread_id": 0, "sender_id": 3, "ts": "2024-01-01T10:02:00Z", "text": "c"},
    {"thread_id": 1, "sender_id": 2, "ts": "2024-01-02T10:00:00Z", "text": "d"},
    {"thread_id": 2, "sender_id": 4, "ts": "2024-01-03T10:00:00Z", "text": "e"},
    {"thread_id": 2, "sender_id": 4, "ts": "2024-01-03T10:01:00Z", "text": "f"},
    {"thread_id": 2, "sender_id": 4, "ts": "2024-01-03T10:02:00Z", "text": "g"},
    {"thread_id": 2, "sender_id": 0, "ts": "2024-01-03T10:03:00Z", "text": "h"},
]


def test_social_graph() -> None:
    ds = build_dataset(PEOPLE, GRAPH_THREADS, GRAPH_MESSAGES)
    nodes, edges = social_graph(
        filtered_messages(ds, FilterParams()), ds.threads_df(), ref, owner_id=0
    )
    assert [(n.person.person_id, n.messages, n.group_chats) for n in nodes] == [
        (4, 3, 0),
        (1, 2, 2),
        (2, 1, 2),
        (3, 1, 1),
    ]
    assert [(e.source, e.target, e.weight, e.thread_ids) for e in edges] == [
        (1, 2, 2, [0, 1]),
        (1, 3, 1, [0]),
        (2, 3, 1, [0]),
    ]
    nodes, edges = social_graph(
        filtered_messages(ds, FilterParams(include_groups=False)),
        ds.threads_df(),
        ref,
        owner_id=0,
    )
    assert [n.person.person_id for n in nodes] == [4] and edges == []
    nodes, edges = social_graph(
        filtered_messages(ds, FilterParams()), ds.threads_df(), ref, owner_id=0, include_owner=True
    )
    assert {(e.source, e.target) for e in edges if 0 in (e.source, e.target)} == {
        (0, 1),
        (0, 2),
        (0, 3),
    }


def test_graph_endpoint(tmp_path: Path) -> None:
    ds = build_dataset(PEOPLE, GRAPH_THREADS, GRAPH_MESSAGES)
    with make_client(tmp_path, ds) as client:
        r = client.get("/api/overview/graph", params={"max_nodes": 2}).json()
        assert [n["person"]["person_id"] for n in r["nodes"]] == [4, 1]
        assert r["edges"] == []
