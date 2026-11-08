"""Calls analysis (spec §6.8) with hand-calculated values."""

from pathlib import Path
from typing import Any

from instagraph.analysis.calls import analyze_calls, bin_index
from instagraph.api.filters import FilterParams, filtered_messages
from instagraph.schemas.common import PersonRef
from instagraph.store.dataset import Dataset
from instagraph.testing import build_dataset, make_client

PEOPLE = [{"display_name": "Me", "is_owner": True}, {"display_name": "Jana"}]
THREADS = [{"title": "Jana", "participants": [0, 1]}]
CALL: dict[str, Any] = {"thread_id": 0}
MESSAGES: list[dict[str, Any]] = [
    # Mon 2024-01-01 10:00 UTC, audio 10 min, started by Me
    {
        **CALL,
        "sender_id": 0,
        "ts": "2024-01-01T10:00:00Z",
        "call_kind": "audio",
        "call_duration_s": 600,
    },
    {**CALL, "sender_id": 1, "ts": "2024-01-01T11:00:00Z", "text": "fajn"},
    # Mon 2024-01-15 20:00, video 30 min, started by Jana
    {
        **CALL,
        "sender_id": 1,
        "ts": "2024-01-15T20:00:00Z",
        "call_kind": "video",
        "call_duration_s": 1800,
    },
    # Sat 2024-02-03 08:00, missed audio call from Jana
    {
        **CALL,
        "sender_id": 1,
        "ts": "2024-02-03T08:00:00Z",
        "call_kind": "audio",
        "call_duration_s": 0,
        "call_missed": True,
    },
    # Sun 2024-03-10 10:30, audio 30 s, Me
    {
        **CALL,
        "sender_id": 0,
        "ts": "2024-03-10T10:30:00Z",
        "call_kind": "audio",
        "call_duration_s": 30,
    },
]


def ref(pid: int) -> PersonRef:
    return PersonRef(person_id=pid, name=f"P{pid}", color_index=pid)


def dataset() -> Dataset:
    return build_dataset(PEOPLE, THREADS, MESSAGES)


def test_calls_hand_calculated() -> None:
    msgs = filtered_messages(dataset(), FilterParams())
    r = analyze_calls(msgs, ref, chat_id=0, dataset_version=1, people=[0, 1])
    assert (r.total_calls, r.audio_calls, r.video_calls) == (4, 3, 1)
    assert (r.connected_calls, r.missed_calls) == (3, 1)
    assert r.total_ms == 2_430_000
    assert r.avg_ms == 810_000
    assert r.median_ms == 600_000
    assert r.longest is not None
    assert (r.longest.message_id, r.longest.duration_ms, r.longest.caller.person_id) == (
        2,
        1_800_000,
        1,
    )
    assert r.per_month.x == ["2024-01-01", "2024-02-01", "2024-03-01"]
    calls, minutes = r.per_month.series
    assert calls.values == [2, 1, 1]
    assert minutes.values == [40, 0, 0.5]
    assert r.starters.categories == ["P0", "P1"]
    assert r.starters.series[0].values == [2, 2]
    assert r.missed_per_person.categories == ["P1"]
    assert r.missed_per_person.series[0].values == [1]
    # 30 s → "< 1 min", 600 s → "5–15 min", 1800 s → "30–60 min"
    assert r.duration_histogram.series[0].values == [1, 0, 1, 0, 1, 0, 0]
    heat = r.heatmap.values
    assert heat[0][10] == 1 and heat[0][20] == 1 and heat[5][8] == 1 and heat[6][10] == 1
    assert sum(map(sum, heat)) == 4
    assert [c.message_id for c in r.longest_calls] == [2, 0, 4]
    tiles = {t.key: t for t in r.tiles}
    assert tiles["calls_longest"].detail == "2024-01-15"
    assert tiles["calls_missed"].drilldown is not None
    assert tiles["calls_missed"].drilldown.call_missed is True


def test_bin_edges() -> None:
    assert bin_index(59) == 0
    assert bin_index(60) == 1
    assert bin_index(7200) == 6


def test_no_calls() -> None:
    ds = build_dataset(PEOPLE, THREADS, [MESSAGES[1]])
    r = analyze_calls(filtered_messages(ds, FilterParams()), ref, chat_id=0, dataset_version=1)
    assert r.total_calls == 0
    assert r.avg_ms is None and r.median_ms is None and r.longest is None
    assert r.per_month.x == []


def test_calls_endpoint_honors_filters(tmp_path: Path) -> None:
    with make_client(tmp_path, dataset()) as client:
        r = client.get("/api/chats/0/calls").json()
        assert r["total_calls"] == 4
        r = client.get("/api/chats/0/calls", params={"start": "2024-02-01"}).json()
        assert r["total_calls"] == 2
        assert r["total_ms"] == 30_000
        r = client.get("/api/chats/0/calls", params={"people": "1"}).json()
        assert r["total_calls"] == 2
        assert client.get("/api/chats/42/calls").status_code == 404
