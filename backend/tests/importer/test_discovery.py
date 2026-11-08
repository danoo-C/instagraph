import os
import zipfile
from pathlib import Path

import orjson
import pytest
from importer_fixtures import export_files, make_zip, msg, thread, write_tree

from instagraph.importer.discovery import (
    DiscoveryError,
    FileRef,
    HtmlExportError,
    ImportSafetyError,
    discover,
    match_media,
    media_key,
    read_owner_name,
)

JANA = [thread(["Jana Nováková", "Me"], [msg("Jana Nováková", 1000, "ahoj")])]


def test_new_layout_folder(tmp_path: Path) -> None:
    files = export_files(
        {
            ("inbox", "jana_1"): JANA,
            ("request", "spam_2"): JANA,
            ("archived", "old_3"): JANA,
        },
        owner="Daniel Šťastný",
        media=["inbox/jana_1/photos/1.jpg"],
    )
    root = write_tree(tmp_path / "export", files)
    res = discover([root])
    cats = {(t.category, t.folder) for t in res.threads}
    assert cats == {("inbox", "jana_1"), ("request", "spam_2"), ("archived", "old_3")}
    assert res.layouts == {"your_instagram_activity"}
    assert res.personal_info is not None
    assert read_owner_name(res.personal_info) == "Daniel Šťastný"
    assert "messages/inbox/jana_1/photos/1.jpg" in res.media_files
    ref = match_media("your_instagram_activity/messages/inbox/jana_1/photos/1.jpg", res.media_files)
    assert ref is not None and ref.read_bytes().startswith(b"\x89PNG")
    assert res.file_count == len(files)
    assert res.zip_files == []
    t = res.threads[0]
    assert all(Path(f.path).is_absolute() and f.member is None for f in t.files)


def test_old_layout_folder_and_media_key(tmp_path: Path) -> None:
    files = export_files(
        {("inbox", "jana_1"): JANA}, layout="old", media=["inbox/jana_1/videos/v.mp4"]
    )
    root = write_tree(tmp_path / "export", files)
    res = discover([root])
    assert res.layouts == {"root_messages"}
    assert [t.folder for t in res.threads] == ["jana_1"]
    assert match_media("messages/inbox/jana_1/videos/v.mp4", res.media_files) is not None
    assert match_media("messages/inbox/jana_1/videos/missing.mp4", res.media_files) is None
    assert media_key("a/b/messages/inbox/x/photos/1.jpg") == "messages/inbox/x/photos/1.jpg"
    assert media_key("no/messages_here.jpg") is None


def test_nested_unknown_root_is_found(tmp_path: Path) -> None:
    files = {f"deep/er/{k}": v for k, v in export_files({("inbox", "jana_1"): JANA}).items()}
    res = discover([write_tree(tmp_path / "x", files)])
    assert [t.folder for t in res.threads] == ["jana_1"]


def test_zip_source(tmp_path: Path) -> None:
    files = export_files({("inbox", "jana_1"): JANA}, owner="Me")
    z = make_zip(tmp_path / "export.zip", files)
    res = discover([z])
    assert res.zip_files == [str(z.resolve())]
    (t,) = res.threads
    (f,) = t.files
    assert f.member is not None and f.path == str(z.resolve())
    assert orjson.loads(f.read_bytes())["participants"]
    with f.open() as fh:
        assert fh.read(1) == b"{"
    assert f.describe() == f"{z.resolve()}!{f.member}"
    assert res.personal_info is not None and read_owner_name(res.personal_info) == "Me"


def test_multipart_zips_in_folder_are_merged(tmp_path: Path) -> None:
    t1 = thread(["Jana", "Me"], [msg("Jana", 3000, "c")])
    t2 = thread(["Jana", "Me"], [msg("Jana", 1000, "a")])
    all_files = export_files(
        {("inbox", "jana_1"): [t1, t2], ("inbox", "peter_2"): JANA},
        media=["inbox/jana_1/photos/p.jpg"],
    )
    names = sorted(all_files)
    part1 = {n: all_files[n] for n in names if n.endswith("message_2.json")}
    part2 = {n: all_files[n] for n in names if n not in part1}
    folder = tmp_path / "zips"
    make_zip(folder / "instagram-part-1.zip", part1)
    make_zip(folder / "instagram-part-2.zip", part2)
    res = discover([folder])
    assert len(res.zip_files) == 2
    jana = next(t for t in res.threads if t.folder == "jana_1")
    assert [Path(f.member or "").name for f in jana.files] == ["message_1.json", "message_2.json"]
    assert {f.path for f in jana.files} == {str((folder / p).resolve()) for p in os.listdir(folder)}
    assert "messages/inbox/jana_1/photos/p.jpg" in res.media_files


