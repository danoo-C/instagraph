"""Import API: path / upload / sample imports, SSE progress, cancel, report, uploads."""

import importlib.util
import json
import threading
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from instagraph.importer.jobs import ImportJob, get_job_manager
from instagraph.testing import make_client

needs_parser = pytest.mark.skipif(
    importlib.util.find_spec("instagraph.importer.parser") is None
    or importlib.util.find_spec("instagraph.sample.generator") is None,
    reason="parser or sample generator not available",
)


@pytest.fixture(autouse=True)
def _single_worker() -> Iterator[None]:
    manager = get_job_manager()
    manager.workers = 1
    yield
    active = manager.active_job()
    if active is not None:  # never leak a running job into the next test
        active.cancel_event.set()
        _wait_job(active)
    manager.workers = None


def _wait_job(job: ImportJob, timeout: float = 60) -> None:
    end = time.monotonic() + timeout
    while job.active and time.monotonic() < end:
        time.sleep(0.02)


def wait_done(client: TestClient, job_id: str, timeout: float = 60) -> dict[str, Any]:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        state: dict[str, Any] = client.get(f"/api/import/jobs/{job_id}").json()
        if state["status"] not in ("running", "uploading"):
            return state
        time.sleep(0.05)
    raise AssertionError("import did not finish")


@pytest.fixture(scope="module")
def export_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from instagraph.sample.generator import generate_export

    return generate_export(
        tmp_path_factory.mktemp("exp"), messages=3000, seed=3, with_media_files=True
    )


def read_sse(client: TestClient, url: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    with client.stream("GET", url) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        event = "message"
        for line in resp.iter_lines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                events.append((event, json.loads(line.split(":", 1)[1].strip())))
                if event in ("done", "error", "cancelled"):
                    break
    return events


def test_report_requires_data(empty_client: TestClient) -> None:
    resp = empty_client.get("/api/import/report")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "no_data"


def test_unknown_job(empty_client: TestClient) -> None:
    assert empty_client.get("/api/import/jobs/nope").status_code == 404
    assert empty_client.delete("/api/import/jobs/nope").status_code == 404
    assert empty_client.get("/api/import/jobs/nope/events").status_code == 404


def test_path_validation(empty_client: TestClient, tmp_path: Path) -> None:
    cases = {
        "relative/path": "path_not_absolute",
        str(tmp_path / "missing"): "path_not_found",
    }
    txt = tmp_path / "notes.txt"
    txt.write_text("x")
    cases[str(txt)] = "unsupported_path"
    for path, code in cases.items():
        resp = empty_client.post("/api/import/path", json={"path": path})
        assert resp.status_code == 400, path
        assert resp.json()["error"]["code"] == code
    assert empty_client.post("/api/import/path", json={}).status_code == 422


@needs_parser
def test_path_import_with_sse_and_report(tmp_path: Path, export_root: Path) -> None:
    with make_client(tmp_path) as client:
        resp = client.post("/api/import/path", json={"path": str(export_root)})
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]
        events = read_sse(client, f"/api/import/jobs/{job_id}/events")
        assert events[-1][0] == "done"
        final = events[-1][1]
        assert final["status"] == "done" and final["error"] is None
        assert final["dataset_version"] >= 1
        assert all(e == "progress" for e, _ in events[:-1])

        status = client.get("/api/status").json()
        assert status["has_data"]
        assert status["owner"]["name"] == "Daniel"

        report = client.get("/api/import/report").json()
        assert report["dataset_version"] == status["dataset_version"]
        assert report["kind"] == "path"
        assert report["messages"] == status["counts"]["messages"]
        assert report["owner"]["method"] == "personal_information"
        assert report["layouts"] == ["your_instagram_activity"]
        assert report["locales"]["enabled"]
        assert report["files"] > 0 and report["threads"] > 0
        assert "parse" in report["durations_ms"]
        assert report["earliest"] and report["latest"]
        assert report["media"] > 0 and report["media_found"] > 0
        assert not report["kept_uploads"]

        # SSE for a finished job: just the final event
        again = read_sse(client, f"/api/import/jobs/{job_id}/events")
        assert [e for e, _ in again] == ["done"]

        # anonymize hides names / texts
        settings = client.get("/api/settings")
        if settings.status_code == 200:
            body = settings.json()
            body["anonymize"] = True
            if client.put("/api/settings", json=body).status_code == 200:
                anon = client.get("/api/import/report").json()
                assert anon["owner"]["name"] == "Me"
                assert anon["unmatched_system_like"] == []

        # persisted + raw sidecar written
        assert (tmp_path / "dataset" / "messages.parquet").is_file()
        assert (tmp_path / "import_raw" / "sources.json").is_file()


