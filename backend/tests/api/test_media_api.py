"""Media file + thumbnail endpoints: folder and zip sources, 404s, no path traversal."""

import io
import wave
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from instagraph.media.audio import audio_durations_or_start, compute_audio_durations
from instagraph.media.files import MediaUnavailableError, content_type_for, resolve_media
from instagraph.store.dataset import get_store
from instagraph.testing import build_dataset, make_client

PHOTO_URI = "your_instagram_activity/messages/inbox/jana_1/photos/a.jpg"
ZIP_PHOTO = "your_instagram_activity/messages/inbox/jana_1/photos/z.png"
ZIP_AUDIO = "your_instagram_activity/messages/inbox/jana_1/audio/v.wav"


def jpeg_bytes(w: int, h: int, orientation: int | None = None) -> bytes:
    img = Image.new("RGB", (w, h), (200, 30, 30))
    out = io.BytesIO()
    if orientation is None:
        img.save(out, "JPEG")
    else:
        exif = Image.Exif()
        exif[0x0112] = orientation
        img.save(out, "JPEG", exif=exif.tobytes())
    return out.getvalue()


def png_bytes() -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", (64, 32), (0, 0, 255, 128)).save(out, "PNG")
    return out.getvalue()


def wav_bytes(seconds: float = 1.0) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * int(8000 * seconds))
    return out.getvalue()


