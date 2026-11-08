import pickle
from pathlib import Path

import orjson
import pytest
from importer_fixtures import export_files, make_zip, mojibake, msg, thread, write_tree

from instagraph.importer.discovery import FileRef, ThreadSource, discover
from instagraph.importer.parser import (
    STAT_DUPLICATES,
    STAT_INVALID_JSON,
    STAT_MISSING_TIMESTAMP,
    STAT_REACTION_TEXT,
    ParsedThreadWithStats,
    fix_tree,
    parse_thread,
    sample_short_texts,
    share_kind_from_link,
)
from instagraph.importer.types import ParsedMedia, ParsedReaction, ParsedThread

LOC = ("en", "sk", "cs")
P = ["Jana Nováková", "Daniel"]


def parse(tmp_path: Path, files: list[dict], *, encode: bool = True, locales=LOC) -> ParsedThread:
    root = write_tree(tmp_path / "exp", export_files({("inbox", "jana_1"): files}, encode=encode))
    (src,) = discover([root]).threads
    return parse_thread(src, locales)


# --------------------------------------------------------------------------------------
# Encoding
# --------------------------------------------------------------------------------------

SAMPLES = [
    "čšťžýáíéúäôň ČŠŤŽÝÁÍÉÚÄÔŇ ľĺŕ",
    "příliš žluťoučký kůň úpěl ďábelské ódy",
    "ahoj 😂",
    "👨‍👩‍👧‍👦 family ZWJ",
    "🇸🇰🇨🇿 flags",
    "👍🏽 skin tone 🤷‍♀️",
    "❤️ with VS16",
    "plain ascii",
]


@pytest.mark.parametrize("s", SAMPLES)
def test_fix_tree_repairs_mojibake(s: str) -> None:
    n = [0]
    assert fix_tree({"a": [mojibake(s)]}, n) == {"a": [s]}
    assert n[0] == (0 if s.isascii() else 1)


@pytest.mark.parametrize("s", SAMPLES)
def test_already_correct_strings_unchanged(s: str) -> None:
    n = [0]
    assert fix_tree(s, n) == s
    assert n[0] == 0


def test_every_string_fixed_and_counted(tmp_path: Path) -> None:
    t = parse(
        tmp_path,
        [
            thread(
                ["Jana Nováková", "Daniel"],
                [
                    msg(
                        "Jana Nováková",
                        1000,
                        "čšťžýáíéúäôň 😂",
                        reactions=[{"reaction": "❤️", "actor": "Daniel", "timestamp": 2}],
                        share={
                            "link": "https://x.sk/ž",
                            "share_text": "Žltý",
                            "original_content_owner": "ľudo",
                        },
                    )
                ],
                title="Partia 🇸🇰",
            )
        ],
    )
    assert t.participants == ["Jana Nováková", "Daniel"]
    assert t.title == "Partia 🇸🇰"
    (m,) = t.messages
    assert m.sender_name == "Jana Nováková"
    assert m.text == "čšťžýáíéúäôň 😂"
    assert m.share_text == "Žltý" and m.share_owner == "ľudo"
    assert m.reactions == [ParsedReaction("❤", "Daniel", 2)]
    assert t.encoding_fixes >= 6


# --------------------------------------------------------------------------------------
# Merge, order, dedupe
# --------------------------------------------------------------------------------------


def test_multi_file_merge_order_and_dedupe(tmp_path: Path) -> None:
    # message_1 holds the newest messages; message_2 the older ones. Both newest first.
    f1 = thread(P, [msg("Daniel", 3000, "c"), msg("Jana Nováková", 4000, "d")])
    f2 = thread(
        P,
        [
            msg("Jana Nováková", 1000, "a"),
            msg("Daniel", 2000, "b1"),
            msg("Jana Nováková", 2000, "b2"),  # same ms: file order (older is later in file)
            msg("Daniel", 3000, "c"),  # exact duplicate of f1's message
        ],
    )
    t = parse(tmp_path, [f1, f2])
    assert [m.text for m in t.messages] == ["a", "b1", "b2", "c", "d"]
    assert t.file_count == 2
    assert isinstance(t, ParsedThreadWithStats)
    assert t.stats[STAT_DUPLICATES] == 1
    # Ties across files: the higher file index is older, so its copy is kept.
    assert [m.file_index for m in t.messages] == [2, 2, 2, 2, 1]


