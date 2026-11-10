"""Pseudonymizer (docs/ai-assistant.md A6, A10)."""

import time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from instagraph.ai.pseudonym import (
    PLACEHOLDER_RE,
    ChatEntry,
    PersonEntry,
    Pseudonymizer,
    name_endings,
)

PEOPLE = [
    PersonEntry(0, "Daniel Danko", usernames=("dano.danko",), is_owner=True),
    PersonEntry(1, "Eva Nováková", aliases=("Evka",), usernames=("evka.n",)),
    PersonEntry(2, "Martin Kováč", aliases=("Kovi",)),
    PersonEntry(3, "Jana", usernames=("janka_123",)),
    PersonEntry(4, "Jan Horák"),
    PersonEntry(5, "Max", usernames=("max_k",), match_in_text=False),
    PersonEntry(6, "Eva Smith"),
    PersonEntry(7, "Will Turner"),
    PersonEntry(8, "Šimon Veselý"),
    PersonEntry(9, "Jo"),
    PersonEntry(10, "Mici", aliases=("🐱",)),
]
CHATS = [
    ChatEntry(100, "Eva Nováková"),
    ChatEntry(101, "Partia 🍻"),
    ChatEntry(102, "🔥🔥🔥"),
    ChatEntry(103, "Výlet Tatry 2023"),
    ChatEntry(104, "AB"),
]


@pytest.fixture(scope="module")
def ps() -> Pseudonymizer:
    return Pseudonymizer(PEOPLE, CHATS)


def single(name: str) -> Pseudonymizer:
    return Pseudonymizer([PersonEntry(1, name)], [])


# --------------------------------------------------------------------------------------
# Placeholders
# --------------------------------------------------------------------------------------


def test_placeholder_assignment(ps: Pseudonymizer) -> None:
    assert ps.person_placeholder(0) == "[ME]"
    assert [ps.person_placeholder(i) for i in range(1, 11)] == [f"[P{i}]" for i in range(1, 11)]
    assert [ps.chat_placeholder(t) for t in range(100, 105)] == [f"[C{i}]" for i in range(1, 6)]
    with pytest.raises(KeyError):
        ps.person_placeholder(999)
    with pytest.raises(KeyError):
        ps.chat_placeholder(999)


def test_placeholders_stable_regardless_of_input_order() -> None:
    a = Pseudonymizer(PEOPLE, CHATS)
    b = Pseudonymizer(reversed(PEOPLE), reversed(CHATS))
    for p in PEOPLE:
        assert a.person_placeholder(p.person_id) == b.person_placeholder(p.person_id)
    for c in CHATS:
        assert a.chat_placeholder(c.thread_id) == b.chat_placeholder(c.thread_id)


def test_endings_resource_loads() -> None:
    endings = name_endings()
    assert {"ovi", "om", "ka", "ou", "ik", "inka", "ecka", "uska"} <= endings
    assert all(e == e.lower() and e.isascii() for e in endings)


# --------------------------------------------------------------------------------------
# Matching: inflection, accents, case
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "form"),
    [
        ("Daniel", "Daniel"),
        ("Daniel", "Danielovi"),
        ("Daniel", "Danielom"),
        ("Daniel", "Daniela"),
        ("Daniel", "Danielem"),  # CS instrumental
        ("Dan", "Danka"),
        ("Dan", "Danko"),
        ("Dan", "Daník"),
        ("Dan", "Danovi"),
        ("Eva", "Evou"),
        ("Eva", "Eve"),
        ("Eva", "Evy"),
        ("Eva", "Evka"),
        ("Eva", "Evke"),
        ("Eva", "Evičku"),
        ("Eva", "Evuška"),
        ("Martin", "Martina"),
        ("Martin", "Martinom"),
        ("Martin", "Martinko"),
        ("Jana", "Jane"),
        ("Jana", "Janou"),
        ("Jana", "Janka"),
        ("Jana", "Janinka"),
        ("Pavel", "Pavla"),
        ("Pavel", "Pavlovi"),
        ("Marek", "Marka"),
        ("Nováková", "Novákovej"),
        ("Nováková", "Novákovou"),
        ("Novák", "Novákovi"),
        ("Veselý", "Veselého"),
        ("Veselý", "Veselému"),
        ("Mária", "Márie"),
        ("Mária", "Máriou"),
        ("Tomáš", "Tomášovi"),
        ("Tomáš", "Tomáškovi"),
        ("Jano", "Janovi"),
    ],
)
def test_inflected_forms(name: str, form: str) -> None:
    p = single(name)
    assert p.encode_text(f"Včera som bol s {form}, fakt.") == "Včera som bol s [P1], fakt."


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("Daniel", "Danielle"),
        ("Daniel", "Danielsson"),
        ("Dan", "dance"),
        ("Dan", "Dante"),
        ("Jan", "January"),
        ("Eva", "evaluate"),
        ("Eva", "evil"),
        ("Martin", "Martinique"),
        ("Ana", "áno"),  # stop-word: "an" + "o" folded
        ("Ana", "ano"),
        ("Jana", "Janovicky"),
    ],
)
def test_not_matched(name: str, text: str) -> None:
    p = single(name)
    s = f"well {text} here"
    assert p.encode_text(s) == s