def test_several_explicit_sources(tmp_path: Path) -> None:
    a = make_zip(tmp_path / "a.zip", export_files({("inbox", "x_1"): JANA}))
    b = write_tree(tmp_path / "b", export_files({("inbox", "y_2"): JANA}, layout="old"))
    res = discover([a, b])
    assert {t.folder for t in res.threads} == {"x_1", "y_2"}
    assert res.layouts == {"your_instagram_activity", "root_messages"}


def test_files_sorted_by_numeric_index(tmp_path: Path) -> None:
    files = export_files({("inbox", "j_1"): [JANA[0]] * 11})
    res = discover([write_tree(tmp_path / "e", files)])
    names = [Path(f.path).name for f in res.threads[0].files]
    assert names[:3] == ["message_1.json", "message_2.json", "message_3.json"]
    assert names[-1] == "message_11.json"


def test_html_export_detected(tmp_path: Path) -> None:
    write_tree(
        tmp_path / "e",
        {"your_instagram_activity/messages/inbox/jana_1/message_1.html": b"<html></html>"},
    )
    with pytest.raises(HtmlExportError, match="JSON"):
        discover([tmp_path / "e"])


def test_missing_and_bad_sources(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        discover([tmp_path / "nope"])
    (tmp_path / "x.txt").write_text("hi")
    with pytest.raises(DiscoveryError):
        discover([tmp_path / "x.txt"])


@pytest.mark.parametrize(
    "member",
    ["../evil/messages/inbox/a_1/message_1.json", "/etc/passwd", "a/../../b.json", "C:/x.json"],
)
def test_zip_slip_rejected(tmp_path: Path, member: str) -> None:
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("messages/inbox/a_1/message_1.json", b"{}")
        zf.writestr(zipfile.ZipInfo(member), b"x")
    with pytest.raises(ImportSafetyError):
        discover([z])


def test_zip_bomb_rejected_by_total_size(tmp_path: Path) -> None:
    z = tmp_path / "bomb.zip"
    with zipfile.ZipFile(z, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("messages/inbox/a_1/message_1.json", b"0" * 2_000_000)
    assert z.stat().st_size < 100_000
    with pytest.raises(ImportSafetyError, match="zip bomb"):
        discover([z], max_total_uncompressed=1_000_000)
    assert discover([z]).file_count == 1


def test_zip_member_count_cap(tmp_path: Path) -> None:
    z = tmp_path / "many.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for i in range(20):
            zf.writestr(f"messages/inbox/a_1/photos/{i}.jpg", b"x")
    with pytest.raises(ImportSafetyError):
        discover([z], max_members=10)


def test_symlink_escaping_root_is_ignored(tmp_path: Path) -> None:
    outside = write_tree(tmp_path / "outside", export_files({("inbox", "secret_9"): JANA}))
    root = write_tree(tmp_path / "root", export_files({("inbox", "jana_1"): JANA}))
    inbox = root / "your_instagram_activity/messages/inbox"
    try:
        (inbox / "secret_9").symlink_to(
            outside / "your_instagram_activity/messages/inbox/secret_9", target_is_directory=True
        )
        (inbox / "jana_1" / "message_2.json").symlink_to(
            outside / "your_instagram_activity/messages/inbox/secret_9/message_1.json"
        )
        # A symlink that stays inside the root is followed (also when the target folder
        # is walked on its own), and a cycle doesn't hang.
        stash = write_tree(root / "stash", export_files({("inbox", "x"): JANA}, layout="old"))
        (inbox / "alias_5").symlink_to(stash / "messages/inbox/x", target_is_directory=True)
        (inbox / "jana_1" / "loop").symlink_to(inbox, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not supported")
    res = discover([root])
    folders = sorted(t.folder for t in res.threads)
    assert folders == ["alias_5", "jana_1", "x"]
    jana = next(t for t in res.threads if t.folder == "jana_1")
    assert [Path(f.path).name for f in jana.files] == ["message_1.json"]
    for t in res.threads:
        for f in t.files:
            assert Path(f.path).is_relative_to(root.resolve())


def test_fileref_is_picklable(tmp_path: Path) -> None:
    import pickle

    ref = FileRef(path=str(tmp_path / "a.zip"), member="messages/x")
    assert pickle.loads(pickle.dumps(ref)) == ref


def test_owner_name_shapes(tmp_path: Path) -> None:
    p = tmp_path / "pi.json"
    p.write_bytes(orjson.dumps({"name": "Plain Name"}))
    assert read_owner_name(FileRef(str(p))) == "Plain Name"
    p.write_bytes(orjson.dumps([{"string_map_data": {"Meno": {"value": "J\u00c3\u00a1n"}}}]))
    assert read_owner_name(FileRef(str(p))) == "Ján"
    p.write_bytes(b"not json")
    assert read_owner_name(FileRef(str(p))) is None
    p.write_bytes(orjson.dumps({"profile_user": [{"string_map_data": {}}]}))
    assert read_owner_name(FileRef(str(p))) is None