def test_tie_order_within_file_uses_reversed_position(tmp_path: Path) -> None:
    f = thread(P, [])
    # Same timestamps; the file is newest first, so the later entry is the older one.
    f["messages"] = [msg("Daniel", 1000, "second"), msg("Jana Nováková", 1000, "first")]
    t = parse(tmp_path, [f])
    assert [m.text for m in t.messages] == ["first", "second"]


def test_parse_from_zip_and_picklable(tmp_path: Path) -> None:
    z = make_zip(
        tmp_path / "e.zip", export_files({("inbox", "j_1"): [thread(P, [msg("Daniel", 5, "x")])]})
    )
    (src,) = discover([z]).threads
    src2 = pickle.loads(pickle.dumps(src))
    t = parse_thread(src2, LOC)
    assert [m.text for m in t.messages] == ["x"]
    assert "!" in t.source
    assert pickle.loads(pickle.dumps(t)).messages[0].text == "x"


# --------------------------------------------------------------------------------------
# Message mapping
# --------------------------------------------------------------------------------------


def test_types_and_parts(tmp_path: Path) -> None:
    msgs = [
        msg("Jana Nováková", 1, "hello"),
        msg("Jana Nováková", 2, "pozri https://example.com/a."),
        msg(
            "Jana Nováková",
            3,
            photos=[{"uri": "messages/inbox/jana_1/photos/1.jpg"}, {"uri": "p2"}],
        ),
        msg("Jana Nováková", 4, "caption", videos=[{"uri": "v"}], photos=[{"uri": "p"}]),
        msg("Jana Nováková", 5, audio_files=[{"uri": "a"}]),
        msg("Jana Nováková", 6, gifs=[{"uri": "g"}]),
        msg("Jana Nováková", 7, sticker={"uri": "s"}),
        msg("Jana Nováková", 8, share={"link": "https://www.instagram.com/reel/abc/"}),
        msg("Jana Nováková", 9),
        msg("Jana Nováková", 10, ""),
        msg("Daniel", 11, "Audio call ended", call_duration=312),
        msg("Daniel", 12, is_unsent=True),
    ]
    t = parse(tmp_path, [thread(P, msgs)])
    types = [m.type for m in t.messages]
    assert types == [
        "text", "link", "photo", "video", "audio", "gif", "sticker", "share",
        "unknown", "unknown", "call", "unsent",
    ]  # fmt: skip
    m = t.messages
    assert m[1].links == ["https://example.com/a"]
    assert m[2].n_photos == 2
    assert m[2].media[0] == ParsedMedia("photo", "messages/inbox/jana_1/photos/1.jpg")
    assert (m[3].n_videos, m[3].n_photos, m[3].text) == (1, 1, "caption")
    assert m[6].n_stickers == 1 and m[6].media == [ParsedMedia("sticker", "s")]
    assert m[7].share_kind == "reel"
    assert m[9].text is None
    assert (m[10].call_duration_s, m[10].call_kind, m[10].call_missed) == (312, "audio", False)
    assert m[11].is_unsent