def test_stopword_inflection_blocked_but_real_forms_kept() -> None:
    p = single("Ana")
    assert p.encode_text("áno, s Anou") == "áno, s [P1]"


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("Šimon", "Simon"),
        ("Simon", "Šimon"),
        ("Simon", "ŠIMONOVI"),
        ("Šimon", "simonom"),
        ("Šimon", "Šimon"),  # decomposed accent in the text
        ("Šimon", "Šimon"),  # decomposed accent in the name
        ("Zoë", "Zoe"),
        ("Daniel", "DANIEL"),
        ("Daniel", "daniel"),
        ("Daniel", "dAnIeLoVi"),
    ],
)
def test_accents_and_case(name: str, text: str) -> None:
    p = single(name)
    assert p.encode_text(f"Ahoj {text}!") == "Ahoj [P1]!"


def test_accented_text_keeps_surrounding_characters() -> None:
    p = single("Eva")
    # combining mark right after the name belongs to the name
    assert p.encode_text("Čau Évá, ako sa máš?") == "Čau [P1], ako sa máš?"
    assert p.encode_text("ßß Eva ßß") == "ßß [P1] ßß"


def test_english_fixture(ps: Pseudonymizer) -> None:
    assert ps.encode_text("Daniel's car is parked next to Martin") == (
        "[ME]'s car is parked next to [P2]"
    )
    assert ps.encode_text("I will call you, Will") == "I will call you, Will"
    assert ps.encode_text("Will Turner said hi") == "[P7] said hi"
    assert ps.encode_text("Turner said hi") == "[P7] said hi"


def test_slovak_fixture(ps: Pseudonymizer) -> None:
    assert ps.encode_text("Povedz Danielovi, že s Evou Novákovou ideme za Kovim.") == (
        "Povedz [ME], že s [P1] ideme za [P2]."
    )
    assert ps.encode_text("Evka a Martinko a Šimonovi Veselému") == "[P1] a [P2] a [P8]"


def test_czech_fixture(ps: Pseudonymizer) -> None:
    assert ps.encode_text("Řekni Danielovi a Martinovi, že Janě volám.") == (
        "Řekni [ME] a [P2], že [P3] volám."
    )
    assert ps.encode_text("Byl jsem s Danielem a Horákem") == "Byl jsem s [ME] a [P4]"


# --------------------------------------------------------------------------------------
# Longest match, overlaps, tie-breaking
# --------------------------------------------------------------------------------------


def test_full_name_beats_first_name(ps: Pseudonymizer) -> None:
    assert ps.encode_text("Eva Smith a Eva Nováková") == "[P6] a [P1]"
    assert ps.encode_text("s Evou Smith") == "s [P6]"
    # a full-name match spanning a line break still counts as one name
    assert ps.encode_text("Eva\nSmith") == "[P6]"


def test_bare_shared_first_name_goes_to_lowest_id(ps: Pseudonymizer) -> None:
    # "Eva" is a name part of both P1 (id 1) and P6 (id 6) → lower id
    assert ps.encode_text("Videl som Evu") == "Videl som [P1]"


def test_person_called_exactly_the_name_beats_name_parts() -> None:
    p = Pseudonymizer(
        [PersonEntry(1, "Eva Nováková"), PersonEntry(2, "Eva Smith"), PersonEntry(9, "Eva")], []
    )
    assert p.encode_text("ahoj Eva") == "ahoj [P3]"
    assert p.encode_text("ahoj Evka") == "ahoj [P3]"
    assert p.encode_text("ahoj Eva Smith") == "ahoj [P2]"


