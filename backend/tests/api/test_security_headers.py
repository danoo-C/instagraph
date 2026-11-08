"""DNS-rebinding (Host allow-list) and CSRF (Origin / Sec-Fetch-Site) protection, §10."""

from pathlib import Path

import pytest

from instagraph.testing import make_client


@pytest.fixture
def client(tmp_path: Path):  # type: ignore[no-untyped-def]
    with make_client(tmp_path) as c:
        yield c


def test_bad_host_rejected(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/status", headers={"Host": "evil.example.com:8000"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_host"


@pytest.mark.parametrize(
    "host", ["127.0.0.1:8000", "localhost:5173", "localhost", "[::1]:8000", "LOCALHOST:1"]
)
def test_local_hosts_allowed(client, host: str) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/api/status", headers={"Host": host}).status_code == 200


EVIL = {"Origin": "https://evil.example.com"}


def test_cross_site_upload_rejected(client) -> None:  # type: ignore[no-untyped-def]
    r = client.post(
        "/api/import/upload", files={"file": ("x.zip", b"PK", "application/zip")}, headers=EVIL
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden_origin"


def test_cross_site_delete_and_put_rejected(client) -> None:  # type: ignore[no-untyped-def]
    assert client.delete("/api/data", headers=EVIL).status_code == 403
    assert client.put("/api/settings", json={}, headers=EVIL).status_code == 403
    assert client.request("DELETE", "/api/data", headers={"Origin": "null"}).status_code == 403


def test_sec_fetch_site_cross_site_rejected(client) -> None:  # type: ignore[no-untyped-def]
    r = client.delete("/api/data", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_rebinding_origin_matching_bad_host_rejected(client) -> None:  # type: ignore[no-untyped-def]
    r = client.delete(
        "/api/data", headers={"Host": "evil.example.com", "Origin": "http://evil.example.com"}
    )
    assert r.status_code == 400


def test_dev_origin_allowed(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from instagraph.config import Settings
    from instagraph.main import create_app

    app = create_app(
        Settings(
            data_dir=tmp_path,
            frontend_dist=tmp_path / "_none",
            cors_origins=["http://localhost:5173"],
        )
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        r = c.delete(
            "/api/data",
            headers={"Origin": "http://localhost:5173", "Sec-Fetch-Site": "same-site"},
        )
        assert r.status_code != 403
        # Vite proxy (changeOrigin: false): Host and Origin are both 127.0.0.1:5173.
        r = c.delete(
            "/api/data", headers={"Host": "127.0.0.1:5173", "Origin": "http://127.0.0.1:5173"}
        )
        assert r.status_code != 403


def test_same_origin_and_no_origin_allowed(client) -> None:  # type: ignore[no-untyped-def]
    r = client.delete(
        "/api/data",
        headers={
            "Host": "127.0.0.1:8000",
            "Origin": "http://127.0.0.1:8000",
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert r.status_code not in (400, 403)
    assert client.delete("/api/data").status_code not in (400, 403)
    assert client.put("/api/settings", json={}).status_code != 403