@pytest.mark.parametrize(
    ("link", "kind"),
    [
        ("https://www.instagram.com/reel/Cx1/", "reel"),
        ("https://instagram.com/reels/Cx1", "reel"),
        ("https://www.instagram.com/p/Cx1/?igsh=1", "post"),
        ("https://www.instagram.com/tv/Cx1", "post"),
        ("https://www.instagram.com/stories/jana/123/", "story"),
        ("https://www.instagram.com/jana.novakova/", "profile"),
        ("https://www.instagram.com/explore/tags/x/", "other"),
        ("https://youtu.be/xyz", "external"),
        ("", "other"),
        (None, "other"),
    ],
)
def test_share_kind(link: str | None, kind: str) -> None:
    assert share_kind_from_link(link) == kind


def test_attachment_placeholder_yields_to_share(tmp_path: Path) -> None:
    t = parse(
        tmp_path,
        [
            thread(
                P,
                [
                    msg(
                        "Jana Nováková",
                        1,
                        "Jana Nováková sent an attachment.",
                        share={"link": "https://www.instagram.com/p/x/"},
                    ),
                    msg("Jana Nováková", 2, "Jana Nováková sent an attachment."),
                ],
            )
        ],
    )
    a, b = t.messages
    assert (a.type, a.text, a.share_kind) == ("share", None, "post")
    assert (b.type, b.system_event, b.text) == ("system", "attachment", None)


def test_likes_calls_unsent_from_text(tmp_path: Path) -> None:
    msgs = [
        msg("Jana Nováková", 1, "hi"),
        msg("Daniel", 2, "Liked a message"),
        msg("Daniel", 3, "You missed a video chat"),
        msg("Jana Nováková", 4, "Jana Nováková started a video chat"),
        msg("Jana Nováková", 5, "Video chat ended", call_duration=60),
        msg("Daniel", 6, "Daniel unsent a message"),
        msg("Jana Nováková", 7, "Zmeškali ste hlasový hovor"),
    ]
    t = parse(tmp_path, [thread(P, msgs)])
    m = t.messages
    assert m[1].type == "like" and m[1].text is None
    assert (m[2].type, m[2].call_kind, m[2].call_missed) == ("call", "video", True)
    assert (m[3].type, m[3].system_event, m[3].call_kind) == ("system", "call_event", "video")
    assert (m[4].type, m[4].call_kind, m[4].call_duration_s) == ("call", "video", 60)
    assert m[5].type == "unsent" and m[5].is_unsent
    assert (m[6].type, m[6].call_kind, m[6].call_missed) == ("call", "audio", True)


def test_system_messages_en_sk_cs(tmp_path: Path) -> None:
    msgs = [
        msg("Daniel", 1, "Daniel named the group Partia 🎉."),
        msg("Jana Nováková", 2, "Jana Nováková pomenovala skupinu Kamoši"),
        msg("Jana Nováková", 3, "Jana Nováková přidala Daniel do skupiny"),
        msg("Daniel", 4, "Daniel left the group."),
        msg("Daniel", 5, "Daniel left the group, just kidding"),
    ]
    t = parse(tmp_path, [thread(P, msgs)])
    m = t.messages
    assert (m[0].type, m[0].system_event, m[0].system_payload) == ("system", "rename", "Partia 🎉")
    assert (m[1].system_event, m[1].system_payload) == ("rename", "Kamoši")
    assert (m[2].system_event, m[2].system_payload) == ("join", "Daniel")
    assert (m[3].system_event, m[3].system_payload, m[3].text) == ("leave", "Daniel", None)
    assert m[4].type == "text"


def test_no_locales_means_no_classification(tmp_path: Path) -> None:
    t = parse(tmp_path, [thread(P, [msg("Daniel", 1, "Liked a message")])], locales=())
    assert t.messages[0].type == "text"