def test_exact_form_beats_inflected_form() -> None:
    p = Pseudonymizer([PersonEntry(1, "Jan"), PersonEntry(2, "Jana")], [])
    assert p.encode_text("Jana a Jan") == "[P2] a [P1]"
    # "Danka" exact alias of P2 beats "Dan" + "-ka"
    p = Pseudonymizer([PersonEntry(1, "Dan"), PersonEntry(2, "Daniela", aliases=("Danka",))], [])
    assert p.encode_text("Danka a Danko") == "[P2] a [P1]"


def test_longest_inflected_name_wins() -> None:
    p = Pseudonymizer([PersonEntry(1, "Dan"), PersonEntry(2, "Daniel")], [])
    assert p.encode_text("Danielovi aj Danovi") == "[P2] aj [P1]"


def test_owner_names_are_hidden_too(ps: Pseudonymizer) -> None:
    assert ps.encode_text("Danko, kde si?") == "[ME], kde si?"


# --------------------------------------------------------------------------------------
# Usernames, mentions, URLs, chat titles
# --------------------------------------------------------------------------------------


def test_mentions() -> None:
    p = Pseudonymizer(PEOPLE, CHATS)  # fresh: [Un] numbering is per instance
    assert p.encode_text("pozri @evka.n a @dano.danko") == "pozri [P1] a [ME]"
    assert p.encode_text("@someone.else, @Other_Guy a @someone.else.") == "[U1], [U2] a [U1]."
    assert p.decode_text("[U1] [U2]") == "@someone.else @Other_Guy"
    # known username of a match_in_text=False person still maps to the person
    assert p.encode_text("cc @max_k") == "cc [P5]"
    # e-mail addresses aren't mentions
    assert p.encode_text("jan@x.sk") == "[P4]@x.sk"


def test_username_placeholder_for_share_owners() -> None:
    p = Pseudonymizer(PEOPLE, CHATS)
    assert p.username_placeholder("evka.n") == "[P1]"
    assert p.username_placeholder("@EVKA.N") == "[P1]"
    assert p.username_placeholder("natgeo") == "[U1]"
    assert p.encode_text("@natgeo") == "[U1]"
    assert p.username_placeholder("other") == "[U2]"
    assert p.decode_text("[U1]") == "@natgeo"


def test_usernames_in_plain_text_and_urls(ps: Pseudonymizer) -> None:
    assert ps.encode_text("evka.n posted") == "[P1] posted"
    assert ps.encode_text("https://instagram.com/evka.n/") == "https://instagram.com/[P1]/"
    assert ps.encode_text("https://instagram.com/janka_123") == "https://instagram.com/[P3]"
    out = ps.encode_text("https://www.example.com/daniel-novak?q=martin")
    assert out == "https://www.example.com/[ME]-novak?q=[P2]"
    assert "instagram.com" in ps.encode_text("https://instagram.com/p/abc123")


def test_chat_titles(ps: Pseudonymizer) -> None:
    assert ps.encode_text("v skupine Partia 🍻 je ticho") == "v skupine [C2] je ticho"
    assert ps.encode_text("Partia sa stretne") == "[C2] sa stretne"
    assert ps.encode_text("Výlet Tatry 2023 bol super") == "[C4] bol super"
    assert ps.encode_text("🔥🔥🔥 hot") == "[C3] hot"
    # short titles only as a whole string
    assert ps.encode_text("AB testing") == "AB testing"
    assert ps.encode_text("AB") == "[C5]"
    # a chat titled like a person → the person
    assert ps.encode_text("Eva Nováková") == "[P1]"


def test_emoji_alias(ps: Pseudonymizer) -> None:
    assert ps.encode_text("love you 🐱") == "love you [P10]"


# --------------------------------------------------------------------------------------
# Common-word / short names
# --------------------------------------------------------------------------------------


def test_match_in_text_false(ps: Pseudonymizer) -> None:
    assert ps.encode_text("max speed is Max") == "max speed is Max"
    assert ps.encode_text("max_k posted") == "max_k posted"
    assert ps.encode_text("Max") == "[P5]"
    assert ps.encode_text("  máx \n") == "  [P5] \n"
    assert ps.encode_text("max_k") == "[P5]"
    assert ps.encode_text("@max_k") == "[P5]"
    assert ps.encode_value({"s": "Max", "t": "max out"}) == {"s": "[P5]", "t": "max out"}


def test_short_names_only_as_whole_string() -> None:
    p = Pseudonymizer([PersonEntry(0, "Me", is_owner=True), PersonEntry(1, "Jo")], [])
    assert p.encode_text("call me, jo") == "call me, jo"
    assert p.encode_text("Me") == "[ME]"
    assert p.encode_text("Jo") == "[P1]"


