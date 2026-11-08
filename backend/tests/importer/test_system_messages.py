import json
from importlib import resources

import pytest

from instagraph.importer.system_messages import (
    available_locales,
    classify,
    detect_locales,
    enabled_locales,
    fold,
    load_locale,
    looks_like_system,
)
from instagraph.store.dataset import SYSTEM_EVENTS

P = ["Jana Nováková", "Daniel Šťastný", "Peter"]
ALL = ("en", "sk", "cs")


def test_available_locales() -> None:
    assert {"en", "sk", "cs"} <= set(available_locales())
    for code in available_locales():
        loc = load_locale(code)
        assert loc.patterns
        assert all(p.event is None or p.event in SYSTEM_EVENTS for p in loc.patterns)


def test_sk_cs_marked_unverified() -> None:
    pkg = resources.files("instagraph.importer.locales")
    for code in ("sk", "cs"):
        data = json.loads((pkg / f"{code}.json").read_text(encoding="utf-8"))
        assert data["verified"] is False
    assert load_locale("en").verified


def test_fold_preserves_length() -> None:
    s = "Páči sa mu správa čšťžýáíéúäôňĽĹŔ 😂"
    assert len(fold(s)) == len(s)
    assert fold("Zmeškaný hovor") == "Zmeskany hovor"


# (text, locale, type, event, payload)
CASES = [
    # EN
    ("Liked a message", "en", "like", None, None),
    ("Jana Nováková liked a message", "en", "like", None, None),
    ("You sent an attachment.", "en", "system", "attachment", None),
    ("Jana Nováková sent an attachment.", "en", "system", "attachment", None),
    ("Audio call ended", "en", "call", None, None),
    ("Video chat ended", "en", "call", None, None),
    ("Peter started an audio call", "en", "system", "call_event", None),
    ("Peter started a video chat", "en", "system", "call_event", None),
    ("You missed an audio call", "en", "call", None, None),
    ("Peter missed a video chat", "en", "call", None, None),
    ("Peter started sharing video", "en", "system", "call_event", None),
    ("This message was unsent", "en", "unsent", None, None),
    ("Peter unsent a message", "en", "unsent", None, None),
    ("Peter named the group Chata 2024", "en", "rename", "rename", "Chata 2024"),
    ("Peter added Jana Nováková to the group.", "en", "system", "join", "Jana Nováková"),
    ("Peter left the group.", "en", "system", "leave", "Peter"),
    ("Peter removed Jana from the group.", "en", "system", "removed", "Jana"),
    ("Peter changed the group photo.", "en", "system", "photo_change", None),
    ("Peter changed the theme to Love.", "en", "system", "theme_change", "Love"),
    ("Peter set the quick reaction to 🔥.", "en", "system", "emoji_change", "🔥"),
    ("Peter set the nickname for Jana to Janka.", "en", "system", "nickname", "Jana\tJanka"),
    ("Peter set his own nickname to Petko", "en", "system", "nickname", "Peter\tPetko"),
    ("Replied to your story", "en", "system", "story_reply", None),
    ("Mentioned you in their story", "en", "system", "story_mention", None),
    ("Peter made Jana an admin.", "en", "system", "admin", "Jana"),
    # SK (unverified strings, diacritics optional, masc/fem)
    ("Páči sa mu správa", "sk", "like", None, None),
    ("Paci sa jej sprava", "sk", "like", None, None),
    ("Jana Nováková poslala prílohu.", "sk", "system", "attachment", None),
    ("Peter poslal(a) prílohu.", "sk", "system", "attachment", None),
    ("Hlasový hovor sa skončil", "sk", "call", None, None),
    ("Videohovor sa skončil", "sk", "call", None, None),
    ("Zmeškali ste videohovor", "sk", "call", None, None),
    ("Peter začal hlasový hovor", "sk", "system", "call_event", None),
    ("Jana Nováková zrušila odoslanie správy", "sk", "unsent", None, None),
    ("Peter pomenoval skupinu Chata", "sk", "system", "rename", "Chata"),
    ("Peter pridal Jana do skupiny", "sk", "system", "join", "Jana"),
    ("Jana Nováková opustila skupinu", "sk", "system", "leave", "Jana Nováková"),
    ("Peter odstránil Jana zo skupiny", "sk", "system", "removed", "Jana"),
    ("Peter zmenil fotku skupiny", "sk", "system", "photo_change", None),
    ("Peter zmenil motív na Láska", "sk", "system", "theme_change", "Láska"),
    ("Peter nastavil prezývku pre Jana na Janka", "sk", "system", "nickname", "Jana\tJanka"),
    ("Odpovedala na váš príbeh", "sk", "system", "story_reply", None),
    ("Spomenul vás vo svojom príbehu", "sk", "system", "story_mention", None),
    # CZ
    ("Líbí se mu zpráva", "cs", "like", None, None),
    ("Jana Nováková poslala přílohu.", "cs", "system", "attachment", None),
    ("Hlasový hovor skončil", "cs", "call", None, None),
    ("Zmeškali jste videohovor", "cs", "call", None, None),
    ("Peter zrušil odeslání zprávy", "cs", "unsent", None, None),
    ("Peter pojmenoval skupinu Chalupa", "cs", "system", "rename", "Chalupa"),
    ("Peter přidal Jana do skupiny", "cs", "system", "join", "Jana"),
    ("Jana Nováková odešla ze skupiny", "cs", "system", "leave", "Jana Nováková"),
    ("Peter odebral Jana ze skupiny", "cs", "system", "removed", "Jana"),
    ("Peter změnil fotku skupiny", "cs", "system", "photo_change", None),
    ("Peter nastavil přezdívku pro Jana na Janička", "cs", "system", "nickname", "Jana\tJanička"),
    ("Odpověděla na váš příběh", "cs", "system", "story_reply", None),
]