@needs_parser
def test_upload_keep_and_delete_uploads(tmp_path: Path, export_root: Path) -> None:
    zbytes = tmp_path / "src.zip"
    with zipfile.ZipFile(zbytes, "w") as zf:
        for f in sorted(export_root.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(export_root).as_posix())
    data_dir = tmp_path / "data"
    with make_client(data_dir) as client, zbytes.open("rb") as fh:
        resp = client.post(
            "/api/import/upload",
            files=[("files", ("../../my export.zip", fh, "application/zip"))],
        )
        assert resp.status_code == 200, resp.text
        job_id = resp.json()["job_id"]
        state = wait_done(client, job_id)
        assert state["status"] == "done", state
        kept = list((data_dir / "uploads" / job_id).glob("*.zip"))
        assert [p.name for p in kept] == ["my export.zip"]  # path parts stripped
        report = client.get("/api/import/report").json()
        assert report["kind"] == "upload" and report["kept_uploads"]
        assert report["media_found"] > 0
        version = report["dataset_version"]

        resp = client.delete("/api/import/uploads")
        assert resp.status_code == 200
        body = resp.json()
        assert body["deleted_files"] == 1
        assert body["freed_bytes"] == zbytes.stat().st_size
        assert body["media_marked_missing"] == report["media_found"]
        assert body["dataset_version"] > version
        assert not (data_dir / "uploads").exists()
        after = client.get("/api/import/report").json()
        assert after["media_found"] == 0 and not after["kept_uploads"]
        assert after["messages"] == report["messages"]  # stats kept


def test_upload_rejects_non_zip(empty_client: TestClient) -> None:
    resp = empty_client.post(
        "/api/import/upload", files=[("files", ("notes.txt", b"hello", "text/plain"))]
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_upload"
    resp = empty_client.post("/api/import/upload", data={"x": "1"})
    assert resp.status_code == 400
    assert get_job_manager().active_job() is None


@needs_parser
def test_sample_import(empty_client: TestClient) -> None:
    resp = empty_client.post("/api/import/sample", json={"messages": 3000, "seed": 3})
    assert resp.status_code == 200
    state = wait_done(empty_client, resp.json()["job_id"])
    assert state["status"] == "done", state
    report = empty_client.get("/api/import/report").json()
    assert report["kind"] == "sample"
    assert report["messages"] == 3000


@needs_parser
def test_html_export_error(empty_client: TestClient, tmp_path: Path) -> None:
    folder = tmp_path / "exp" / "messages" / "inbox" / "jana_1"
    folder.mkdir(parents=True)
    (folder / "message_1.html").write_text("<html></html>")
    resp = empty_client.post("/api/import/path", json={"path": str(tmp_path / "exp")})
    job_id = resp.json()["job_id"]
    events = read_sse(empty_client, f"/api/import/jobs/{job_id}/events")
    assert events[-1][0] == "error"
    assert events[-1][1]["error"]["code"] == "html_export"


def test_conflict_and_cancel(empty_client: TestClient, tmp_path: Path) -> None:
    manager = get_job_manager()
    release = threading.Event()

    def slow_sources(job: ImportJob) -> list[Path]:
        release.wait(10)
        return [tmp_path]

    job = manager.create("path")
    manager.start(job, slow_sources)
    resp = empty_client.post("/api/import/path", json={"path": str(tmp_path)})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "import_running"
    assert empty_client.delete("/api/import/uploads").status_code == 409

    resp = empty_client.delete(f"/api/import/jobs/{job.job_id}")
    assert resp.status_code == 200
    release.set()
    state = wait_done(empty_client, job.job_id)
    assert state["status"] == "cancelled"
    # heartbeat/progress events: a running job emits ≥ 2 events per second
    job2 = manager.create("path")
    release2 = threading.Event()
    manager.start(job2, lambda j: (release2.wait(10), [tmp_path])[1])
    timer = threading.Timer(1.3, release2.set)
    timer.start()
    events = read_sse(empty_client, f"/api/import/jobs/{job2.job_id}/events")
    timer.join()
    progress = [e for e, _ in events if e == "progress"]
    assert len(progress) >= 3
    assert events[-1][0] == "error"
    assert events[-1][1]["error"]["code"] == "no_threads"
