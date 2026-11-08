"""Pipeline back half (merge/dedupe/ids/derive/report) on hand-built ParsedThreads,
plus re-derivation after settings changes."""

from collections import Counter
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from instagraph.importer.pipeline import (
    ImportFailed,
    ProgressEvent,
    affected_steps,
    assemble,
    chunk_from_parsed,
    load_raw,
    rederive,
    save_raw,
)
from instagraph.importer.types import ParsedMedia, ParsedMessage, ParsedReaction, ParsedThread
from instagraph.store.app_settings import AppSettings, PeopleMerge, ThreadSplit
from instagraph.store.dataset import TABLE_SCHEMAS, Dataset, conform
from instagraph.testing import derive_conversation_fields

H = 3_600_000
T0 = 1_700_000_000_000


def msg(sender: str, ts: int, text: str | None = None, **kw: Any) -> ParsedMessage:
    kw.setdefault("type", "text" if text else "unknown")
    return ParsedMessage(sender_name=sender, timestamp_ms=ts, text=text, **kw)


def thread(
    folder: str, participants: list[str], messages: list[ParsedMessage], **kw: Any
) -> ParsedThread:
    kw.setdefault("category", "inbox")
    kw.setdefault("title", None)
    # files store newest first; emulate positions
    for pos, m in enumerate(sorted(messages, key=lambda m: -m.timestamp_ms)):
        m.position = pos
    return ParsedThread(folder=folder, participants=participants, messages=messages, **kw)


def build(threads: list[ParsedThread], settings: AppSettings | None = None, **kw: Any) -> Any:
    chunks = [chunk_from_parsed(t) for t in threads]
    return assemble(chunks, settings=settings or AppSettings(), **kw)