@pytest.mark.parametrize(("text", "locale", "mtype", "event", "payload"), CASES)
def test_classify(
    text: str, locale: str, mtype: str, event: str | None, payload: str | None
) -> None:
    c = classify(text, locales=(locale,), participants=P)
    assert c is not None, text
    expected_type = "system" if mtype == "rename" else mtype
    assert c.type == expected_type
    assert c.system_event == event
    assert c.payload == payload
    assert c.pattern_id is not None and c.pattern_id.startswith(f"{locale}:")


def test_call_details() -> None:
    c = classify("You missed a video chat", locales=ALL, participants=P)
    assert c is not None and (c.call_kind, c.call_missed) == ("video", True)
    c = classify("Audio call ended", locales=ALL, participants=P)
    assert c is not None and (c.call_kind, c.call_missed) == ("audio", False)


@pytest.mark.parametrize(
    ("text", "emoji", "hint"),
    [
        ("Reacted 😂 to your message", "😂", None),
        ("Peter reacted ❤️ to your message", "❤", None),
        ("Reacted 👍🏽 to Jana's message", "👍🏽", "Jana"),
        ("Reagoval(a) 😂 na vašu správu", "😂", None),
        ("Reagovala 😮 na vasu spravu", "😮", None),
        ("Reagoval 😢 na vaši zprávu", "😢", None),
    ],
)
def test_reaction_as_text(text: str, emoji: str, hint: str | None) -> None:
    c = classify(text, locales=ALL, participants=P)
    assert c is not None and c.type is None
    assert c.reaction == (emoji, hint)
    assert c.system_event == "reaction_text"


@pytest.mark.parametrize(
    "text",
    [
        "I left the group",  # actor not a participant
        "Mom named the group chat",  # actor not a participant
        "reacted badly to your message",  # no emoji in the slot
        "ahoj, ako sa máš?",
        "liked a message from yesterday?",
        "",
        "Peter left the group " + "x" * 300,
    ],
)
def test_not_system(text: str) -> None:
    assert classify(text, locales=ALL, participants=P) is None


def test_actor_first_name_and_self_actor() -> None:
    assert classify("Jana left the group", locales=("en",), participants=P) is not None
    assert classify("You left the group", locales=("en",), participants=P) is not None
    # Without participants, the actor check is skipped (used for sampling).
    assert classify("Mom left the group", locales=("en",), participants=()) is not None


def test_locale_order_and_unknown_locale() -> None:
    assert classify("Liked a message", locales=("xx", "en"), participants=P) is not None
    assert classify("Liked a message", locales=(), participants=P) is None
    with pytest.raises(KeyError):
        load_locale("xx")


def _sample(n_system: int, texts: list[str], n_plain: int = 1000) -> list[str]:
    out = [f"plain message number {i}" for i in range(n_plain)]
    for i in range(n_system):
        out.append(texts[i % len(texts)])
    return out


def test_detect_locales_single() -> None:
    texts = _sample(30, ["Liked a message", "Audio call ended", "Peter left the group"])
    counts = detect_locales(texts)
    assert counts["en"] == 30
    assert counts["sk"] == 0 and counts["cs"] == 0
    assert enabled_locales(counts, len(texts)) == ["en"]