@pytest.fixture
def export(tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / "export"
    photo = root / PHOTO_URI
    photo.parent.mkdir(parents=True)
    photo_data = jpeg_bytes(400, 300, orientation=6)  # rotated 90° → displayed 300×400
    photo.write_bytes(photo_data)
    (root / "bad.jpg").write_bytes(b"not an image")
    (root / "page.html").write_bytes(b"<script>alert(1)</script>")
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    (root / "link.jpg").symlink_to(secret)

    zpath = tmp_path / "upload.zip"
    zip_png = png_bytes()
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr(ZIP_PHOTO, zip_png)
        zf.writestr(ZIP_AUDIO, wav_bytes(1.0))
        zf.writestr("your_instagram_activity/messages/inbox/jana_1/videos/v.mp4", b"\0" * 10)

    media = [
        {"message_id": 0, "kind": "photo", "uri": PHOTO_URI, "source": str(root), "exists": True},
        {
            "message_id": 0,
            "kind": "photo",
            "uri": PHOTO_URI,
            "source": str(root / PHOTO_URI),
            "exists": True,
        },
        {
            "message_id": 1,
            "kind": "photo",
            "uri": ZIP_PHOTO,
            "source": f"{zpath}!{ZIP_PHOTO}",
            "exists": True,
        },
        {
            "message_id": 2,
            "kind": "audio",
            "uri": ZIP_AUDIO,
            "source": f"{zpath}::",
            "exists": True,
        },
        {
            "message_id": 3,
            "kind": "video",
            "uri": "inbox/jana_1/videos/v.mp4",
            "source": f"{zpath}!",
            "exists": True,
        },
        {"message_id": 3, "kind": "photo", "uri": "x.jpg", "source": str(root), "exists": False},
        {
            "message_id": 3,
            "kind": "photo",
            "uri": "../secret.txt",
            "source": str(root),
            "exists": True,
        },
        {"message_id": 3, "kind": "photo", "uri": "link.jpg", "source": str(root), "exists": True},
        {"message_id": 3, "kind": "photo", "uri": "bad.jpg", "source": str(root), "exists": True},
        {"message_id": 3, "kind": "photo", "uri": "page.html", "source": str(root), "exists": True},
        {"message_id": 3, "kind": "photo", "uri": "gone.jpg", "source": str(root), "exists": True},
        {
            "message_id": 3,
            "kind": "photo",
            "uri": "../../x",
            "source": f"{zpath}!../../x",
            "exists": True,
        },
    ]
    ds = build_dataset(
        [{"display_name": "Me", "is_owner": True}, {"display_name": "Jana"}],
        [{"title": "Jana", "participants": [0, 1]}],
        [
            {"thread_id": 0, "sender_id": 0, "ts": "2024-01-01T10:00:00Z", "n_photos": 2},
            {"thread_id": 0, "sender_id": 1, "ts": "2024-01-02T10:00:00Z", "n_photos": 1},
            {"thread_id": 0, "sender_id": 1, "ts": "2024-01-03T10:00:00Z", "n_audios": 1},
            {"thread_id": 0, "sender_id": 1, "ts": "2024-01-04T10:00:00Z", "n_videos": 1},
        ],
        media=media,
    )
    return {"ds": ds, "photo": photo_data, "zip_png": zip_png, "root": root}


@pytest.fixture
def client(tmp_path: Path, export: dict[str, Any]) -> Any:
    with make_client(tmp_path / "data", export["ds"]) as c:
        yield c


def test_serve_from_folder_and_file(client: TestClient, export: dict[str, Any]) -> None:
    for media_id in (0, 1):
        r = client.get(f"/api/media/{media_id}")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"
        assert r.content == export["photo"]
        assert "sandbox" in r.headers["content-security-policy"]


def test_range_request_on_file(client: TestClient, export: dict[str, Any]) -> None:
    r = client.get("/api/media/0", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206
    assert r.content == export["photo"][:10]


def test_serve_from_zip(client: TestClient, export: dict[str, Any]) -> None:
    r = client.get("/api/media/2")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == export["zip_png"]
    r = client.get("/api/media/3")  # empty member → uri; "::" separator
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/")
    r = client.get("/api/media/4")  # uri shorter than the member name → suffix match
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/mp4"
    assert r.content == b"\0" * 10


def test_not_found_cases(client: TestClient) -> None:
    assert client.get("/api/media/999").status_code == 404
    assert client.get("/api/media/5").status_code == 404  # exists = false
    assert client.get("/api/media/10").status_code == 404  # file vanished
    body = client.get("/api/media/999").json()
    assert body["error"]["code"] == "not_found"
    assert client.get("/api/media/5/thumb").status_code == 404


def test_path_traversal_impossible(client: TestClient) -> None:
    assert client.get("/api/media/6").status_code == 404  # ../ in uri
    assert client.get("/api/media/7").status_code == 404  # symlink out of the folder
    assert client.get("/api/media/11").status_code == 404  # zip slip member name
    for path in (
        "/api/media/..%2F..%2Fsecret.txt",
        "/api/media/../../secret.txt",
        "/api/media/abc",
    ):
        r = client.get(path)
        assert r.status_code in (404, 422)
        assert b"top secret" not in r.content


def test_active_content_not_served_as_html(client: TestClient) -> None:
    r = client.get("/api/media/9")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert content_type_for("x.svg", "photo") == "application/octet-stream"
    assert content_type_for("noext", "video") == "video/mp4"


def test_thumbnails(client: TestClient, tmp_path: Path) -> None:
    r = client.get("/api/media/0/thumb", params={"size": 128})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    img = Image.open(io.BytesIO(r.content))
    assert img.size == (96, 128)  # EXIF orientation applied (portrait)
    cached = list((tmp_path / "data" / "thumbs").glob("0_128_*.jpg"))
    assert len(cached) == 1
    assert client.get("/api/media/0/thumb", params={"size": 128}).content == r.content
    r = client.get("/api/media/2/thumb")  # PNG with alpha from zip
    assert r.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(r.content)).size == (64, 32)
    r = client.get("/api/media/4/thumb")  # video → placeholder
    assert r.headers["content-type"].startswith("image/svg+xml")
    r = client.get("/api/media/8/thumb")  # undecodable image → placeholder
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert client.get("/api/media/0/thumb", params={"size": 5000}).status_code == 422


def test_resolve_media_unit(export: dict[str, Any]) -> None:
    root = export["root"]
    deeper = root / "your_instagram_activity" / "messages"
    assert resolve_media(str(deeper), PHOTO_URI).kind == "file"  # folder deeper than uri
    with pytest.raises(MediaUnavailableError):
        resolve_media("", "x")
    with pytest.raises(MediaUnavailableError):
        resolve_media(str(root / "nope"), "x")


def test_audio_durations(client: TestClient, tmp_path: Path, export: dict[str, Any]) -> None:
    ds = export["ds"]
    data_dir = tmp_path / "data"
    durations = compute_audio_durations(ds, data_dir)
    assert durations == {3: pytest.approx(1.0)}
    assert (data_dir / "audio_durations.json").is_file()
    status, cached = audio_durations_or_start(ds, data_dir)
    assert status == "ready" and cached == durations
    assert get_store().data_dir == data_dir
    r = client.get("/api/chats/0/media").json()
    vn = r["voice_notes"]
    assert vn["status"] == "ready"
    assert vn["total_ms"] == 1000 and vn["count"] == 1
    gallery = r["gallery"]
    assert gallery["total"] == 11  # photo + video rows joined to messages
    r = client.get("/api/chats/0/media", params={"kind": "audio"}).json()
    assert [i["media_id"] for i in r["gallery"]["items"]] == [3]
    r = client.get("/api/chats/0/media", params={"sender": 0, "limit": 1}).json()
    assert r["gallery"]["next_cursor"] == "1" and r["gallery"]["total"] == 2