def fixture_threads() -> list[ParsedThread]:
    jana_old = thread(
        "jana_111",
        ["Jana", "Daniel"],
        [
            msg("Jana", T0, "Ahoj 😂😂", reactions=[ParsedReaction("❤", "Daniel", T0 // 1000 + 5)]),
            msg(
                "Daniel",
                T0 + 60_000,
                "čau",
                n_photos=1,
                type="photo",
                media=[ParsedMedia("photo", "messages/inbox/jana_111/photos/1.jpg")],
            ),
            msg("Daniel", T0 + 120_000, None, type="like", system_event=None),
        ],
        title="Jana",
        encoding_fixes=3,
        unknown_keys=Counter({"message.foo": 2}),
    )
    jana_new = thread(
        "jana_222",
        ["Daniel", "Jana"],
        [
            msg("Jana", T0 + 10 * H, "druhý thread"),
            msg("Daniel", T0 + 10 * H + 1000, "áno"),
            msg("Daniel", T0 + 10 * H + 1000, "áno"),  # exact duplicate
            msg("Daniel", T0 + 30 * H, None),  # empty → unknown
        ],
        title="Jana Nováková",
    )
    group = thread(
        "partia_333",
        ["Daniel", "Jana", "Peter", "Instagram User"],
        [
            msg("Peter", T0 + 5 * H, "hello all"),
            msg(
                "Instagram User",
                T0 + 5 * H + 1,
                "ghost here",
                reactions=[
                    ParsedReaction("😂", "Instagram User", None),
                    ParsedReaction("👍🏽", "Peter", None),
                ],
            ),
            msg(
                "Daniel",
                T0 + 5 * H + 2,
                None,
                type="system",
                system_event="rename",
                system_payload="Partia",
            ),
        ],
        title="Partia",
    )
    return [jana_old, jana_new, group]


def test_assemble_tables_and_report() -> None:
    events: list[ProgressEvent] = []
    ds, raw, report = build(
        fixture_threads(),
        personal_name="Daniel",
        media_resolver=lambda uri: f"/exp/{uri}" if uri.endswith("1.jpg") else None,
        progress=events.append,
    )
    people = ds.people_df()
    threads = ds.threads_df()
    messages = ds.messages.collect()
    reactions = ds.reactions.collect()
    media = ds.media.collect()

    for name, df in [
        ("people", people),
        ("threads", threads),
        ("messages", messages),
        ("reactions", reactions),
        ("media", media),
    ]:
        assert df.schema == conform(df, TABLE_SCHEMAS[name]).schema

    # jana_111 + jana_222 merged; group separate
    assert threads.height == 2
    assert report["threads_merged"] == 1
    assert report["duplicates_removed"] == 1
    assert report["empty_messages"] == 1
    assert report["encoding_fixes"] == 3
    assert report["unknown_keys"] == [{"key": "message.foo", "count": 2}]
    assert report["owner"]["method"] == "personal_information"
    assert report["owner"]["name"] == "Daniel"
    assert messages.height == 9

    # message ids ascending by time
    assert messages["message_id"].to_list() == list(range(9))
    assert messages["ts"].is_sorted()

    names = dict(zip(people["person_id"], people["display_name"], strict=True))
    owner = people.filter(pl.col("is_owner"))["display_name"].to_list()
    assert owner == ["Daniel"]
    assert "Instagram User (partia_333)" in names.values()

    jana_thread = threads.filter(pl.col("folder") == "jana_222").row(0, named=True)
    assert jana_thread["title"] == "Jana Nováková"
    assert jana_thread["message_count"] == 6
    assert not jana_thread["is_group"]
    group = threads.filter(pl.col("folder") == "partia_333").row(0, named=True)
    assert group["is_group"]
    assert len(group["participants"]) == 4

    # text metrics
    first = messages.row(0, named=True)
    assert first["text"] == "Ahoj 😂😂"
    assert first["word_count"] == 1 and first["char_count"] == 7
    assert first["emojis"] == ["😂", "😂"]
    system = messages.filter(pl.col("type") == "system").row(0, named=True)
    assert system["word_count"] == 0 and system["session_id"] is None

    # reactions mapped to message ids and actor ids
    assert reactions.height == 3
    r0 = reactions.filter(pl.col("message_id") == 0).row(0, named=True)
    assert names[r0["actor_id"]] == "Daniel"
    assert r0["ts"] is not None
    ghost_r = reactions.filter(pl.col("emoji") == "😂").row(0, named=True)
    assert names[ghost_r["actor_id"]] == "Instagram User (partia_333)"

    # media
    assert media.height == 1
    assert media["exists"].to_list() == [True]
    assert media["source"].to_list() == ["/exp/messages/inbox/jana_111/photos/1.jpg"]

    # conversation fields equal the reference implementation
    ref_rows = [
        {
            "message_id": r["message_id"],
            "thread_id": r["thread_id"],
            "sender_id": r["sender_id"],
            "ts_ms": int(r["ts"].timestamp() * 1000),
            "type": r["type"],
        }
        for r in messages.iter_rows(named=True)
    ]
    derive_conversation_fields(ref_rows)
    for ref, row in zip(ref_rows, messages.iter_rows(named=True), strict=True):
        for col in ("session_id", "is_session_start", "turn_id", "reply_latency_ms"):
            assert ref[col] == row[col], col

    assert report["earliest"].startswith("2023-11-14")
    assert {e.stage for e in events} >= {"merge", "derive"}
    assert raw.messages.height == messages.height
    assert set(raw.messages.columns) == {"message_id", "source_idx", "raw_sender"}


def test_rederive_people_merge_and_split_roundtrip(tmp_path: Path) -> None:
    t1 = thread("jana_1", ["Jana", "Me"], [msg("Jana", T0, "a"), msg("Me", T0 + 1000, "b")])
    t2 = thread(
        "jananovakova_2",
        ["Jana Nováková", "Me"],
        [
            msg("Jana Nováková", T0 + 2000, "c", reactions=[ParsedReaction("❤", "Me", None)]),
            msg("Me", T0 + 3000, "d"),
        ],
    )
    ds, raw, _ = build([t1, t2])
    assert ds.threads_df().height == 2
    assert ds.people_df().height == 3
    save_raw(tmp_path, raw)
    loaded = load_raw(tmp_path, ds.import_report["import_id"])
    assert loaded is not None
    assert load_raw(tmp_path, "other-import") is None

    merged_settings = AppSettings(people_merges=[PeopleMerge(names=["Jana", "Jana Nováková"])])
    assert affected_steps(ds, merged_settings) == {"identity", "conversation", "owner"}
    ds2 = rederive(ds, merged_settings, data_dir=tmp_path)
    people = ds2.people_df()
    assert people.height == 2
    jana = people.filter(pl.col("display_name") == "Jana Nováková").row(0, named=True)
    assert sorted(jana["aliases"]) == ["Jana", "Jana Nováková"]
    # different folder prefixes → still two threads, but same sender id
    msgs = ds2.messages.collect()
    jana_msgs = msgs.filter(pl.col("sender_id") == jana["person_id"])
    assert jana_msgs.height == 2
    assert msgs["message_id"].to_list() == ds.messages.collect()["message_id"].to_list()
    assert ds2.reactions.collect().height == 1
    assert ds2.import_report["derive_settings"]["people_merges"]

    # Split again (remove the merge) → back to 3 people
    ds3 = rederive(ds2, AppSettings(), raw=loaded)
    assert ds3.people_df().height == 3


def test_rederive_session_gap_only_and_owner() -> None:
    t1 = thread(
        "jana_1",
        ["Jana", "Me"],
        [msg("Jana", T0, "a"), msg("Me", T0 + 30 * 60_000, "b"), msg("Jana", T0 + 2 * H, "c")],
    )
    ds, _, report = build([t1])
    assert report["owner"]["method"] == "most_threads"
    s = AppSettings(session_gap_min=10)
    assert affected_steps(ds, s) == {"conversation"}
    ds2 = rederive(ds, s)
    starts = ds2.messages.collect()["is_session_start"].to_list()
    assert starts == [True, True, True]
    assert ds.messages.collect()["is_session_start"].to_list() == [True, False, True]

    s2 = AppSettings(session_gap_min=10, owner_name="Jana")
    assert affected_steps(ds2, s2) == {"owner"}
    ds3 = rederive(ds2, s2)
    owner = ds3.people_df().filter(pl.col("is_owner"))["display_name"].to_list()
    assert owner == ["Jana"]
    assert ds3.import_report["owner"]["method"] == "settings"
    # owner_person_id override (current ids) works too, and clearing falls back to auto
    me_id = ds3.people_df().filter(pl.col("display_name") == "Me")["person_id"].item()
    ds4 = rederive(ds3, AppSettings(session_gap_min=10, owner_person_id=me_id))
    assert ds4.owner_id() == me_id
    ds5 = rederive(ds4, AppSettings(session_gap_min=10))
    assert ds5.import_report["owner"]["method"] == "most_threads"


def test_rederive_without_raw_requires_reimport(tmp_path: Path) -> None:
    t1 = thread("jana_1", ["Jana", "Me"], [msg("Jana", T0, "a")])
    ds, _, _ = build([t1])
    with pytest.raises(ImportFailed) as err:
        rederive(
            ds, AppSettings(thread_splits=[ThreadSplit(folders=["a", "b"])]), data_dir=tmp_path
        )
    assert err.value.code == "reimport_required"


def test_cross_source_dedupe_and_order_ties() -> None:
    # same folder twice (e.g. two zip parts parsed separately): duplicates removed
    a = thread("x_1", ["A", "B"], [msg("A", T0, "one"), msg("B", T0, "two")])
    b = thread("x_1", ["A", "B"], [msg("A", T0, "one"), msg("B", T0 + 5, "three")])
    ds, _, report = build([a, b])
    msgs = ds.messages.collect()
    assert report["duplicates_removed"] == 1
    assert msgs.height == 3
    assert ds.threads_df().height == 1


def test_empty_input() -> None:
    ds, _, report = build([])
    assert isinstance(ds, Dataset)
    assert report["messages"] == 0
    assert ds.counts()["messages"] == 0