def test_detect_locales_mixed_language() -> None:
    sk = ["Páči sa mu správa", "Hlasový hovor sa skončil", "Peter poslal prílohu."]
    en = ["Liked a message", "You sent an attachment."]
    cs = ["Líbí se mu zpráva", "Zmeškali jste videohovor"]
    texts = _sample(25, sk) + _sample(40, en, 0) + _sample(3, cs, 0)
    counts = detect_locales(texts)
    assert counts["en"] >= 40 and counts["sk"] >= 25
    enabled = enabled_locales(counts, total_messages=1_000_000)
    assert enabled[:2] == ["en", "sk"]
    assert "cs" not in enabled  # 3 matches, < 20 and < 0.1 %
    # A small export: 3 of 1500 = 0.2 % ≥ 0.1 % → on.
    assert "cs" in enabled_locales({"cs": 3, "en": 0}, 1500)


def test_detect_locales_with_participants_per_text() -> None:
    counts = detect_locales(
        [("Mom left the group", ["Peter"]), ("Peter left the group", ["Peter"])]
    )
    assert counts["en"] == 1


def test_enabled_locales_fallback() -> None:
    assert enabled_locales({"en": 0, "sk": 0}, 100) == ["en"]
    assert enabled_locales({"sk": 25, "en": 0}, 10**7) == ["sk"]


def test_looks_like_system() -> None:
    assert looks_like_system("Peter pinned the poll", P)
    assert looks_like_system("Jana Nováková vytvorila anketu", P)
    assert looks_like_system("You started a poll", P)
    assert not looks_like_system("Peter, kde si?", P)
    assert not looks_like_system("Peter je super 😂", P)
    assert not looks_like_system("ahoj Peter", P)
    assert not looks_like_system("Peter Pan", P)


def test_every_pattern_compiles_and_has_keyword() -> None:
    # load_locale raises on a missing keyword / bad event; this checks all shipped files.
    for code in available_locales():
        loc = load_locale(code)
        assert loc.gate
        ids = [p.id for p in loc.patterns]
        assert len(ids) == len(set(ids))


# Masculine / feminine / "(a)" participle forms must all be recognised (SK, CS).
SK_GENDERED = [
    ("Michal Benková pomenoval{} skupinu Gymko IV.A.", "rename", "Gymko IV.A"),
    ("Eliška Brown pridal{} používateľa James Brown do skupiny.", "join", "James Brown"),
    ("Peter zmenil{} fotku skupiny.", "photo_change", None),
    ("Peter opustil{} skupinu.", "leave", "Peter"),
    ("Peter poslal{} prílohu.", "attachment", None),
    ("Peter odstránil{} používateľa Jana zo skupiny.", "removed", "Jana"),
    ("Peter zmenil{} názov skupiny na Chata", "rename", "Chata"),
]
CS_GENDERED = [
    ("Peter pojmenoval{} skupinu Chata.", "rename", "Chata"),
    ("Peter přidal{} uživatele Jana do skupiny.", "join", "Jana"),
    ("Peter změnil{} fotku skupiny.", "photo_change", None),
    ("Peter opustil{} skupinu.", "leave", "Peter"),
    ("Peter poslal{} přílohu.", "attachment", None),
    ("Peter odebral{} uživatele Jana ze skupiny.", "removed", "Jana"),
]


@pytest.mark.parametrize("suffix", ["", "a", "(a)"])
@pytest.mark.parametrize(
    ("locale", "template", "event", "payload"),
    [("sk", *c) for c in SK_GENDERED] + [("cs", *c) for c in CS_GENDERED],
)
def test_gendered_forms(
    locale: str, template: str, event: str, payload: str | None, suffix: str
) -> None:
    parts = ["Michal Benková", "Eliška Brown", "James Brown", "Peter", "Jana"]
    c = classify(template.format(suffix), locales=(locale,), participants=parts)
    assert c is not None and c.system_event == event
    if payload is not None:
        assert c.payload == payload


def test_you_forms_sk() -> None:
    for text in (
        "Pomenovali ste skupinu Chata.",
        "Pridali ste používateľa Jana do skupiny.",
        "Opustili ste skupinu.",
        "Zmenili ste fotku skupiny.",
        "Poslali ste prílohu.",
    ):
        assert classify(text, locales=("sk",), participants=P) is not None, text


def test_enabled_locales_ignores_overlap_of_relatives() -> None:
    from instagraph.importer.system_messages import detect_locale_matches

    sk = [
        "Peter opustil skupinu.",
        "Peter poslal prílohu.",
        "Peter zmenil fotku skupiny.",
        "Páči sa mu správa",  # Slovak only
    ]
    texts = _sample(30, sk) + _sample(40, ["Liked a message"], 0)
    matches = detect_locale_matches(texts)
    counts = {c: len(m) for c, m in matches.items()}
    assert counts["cs"] > 0  # Czech patterns match these Slovak texts too…
    assert enabled_locales(counts, len(texts), matches) == ["en", "sk"]  # …but stay off
