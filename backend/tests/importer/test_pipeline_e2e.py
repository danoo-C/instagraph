"""End-to-end ``run_import`` on generated fake exports (needs the parser + generator)."""

import importlib.util
import json
import threading
import zipfile
from pathlib import Path

import polars as pl
import pytest

from instagraph.importer.pipeline import (
    ImportCancelled,
    ImportFailed,
    ProgressEvent,
    run_import,
    run_import_full,
)
from instagraph.store.app_settings import AppSettings
from instagraph.testing import derive_conversation_fields

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("instagraph.importer.parser") is None
    or importlib.util.find_spec("instagraph.sample.generator") is None,
    reason="parser or sample generator not available",
)


@pytest.fixture(scope="module")
def export_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from instagraph.sample.generator import generate_export

    return generate_export(tmp_path_factory.mktemp("exp"), messages=3000, seed=3)


def test_folder_import(export_root: Path) -> None:
    events: list[ProgressEvent] = []
    ds = run_import(
        [export_root],
        settings=AppSettings(),
        progress=events.append,
        cancel=threading.Event(),
        workers=1,
    )
    report = ds.import_report
    counts = ds.counts()
    assert counts["messages"] == report["messages"] > 2500
    assert report["owner"] == {
        "name": "Daniel",
        "person_id": ds.owner_id(),
        "method": "personal_information",
        "auto_name": "Daniel",
        "auto_method": "personal_information",
        "personal_information_name": "Daniel",
    }
    assert report["layouts"] == ["your_instagram_activity"]
    assert report["files"] > 0
    assert report["locales"]["enabled"]
    assert set(report["durations_ms"]) >= {"discovery", "locales", "parse", "merge", "derive"}
    assert report["earliest"] < report["latest"]
    json.dumps(report, default=str)  # serializable

    msgs = ds.messages.collect()
    assert msgs["message_id"].to_list() == list(range(msgs.height))
    assert msgs["ts"].is_sorted()
    threads = ds.threads_df()
    assert threads["message_count"].sum() == msgs.height
    assert set(threads["category"].cast(pl.String).unique()) >= {"inbox", "request"}

    # derived conversation fields equal the reference
    rows = [
        {
            "message_id": r["message_id"],
            "thread_id": r["thread_id"],
            "sender_id": r["sender_id"],
            "ts_ms": int(r["ts"].timestamp() * 1000),
            "type": r["type"],
        }
        for r in msgs.select("message_id", "thread_id", "sender_id", "ts", "type").iter_rows(
            named=True
        )
    ]
    derive_conversation_fields(rows)
    got = msgs.select("session_id", "is_session_start", "turn_id", "reply_latency_ms").rows()
    exp = [
        (r["session_id"], r["is_session_start"], r["turn_id"], r["reply_latency_ms"]) for r in rows
    ]
    assert got == exp

    # every reaction points at an existing message and person
    reactions = ds.reactions.collect()
    assert reactions.height > 0
    assert reactions["message_id"].is_in(msgs["message_id"].implode()).all()
    assert reactions["actor_id"].is_in(ds.people_df()["person_id"].implode()).all()
    stages = [e.stage for e in events]
    assert stages[0] == "discovery"
    assert "parse" in stages and stages[-1] == "derive"
    parse_events = [e for e in events if e.stage == "parse" and e.total]
    assert parse_events[-1].done == parse_events[-1].total
    assert parse_events[-1].messages > 0


def test_zip_import_with_pool_matches_folder(export_root: Path, tmp_path: Path) -> None:
    zpath = tmp_path / "export.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        for f in sorted(export_root.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(export_root).as_posix())
    a = run_import_full(
        [export_root], settings=AppSettings(), progress=lambda e: None, workers=1
    ).dataset
    b = run_import_full([zpath], settings=AppSettings(), progress=lambda e: None, workers=2).dataset
    assert a.counts() == b.counts()
    cols = ["message_id", "thread_id", "sender_id", "ts", "type", "text", "session_id"]
    assert a.messages.collect().select(cols).equals(b.messages.collect().select(cols))
    assert b.import_report["zip_files"] == [str(zpath.resolve())]


def test_errors(tmp_path: Path) -> None:
    with pytest.raises(ImportFailed) as err:
        run_import_full([tmp_path / "missing"], settings=AppSettings(), progress=lambda e: None)
    assert err.value.code == "path_not_found"

    with pytest.raises(ImportFailed) as err:
        run_import_full([tmp_path], settings=AppSettings(), progress=lambda e: None)
    assert err.value.code == "no_threads"

    html = tmp_path / "html" / "messages" / "inbox" / "jana_1"
    html.mkdir(parents=True)
    (html / "message_1.html").write_text("<html></html>")
    with pytest.raises(ImportFailed) as err:
        run_import_full([tmp_path / "html"], settings=AppSettings(), progress=lambda e: None)
    assert err.value.code == "html_export"

    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../evil/messages/inbox/x_1/message_1.json", "{}")
    with pytest.raises(ImportFailed) as err:
        run_import_full([bad], settings=AppSettings(), progress=lambda e: None)
    assert err.value.code == "unsafe_zip"


def test_cancel(export_root: Path) -> None:
    cancel = threading.Event()

    def progress(e: ProgressEvent) -> None:
        if e.stage == "parse" and e.done >= 1:
            cancel.set()

    with pytest.raises(ImportCancelled):
        run_import_full([export_root], settings=AppSettings(), progress=progress, cancel=cancel)


def test_locale_override(export_root: Path) -> None:
    ds = run_import_full(
        [export_root],
        settings=AppSettings(enabled_locales=["en"], owner_name="Nobody Known"),
        progress=lambda e: None,
    ).dataset
    assert ds.import_report["locales"]["mode"] == "settings"
    assert ds.import_report["locales"]["enabled"] == ["en"]
    assert ds.import_report["owner"]["method"] == "personal_information"


@pytest.mark.parametrize(("messages", "seed"), [(1500, 5), (3000, 7)])
def test_generated_sample_system_messages_recognised(
    tmp_path: Path, messages: int, seed: int
) -> None:
    from instagraph.sample.generator import generate_export

    root = generate_export(tmp_path / "x", messages=messages, seed=seed)
    res = run_import_full([root], settings=AppSettings(), progress=lambda e: None, workers=1)
    report = res.dataset.import_report
    assert report["unmatched_system_like"] == []
    assert "sk" in report["locales"]["enabled"] and "cs" not in report["locales"]["enabled"]