def test_reactions_as_text_attach_to_previous_other_sender(tmp_path: Path) -> None:
    msgs = [
        msg("Jana Nováková", 1000, "first"),
        msg("Daniel", 2000, "mine"),
        msg("Daniel", 3000, "Reacted 😂 to your message"),  # Daniel reacts → Jana's "first"
        msg("Jana Nováková", 4000, "Reagovala ❤️ na vašu správu"),  # → Daniel's "mine"
        msg("Jana Nováková", 500, "Reacted 👍 to your message"),  # nothing earlier
    ]
    t = parse(tmp_path, [thread(P, msgs)])
    texts = [m.text for m in t.messages]
    assert texts == ["first", "mine"]
    first, mine = t.messages
    assert first.reactions == [ParsedReaction("😂", "Daniel", 3)]
    assert mine.reactions == [ParsedReaction("❤", "Jana Nováková", 4)]
    assert isinstance(t, ParsedThreadWithStats)
    assert t.stats[STAT_REACTION_TEXT] == 2


def test_reaction_timestamps_seconds_and_ms(tmp_path: Path) -> None:
    m = msg(
        "Jana Nováková",
        1,
        "x",
        reactions=[
            {"reaction": "👍🏽", "actor": "Daniel", "timestamp": 1700000000},
            {"reaction": "❤", "actor": "Daniel", "timestamp": 1700000000123},
            {"reaction": "😮", "actor": "Daniel"},
        ],
    )
    t = parse(tmp_path, [thread(P, [m])])
    assert [r.ts_s for r in t.messages[0].reactions] == [1700000000, 1700000000, None]
    assert t.messages[0].reactions[0].emoji == "👍🏽"


def test_unknown_keys_and_robustness(tmp_path: Path) -> None:
    f = thread(
        P,
        [
            msg("Daniel", 1, "a", brand_new_field=1, share={"link": "x", "weird": 2}),
            msg("Daniel", 2, "b", brand_new_field=3, reactions=[{"reaction": "❤", "odd": 1}]),
            msg("Daniel", 3, "c", photos="not a list"),
            {"sender_name": "Daniel", "content": "no ts"},
            "not a dict",
        ],
        newest_first=False,
        top_level_new=True,
    )
    f.pop("title")
    t = parse(tmp_path, [f])
    assert t.unknown_keys["messages[].brand_new_field"] == 2
    assert t.unknown_keys["messages[].share.weird"] == 1
    assert t.unknown_keys["messages[].reactions[].odd"] == 1
    assert t.unknown_keys["top_level_new"] == 1
    assert t.title is None
    assert [m.text for m in t.messages] == ["a", "b", "c"]
    assert isinstance(t, ParsedThreadWithStats)
    assert t.stats[STAT_MISSING_TIMESTAMP] == 1
    assert t.stats["invalid_field:messages[].photos"] == 1


def test_invalid_json_file_is_skipped(tmp_path: Path) -> None:
    good = tmp_path / "good.json"
    good.write_bytes(orjson.dumps(thread(P, [msg("Daniel", 1, "ok")])))
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"{broken")
    nomsg = tmp_path / "nomsg.json"
    nomsg.write_bytes(orjson.dumps({"participants": []}))
    src = ThreadSource(
        folder="x_1",
        category="request",
        files=(FileRef(str(good)), FileRef(str(bad)), FileRef(str(nomsg))),
    )
    t = parse_thread(src, LOC)
    assert t.category == "request"
    assert [m.text for m in t.messages] == ["ok"]
    assert isinstance(t, ParsedThreadWithStats)
    assert t.stats[STAT_INVALID_JSON] == 2


def test_sample_short_texts(tmp_path: Path) -> None:
    root = write_tree(
        tmp_path / "e",
        export_files(
            {
                ("inbox", "j_1"): [
                    thread(
                        P,
                        [msg("Daniel", i, f"Páči sa mu {i}") for i in range(10)]
                        + [msg("Daniel", 99, "x" * 500), msg("Daniel", 100)],
                    )
                ]
            }
        ),
    )
    (src,) = discover([root]).threads
    texts = sample_short_texts(src, limit=5)
    assert len(texts) == 5 and all(t.startswith("Páči sa mu") for t in texts)
    assert len(sample_short_texts(src)) == 10
