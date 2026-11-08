from fastapi.testclient import TestClient


def test_status_without_data(empty_client: TestClient) -> None:
    body = empty_client.get("/api/status").json()
    assert body["has_data"] is False and body["counts"] is None


def test_status_with_data(client: TestClient) -> None:
    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_data"] and body["counts"]["messages"] == 20
    assert body["owner"]["name"] == "Me"
    assert "default-src 'self'" in resp.headers["content-security-policy"]


def test_delete_data(client: TestClient) -> None:
    before = client.get("/api/status").json()["dataset_version"]
    body = client.delete("/api/data").json()
    assert body["has_data"] is False and body["dataset_version"] > before


def test_error_shapes(client: TestClient) -> None:
    resp = client.get("/api/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
    resp = client.post("/api/status")
    assert resp.status_code == 405 and "error" in resp.json()


def test_context_dependency(tmp_path: object, client: TestClient) -> None:
    # A throwaway route using Context: 409 without data, filters validated.
    from fastapi import APIRouter

    from instagraph.api.deps import Context

    router = APIRouter()

    @router.get("/api/_probe")
    def probe(ctx: Context) -> dict[str, object]:
        n = ctx.cached("probe", lambda: ctx.messages().collect().height)
        return {"n": n, "tz": ctx.tz, "v": ctx.dataset_version}

    app = client.app
    app.include_router(router)  # type: ignore[attr-defined]
    r = client.get("/api/_probe")
    assert r.json()["n"] == 18, r.text
    assert client.get("/api/_probe?tz=Europe/Bratislava").json()["tz"] == "Europe/Bratislava"
    bad = client.get("/api/_probe?tz=Nope/Nope")
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "validation_error"
    bad = client.get("/api/_probe?start=notadate")
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "validation_error"
    client.delete("/api/data")
    nodata = client.get("/api/_probe")
    assert nodata.status_code == 409 and nodata.json()["error"]["code"] == "no_data"
