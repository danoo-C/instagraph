"""Media & links analysis (spec §6.8) with hand-calculated values."""

from datetime import date
from typing import Any

import pytest

from instagraph.analysis import media as m
from instagraph.api.filters import FilterParams, filtered_messages
from instagraph.schemas.common import PersonRef
from instagraph.store.dataset import Dataset
from instagraph.testing import build_dataset

PEOPLE = [{"display_name": "Me", "is_owner": True}, {"display_name": "Jana"}]
THREADS = [{"title": "Jana", "participants": [0, 1]}]
T: dict[str, Any] = {"thread_id": 0}
MESSAGES: list[dict[str, Any]] = [
    {**T, "sender_id": 0, "ts": "2024-01-05T10:00:00Z", "n_photos": 2},  # 0 photo
    {  # 1 link (2 urls)
        **T,
        "sender_id": 1,
        "ts": "2024-01-06T10:00:00Z",
        "text": "see https://www.example.com/a and http://foo.org/b",
    },
    {  # 2 reel
        **T,
        "sender_id": 1,
        "ts": "2024-01-07T10:00:00Z",
        "share_link": "https://instagram.com/reel/1",
        "share_kind": "reel",
        "share_owner": "funny",
    },
    {  # 3 post
        **T,
        "sender_id": 0,
        "ts": "2024-02-01T10:00:00Z",
        "share_link": "https://instagram.com/p/2",
        "share_kind": "post",
        "share_owner": "funny",
    },
    {  # 4 external share
        **T,
        "sender_id": 0,
        "ts": "2024-02-02T10:00:00Z",
        "share_link": "https://news.site.com/x",
        "share_kind": "external",
    },
    {**T, "sender_id": 1, "ts": "2024-02-03T10:00:00Z", "n_audios": 1},  # 5 voice note
    {**T, "sender_id": 0, "ts": "2024-02-04T10:00:00Z", "is_unsent": True},  # 6 unsent
    {  # 7 story
        **T,
        "sender_id": 1,
        "ts": "2024-03-01T10:00:00Z",
        "share_link": "https://instagram.com/stories/3",
        "share_kind": "story",
        "share_owner": "other",
    },
    {**T, "sender_id": 0, "ts": "2024-03-02T10:00:00Z", "n_videos": 1},  # 8 video
    {**T, "sender_id": 1, "ts": "2024-03-03T10:00:00Z", "text": "www.example.com again"},  # 9
    {**T, "sender_id": 1, "ts": "2024-03-04T10:00:00Z", "type": "like"},  # 10 like
]
MEDIA: list[dict[str, Any]] = [
    {"message_id": 0, "kind": "photo", "exists": True},
    {"message_id": 0, "kind": "photo", "exists": True},
    {"message_id": 5, "kind": "audio", "exists": True},
    {"message_id": 8, "kind": "video", "exists": False},
]


def ref(pid: int) -> PersonRef:
    return PersonRef(person_id=pid, name=f"P{pid}", color_index=pid)


@pytest.fixture
def ds() -> Dataset:
    return build_dataset(PEOPLE, THREADS, MESSAGES, media=MEDIA)


def test_person_counts(ds: Dataset) -> None:
    rows = m.build_person_counts(filtered_messages(ds, FilterParams()), ref, 0, [0, 1])
    me, jana = rows
    assert me.person.person_id == 0
    assert (me.photos, me.videos, me.voice_notes) == (2, 1, 0)
    assert (me.shared_posts, me.shared_reels, me.external_links, me.unsent) == (1, 0, 1, 1)
    assert (jana.voice_notes, jana.shared_reels, jana.shared_stories) == (1, 1, 1)
    assert (jana.external_links, jana.unsent, jana.photos) == (3, 0, 0)
    chart = m.counts_chart(rows)
    assert chart.categories[0] == "Photos"
    assert chart.series[0].values[0] == 2


def test_zero_row_for_silent_participant(ds: Dataset) -> None:
    f = FilterParams(people=(1,))
    rows = m.build_person_counts(filtered_messages(ds, f), ref, 0, [1])
    assert [r.person.person_id for r in rows] == [1]


def test_top_accounts_and_domains(ds: Dataset) -> None:
    msgs = filtered_messages(ds, FilterParams())
    accounts = m.top_shared_accounts(msgs, 0)
    assert [(a.key, a.count) for a in accounts] == [("funny", 2), ("other", 1)]
    assert accounts[0].drilldown.share_owner == "funny"
    domains = m.top_link_domains(msgs, 0)
    assert [(d.key, d.count) for d in domains] == [
        ("example.com", 2),
        ("foo.org", 1),
        ("news.site.com", 1),
    ]


def test_type_mix(ds: Dataset) -> None:
    ts = m.type_mix(filtered_messages(ds, FilterParams()))
    assert ts.x == ["2024-01-01", "2024-02-01", "2024-03-01"]
    v = {s.key: s.values for s in ts.series}
    third = pytest.approx(1 / 3)
    assert v["photo"] == [third, 0, 0]
    assert v["text"] == [third, 0, third]
    assert v["share"] == [third, 0.5, third]
    assert v["audio"] == [0, 0.25, 0]
    assert v["other"] == [0, 0.25, 0]
    assert v["video"] == [0, 0, third]
    for i in range(3):
        assert sum(s.values[i] or 0 for s in ts.series) == pytest.approx(1)


def test_reels_exchange(ds: Dataset) -> None:
    ts = m.reels_exchange(filtered_messages(ds, FilterParams()), ref, "month", [0, 1])
    assert [s.key for s in ts.series] == ["0", "1"]
    assert ts.series[0].values == [0, 1]
    assert ts.series[1].values == [1, 0]
    assert ts.x == ["2024-01-01", "2024-02-01"]


def test_voice_notes(ds: Dataset) -> None:
    msgs = filtered_messages(ds, FilterParams())
    vn = m.voice_notes(msgs, ds.media, ref, 0, {2: 3.5}, "ready", [0, 1])
    assert (vn.count, vn.with_duration, vn.total_ms, vn.avg_ms) == (1, 1, 3500, 3500)
    assert [p.person.person_id for p in vn.per_person] == [1]
    vn = m.voice_notes(msgs, ds.media, ref, 0, {}, "pending")
    assert vn.total_ms is None and vn.count == 1


def test_gallery_pagination_and_filters(ds: Dataset) -> None:
    msgs = filtered_messages(ds, FilterParams())
    page = m.gallery(msgs, ds.media, ref, limit=2)
    assert [i.media_id for i in page.items] == [0, 1]
    assert page.total == 3 and page.next_cursor == "2" and page.years == [2024]
    assert page.items[0].url == "/api/media/0"
    page2 = m.gallery(msgs, ds.media, ref, limit=2, cursor=page.next_cursor)
    assert [i.media_id for i in page2.items] == [3]
    assert page2.next_cursor is None
    assert page2.items[0].exists is False and page2.items[0].url is None
    assert m.gallery(msgs, ds.media, ref, sender=1).total == 0
    assert m.gallery(msgs, ds.media, ref, year=2023).total == 0
    assert m.gallery(msgs, ds.media, ref, kinds=["audio"]).total == 1
    # date filter honored
    feb = filtered_messages(ds, FilterParams(start=date(2024, 2, 1)))
    assert m.gallery(feb, ds.media, ref).total == 1