def test_stopword_name_parts_skipped_but_full_name_matched(ps: Pseudonymizer) -> None:
    assert ps.encode_text("Will Turner will come") == "[P7] will come"


# --------------------------------------------------------------------------------------
# Idempotency, structured values
# --------------------------------------------------------------------------------------

SAMPLES = [
    "Povedz Danielovi, že s Evou Novákovou ideme za Kovim.",
    "pozri @evka.n a @neznamy",
    "Partia 🍻 je super 🔥🔥🔥",
    "https://instagram.com/evka.n",
    "Eva Smith a Eva Nováková",
    "[P1] a [C2] a [ME] a [p3] a [U7]",
    "Max",
    "",
    "   ",
    "12345",
    "2024-01-31T12:00:00",
]


@pytest.mark.parametrize("text", SAMPLES)
def test_idempotent(ps: Pseudonymizer, text: str) -> None:
    once = ps.encode_text(text)
    assert ps.encode_text(once) == once


def test_existing_placeholders_untouched(ps: Pseudonymizer) -> None:
    s = "[P1] vs [C2] vs [ME] vs [U3] vs [P9999]"
    assert ps.encode_text(s) == s


def test_encode_value_nested(ps: Pseudonymizer) -> None:
    value = {
        "Eva": "keys stay",
        "chat": {"title": "Partia 🍻", "id": 101, "share": 0.5, "group": True},
        "messages": [
            {"sender": "Eva Nováková", "text": "Ahoj Danielovi", "ts": "2024-01-31", "n": 3},
            {"sender": "Daniel Danko", "text": None, "type": "text"},
        ],
        "people": ("Martin Kováč", 7),
        "count": 12,
    }
    out = ps.encode_value(value)
    assert out == {
        "Eva": "keys stay",
        "chat": {"title": "[C2]", "id": 101, "share": 0.5, "group": True},
        "messages": [
            {"sender": "[P1]", "text": "Ahoj [ME]", "ts": "2024-01-31", "n": 3},
            {"sender": "[ME]", "text": None, "type": "text"},
        ],
        "people": ("[P2]", 7),
        "count": 12,
    }
    assert value["chat"] == {"title": "Partia 🍻", "id": 101, "share": 0.5, "group": True}


@pytest.mark.parametrize(
    "s", ["text", "photo", "reel", "share", "2024-01-31", "123", "inbox", "group", "like"]
)
def test_enum_like_values_untouched(ps: Pseudonymizer, s: str) -> None:
    assert ps.encode_text(s) == s


# --------------------------------------------------------------------------------------
# Decoding
# --------------------------------------------------------------------------------------


def test_decode_round_trip_structured(ps: Pseudonymizer) -> None:
    for p in PEOPLE:
        assert ps.decode_text(ps.encode_text(p.display_name)) == p.display_name
    for c in CHATS[1:]:  # CHATS[0] is titled like a person → decodes to that person
        assert ps.decode_text(ps.encode_text(c.title)) == c.title
    assert ps.decode_text(ps.encode_text("Eva Nováková")) == "Eva Nováková"


def test_decode_text(ps: Pseudonymizer) -> None:
    assert ps.decode_text("[P3] replies faster than [ME] in [C2].") == (
        "Jana replies faster than Daniel Danko in Partia 🍻."
    )
    assert ps.decode_text("[P99] and [C42] and [U5] and [p1] and [X1]") == (
        "[P99] and [C42] and [U5] and [p1] and [X1]"
    )
    assert ps.decode_text("no placeholders") == "no placeholders"


def test_decode_uses_given_display_names() -> None:
    p = Pseudonymizer([PersonEntry(1, "Person A", aliases=("Eva",))], [])
    assert p.encode_text("Eva") == "[P1]"
    assert p.decode_text("[P1]") == "Person A"


# --------------------------------------------------------------------------------------
# Streaming
# --------------------------------------------------------------------------------------

STREAM_TEXT = "[ME] a [P3] v [C2], [P10]! [U1]? [note] [P12345] [P99] [ME"


def stream(ps: Pseudonymizer, chunks: list[str]) -> tuple[str, list[str]]:
    d = ps.stream_decoder()
    emitted = [d.feed(c) for c in chunks]
    emitted.append(d.flush())
    return "".join(emitted), emitted


