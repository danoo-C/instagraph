"""Emoji analysis (§6.6) with hand-calculated expectations."""

from typing import Any

import polars as pl
import pytest

from instagraph.analysis import emoji as E
from instagraph.api.filters import add_local_time
from instagraph.resources import EMOJI_CATEGORIES, emoji_category, emoji_category_map, heart_emojis
from instagraph.testing import build_dataset

PEOPLE: list[dict[str, Any]] = [{"display_name": "Me", "is_owner": True}, {"display_name": "Jana"}]


def frame(messages: list[dict[str, Any]]) -> pl.DataFrame:
    rows = [
        {"thread_id": 0, "ts": f"2024-01-0{1 + i // 50}T10:{i % 50:02d}:00Z", **m}
        for i, m in enumerate(messages)
    ]
    ds = build_dataset(PEOPLE, [{"title": "t", "participants": [0, 1]}], rows)
    return add_local_time(ds.messages, "UTC").collect()


FAMILY = "👨‍👩‍👧"
WOMAN_SHRUG_DARK = "🤷🏿‍♀️"


def test_zwj_and_flags_are_one_emoji_and_skin_tones_merge() -> None:
    df = frame(
        [
            {"sender_id": 0, "text": f"{FAMILY} 🇸🇰 #️⃣"},
            {"sender_id": 1, "text": "👍🏽👍 ❤️ ❤"},
            {"sender_id": 1, "text": WOMAN_SHRUG_DARK},
        ]
    )
    merged = E.emoji_long(df).collect()["emoji"].to_list()
    assert merged == [FAMILY, "🇸🇰", "#⃣", "👍", "👍", "❤", "❤", "🤷‍♀"]
    kept = E.emoji_long(df, merge_skin_tones=False).collect()["emoji"].to_list()
    assert kept[3:5] == ["👍🏽", "👍"]
    assert kept[-1] == "🤷🏿‍♀"
    gendered = E.emoji_long(df, merge_gendered=True).collect()["emoji"].to_list()
    assert gendered[-1] == "🤷"


def test_totals() -> None:
    df = frame(
        [
            {"sender_id": 0, "text": "ahoj 😂😂"},
            {"sender_id": 0, "text": "nič"},
            {"sender_id": 0, "text": "😂 🥰"},  # emoji-only (whitespace allowed)
            {"sender_id": 1, "text": "👍🏽👍"},  # emoji-only, unique 1 after merging
            {"sender_id": 1, "type": "like", "text": "❤"},  # not a text message
        ]
    )
    t = E.emoji_totals(df)
    assert t[0].text_messages == 3 and t[0].total == 4 and t[0].unique == 2
    assert t[0].messages_with_emoji == 2 and t[0].emoji_only_messages == 1
    assert t[0].per_message == pytest.approx(4 / 3)
    assert t[0].with_emoji_rate == pytest.approx(2 / 3)
    assert (t[1].total, t[1].unique, t[1].emoji_only_messages) == (2, 1, 1)
    assert E.emoji_totals(df, merge_skin_tones=False)[1].unique == 2
    assert E.emoji_per_message(df) == {0: pytest.approx(4 / 3), 1: 2.0}


def test_top_emojis() -> None:
    df = frame([{"sender_id": 0, "text": "😂😂🔥"}, {"sender_id": 1, "text": "🔥😂 ❤️"}])
    assert E.top_emojis(df, 2) == [("😂", 3), ("🔥", 2)]


def test_longest_chain_requires_adjacency() -> None:
    df = frame(
        [
            {"sender_id": 0, "text": "😂 😂 😂 😂"},  # 4 emojis, chain 1 → ignored
            {"sender_id": 0, "text": f"x 🔥{FAMILY}🇸🇰 y 😂"},  # chain of 3
            {"sender_id": 1, "text": "👍🏽👍"},
        ]
    )
    chains = E.longest_chains(df)
    assert (chains[0].length, chains[0].emojis, chains[0].message_id) == (3, f"🔥{FAMILY}🇸🇰", 1)
    assert (chains[1].length, chains[1].emojis) == (2, "👍👍")
    assert E.longest_chains(df, merge_skin_tones=False)[1].emojis == "👍🏽👍"


def test_signature_emoji_log_odds() -> None:
    df = frame(
        [{"sender_id": 0, "text": "😂😂😂❤️"}] * 3 + [{"sender_id": 1, "text": "🥰🥰🥰❤️"}] * 3
    )
    counts = E.emoji_counts(E.emoji_long(df).collect())
    sig = E.signature_emojis(counts)
    assert sig[0][0] == "😂" and sig[1][0] == "🥰"
    assert sig[0][1] == 9 and sig[0][2] > 0
    # one person only → no signature
    assert E.signature_emojis(counts.filter(pl.col("sender_id") == 0)) == {}


def test_emoticons() -> None:
    df = frame(
        [
            {"sender_id": 0, "text": "ahoj :) <3 xD"},
            {"sender_id": 0, "text": "https://x.com/:) no"},
            {"sender_id": 1, "text": ":D :D xd"},  # lower-case xd is laughter, not emoticon
        ]
    )
    rows = {(s, e): n for s, e, n in E.emoticon_counts(df).iter_rows()}
    assert rows == {(0, ":)"): 1, (0, "<3"): 1, (0, "xD"): 1, (1, ":D"): 2}


def test_categories_and_hearts() -> None:
    assert emoji_category("😂") == "smileys"
    assert emoji_category("❤️") == "hearts"
    assert emoji_category("👍🏽") == "people"
    assert emoji_category(WOMAN_SHRUG_DARK) == "people"
    assert emoji_category("🇸🇰") == "flags"
    assert emoji_category("🐶") == "animals"
    assert emoji_category("🍕") == "food"
    assert emoji_category("🚗") == "travel"
    assert emoji_category("⚽") == "activities"
    assert emoji_category("💻") == "objects"
    assert emoji_category("✅") == "symbols"
    assert set(emoji_category_map().values()) == set(EMOJI_CATEGORIES)
    assert {"❤", "🧡", "💛", "💚", "💙", "💜", "🖤", "🤍", "🤎", "💕", "💖"} <= heart_emojis()

    df = frame([{"sender_id": 0, "text": "😂❤️🐶"}, {"sender_id": 1, "text": "🫶🏽💙"}])
    long = E.emoji_long(df, merge_skin_tones=False).collect()
    cats = {(s, c): n for s, c, n in E.category_counts(long).iter_rows()}
    assert cats == {(0, "smileys"): 1, (0, "hearts"): 1, (0, "animals"): 1, (1, "hearts"): 2}
    assert long.filter(E.is_heart_expr()).height == 3
