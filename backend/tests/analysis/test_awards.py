"""Award rules, thresholds and ties (``analysis.awards``)."""

from typing import Any

import polars as pl

from instagraph.analysis.awards import (
    AWARDS,
    AWARDS_BY_KEY,
    MIN_MESSAGES,
    awards_won_by,
    compute_awards,
    personality_summary,
)


def _by_key(rows: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    return {r.award.key: r for r in compute_awards(pl.DataFrame(rows), **kw)}


def test_catalogue_matches_spec() -> None:
    assert len(AWARDS) == 18
    assert {a.key for a in AWARDS} >= {"novelist", "speed_demon", "ghost", "second_thoughts"}


def test_rate_award_threshold_excludes_tiny_contributors() -> None:
    rows = [
        {"sender_id": 0, "text_messages": 100.0, "avg_words": 5.0},
        {"sender_id": 1, "text_messages": 3.0, "avg_words": 40.0},  # below threshold
    ]
    res = _by_key(rows)["novelist"]
    assert res.winners == [0]
    assert res.value == 5.0
    assert res.ranking == [(0, 5.0)]


def test_lower_is_better_and_min_replies() -> None:
    rows = [
        {"sender_id": 0, "replies": 10.0, "reply_median_ms": 60_000.0},
        {"sender_id": 1, "replies": 10.0, "reply_median_ms": 30_000.0},
        {"sender_id": 2, "replies": 2.0, "reply_median_ms": 1_000.0},
    ]
    res = _by_key(rows)["speed_demon"]
    assert res.winners == [1]
    assert res.value == 30_000
    assert [p for p, _ in res.ranking] == [1, 0]


def test_ties_and_count_awards_need_positive_value() -> None:
    rows = [
        {"sender_id": 0, "unsent": 3.0, "voice_notes": 0.0},
        {"sender_id": 1, "unsent": 3.0, "voice_notes": 0.0},
        {"sender_id": 2, "unsent": 1.0, "voice_notes": 0.0},
    ]
    res = _by_key(rows)
    assert res["second_thoughts"].winners == [0, 1]
    assert res["podcaster"].winners == []
    assert res["podcaster"].value is None


def test_null_metric_is_ignored_and_candidates_filter() -> None:
    rows = [
        {"sender_id": 0, "messages": 50.0, "night_share": None},
        {"sender_id": 1, "messages": 50.0, "night_share": 0.2},
        {"sender_id": 2, "messages": 50.0, "night_share": 0.4},
    ]
    assert _by_key(rows)["night_owl"].winners == [2]
    assert _by_key(rows, candidates={0, 1})["night_owl"].winners == [1]


def test_multi_threshold() -> None:
    rows = [
        {"sender_id": 0, "messages": float(MIN_MESSAGES), "turns": 9.0, "msgs_per_turn": 3.0},
        {"sender_id": 1, "messages": float(MIN_MESSAGES), "turns": 10.0, "msgs_per_turn": 1.5},
    ]
    assert _by_key(rows)["double_texter"].winners == [1]


def test_personality() -> None:
    rows = [{"sender_id": 0, "unsent": 2.0, "calls_started": 1.0}]
    won = awards_won_by(compute_awards(pl.DataFrame(rows)), 0)
    assert {r.award.key for r in won} == {"second_thoughts", "caller"}
    text = personality_summary(won)
    assert "Caller" in text and "Second Thoughts" in text and " and " in text
    assert "no awards" in personality_summary([])
    assert AWARDS_BY_KEY["caller"].eligibility() == "at least one"
    assert "≥ 20 messages" in AWARDS_BY_KEY["night_owl"].eligibility()