def test_stream_every_split_point(ps: Pseudonymizer) -> None:
    expected = ps.decode_text(STREAM_TEXT)
    for i in range(len(STREAM_TEXT) + 1):
        out, _ = stream(ps, [STREAM_TEXT[:i], STREAM_TEXT[i:]])
        assert out == expected, i


def test_stream_every_pair_of_split_points(ps: Pseudonymizer) -> None:
    text = "x [P10] y [C2]"
    expected = ps.decode_text(text)
    for i in range(len(text) + 1):
        for j in range(i, len(text) + 1):
            out, _ = stream(ps, [text[:i], text[i:j], text[j:]])
            assert out == expected, (i, j)


def test_stream_char_by_char(ps: Pseudonymizer) -> None:
    out, emitted = stream(ps, list(STREAM_TEXT))
    assert out == ps.decode_text(STREAM_TEXT)
    # no raw placeholder ever reaches the client
    assert not any(PLACEHOLDER_RE.search(e) and "[P99]" not in e for e in emitted)


def test_stream_releases_non_placeholders(ps: Pseudonymizer) -> None:
    d = ps.stream_decoder()
    assert d.feed("see [") == "see "
    assert d.feed("n") == "[n"
    assert d.feed("ote]") == "ote]"
    assert d.feed("[P") == ""
    assert d.feed("3") == ""
    assert d.feed("]") == "Jana"
    assert d.feed("[P1234") == ""
    assert d.feed("5") == "[P12345"
    assert d.feed("[") == ""
    assert d.flush() == "["
    assert d.flush() == ""


# --------------------------------------------------------------------------------------
# Properties and performance
# --------------------------------------------------------------------------------------


@settings(max_examples=300, deadline=None)
@given(
    name=st.text(min_size=1, max_size=40),
    owner=st.booleans(),
    other=st.text(min_size=1, max_size=20),
)
def test_property_display_name_round_trip(name: str, owner: bool, other: str) -> None:
    people = [PersonEntry(5, name, is_owner=owner)]
    # a second, different person must not steal the round trip
    if " ".join(other.casefold().split()) != " ".join(name.casefold().split()):
        people.append(PersonEntry(9, other))
    p = Pseudonymizer(people, [ChatEntry(1, "Some Chat")])
    assert p.decode_text(p.encode_text(name)) == name


@settings(max_examples=200, deadline=None)
@given(text=st.text(max_size=80))
def test_property_idempotent(text: str) -> None:
    p = Pseudonymizer(PEOPLE, CHATS)
    once = p.encode_text(text)
    assert p.encode_text(once) == once


FIRST = ["Adam", "Eva", "Ján", "Mária", "Peter", "Zuzana", "Tomáš", "Lucia", "Martin", "Jana"]
LAST = ["Novák", "Kováčová", "Horváth", "Varga", "Tóth", "Nagy", "Baláž", "Molnár"]


def test_construction_and_matching_speed() -> None:
    people = [
        PersonEntry(
            i,
            f"{FIRST[i % len(FIRST)]}{i} {LAST[i % len(LAST)]}{i // 7}",
            aliases=(f"Nick{i}",),
            usernames=(f"user_{i}",),
            is_owner=i == 0,
        )
        for i in range(500)
    ]
    chats = [ChatEntry(i, f"Group {i} chat") for i in range(500)]
    t0 = time.perf_counter()
    p = Pseudonymizer(people, chats)
    assert time.perf_counter() - t0 < 2.0
    assert p.encode_text("ahoj Peter4 a Nick77 @user_3") == "ahoj [P4] a [P77] [P3]"
    texts = ["Ahoj, ako sa máš? Videl som Petra4 včera v meste o 18:00."] * 5000
    t0 = time.perf_counter()
    for t in texts:
        p.encode_text(t)
    assert time.perf_counter() - t0 < 5.0


# ---- generic words in display names (found in a live run: "user" was hidden) ------------


def test_generic_words_and_bios_are_not_name_parts() -> None:
    p = Pseudonymizer(
        [
            PersonEntry(1, "Owner Person", is_owner=True),
            PersonEntry(2, "Instagram User (instagramuser_18011610128553985)"),
            PersonEntry(3, "Matej Tóth | Software Developer"),
            PersonEntry(4, "Katarina Knechtova (official)"),
        ],
        [],
    )
    text = "Ask the user. Instagram is official software for a developer."
    assert p.encode_text(text) == text
    assert p.encode_text("Matej said hi to Katarina") == "[P2] said hi to [P3]"
    assert p.encode_text("Tóthovi") == "[P2]"
    assert p.encode_text("Matej Tóth | Software Developer") == "[P2]"
