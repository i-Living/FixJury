"""Leaderboard ordering (PLAN_V1.1 §3.5), over payloads a snapshot already computed."""

from __future__ import annotations

import pytest

from fixjury.leaderboard import (
    MIN_RANKED_SAMPLE,
    LeaderboardError,
    as_payload,
    build_leaderboard,
    render_markdown,
)


def block(
    *,
    pairs: int = 10,
    decided: int = 10,
    accepted: int = 6,
    clean: int = 5,
    acceptance: float | None = 0.6,
    ci: tuple[float, float] | None = (0.3, 0.85),
    cost: str | None = "0.5000",
    tokens: str | None = "1200.00",
    median: float | None = 40.0,
) -> dict[str, object]:
    return {
        "denominators": {
            "pairs": pairs,
            "decided_pairs": decided,
            "accepted": accepted,
            "regression_free_accepted": clean,
        },
        "split": {"regression_free": None, "with_regression": None, "not_accepted": None},
        "metrics": {
            "acceptance_rate": acceptance,
            "acceptance_ci": None if ci is None else {"low": ci[0], "high": ci[1]},
            "regression_free_acceptance_rate": clean / decided if decided else None,
            "tokens_per_accepted_fix": tokens,
            "cost_per_accepted_fix": cost,
            "median_time_to_repair_s": median,
        },
    }


def payload(
    agent: str,
    *,
    overall: dict[str, object] | None = None,
    difficulty: dict[str, dict[str, object]] | None = None,
    source: str = "judge",
    config_hash: str = "a" * 64,
    judge_hashes: tuple[str, ...] = ("b" * 64,),
) -> dict[str, object]:
    return {
        "agent": agent,
        "cost_basis": "list_price",
        "pricing_version": "v",
        "verdict_source": source,
        "config_hash": config_hash,
        "judge": {"judge_config_hashes": list(judge_hashes)},
        "groups": {"difficulty": difficulty or {}},
        **(overall or block()),
    }


def test_the_agent_with_the_better_regression_free_rate_ranks_first() -> None:
    board = build_leaderboard(
        [
            payload("baseline", overall=block(clean=5)),
            payload("mini", overall=block(clean=8)),
        ],
        corpus_version="corpus-1",
    )

    ranked = [entry.agent for entry in board.entries if entry.ranked]
    assert ranked == ["mini", "baseline"]
    assert [entry.rank for entry in board.entries if entry.ranked] == [1, 2]


def test_a_thin_sample_is_listed_but_not_ranked() -> None:
    """Four pairs produce an interval wider than forty points: no rank, and it says so."""

    thin = block(pairs=4, decided=4, accepted=4, clean=4)

    board = build_leaderboard(
        [payload("baseline", overall=block(clean=5)), payload("mini", overall=thin)],
        corpus_version="corpus-1",
    )

    mini = next(entry for entry in board.entries if entry.agent == "mini")
    assert mini.rank is None
    assert mini.ranked is False
    assert mini.decided == 4
    assert any(str(MIN_RANKED_SAMPLE) in note for note in board.notes)


def test_an_agent_with_nothing_accepted_has_no_cost_and_still_ranks_under_a_measured_one() -> None:
    board = build_leaderboard(
        [
            payload(
                "empty",
                overall=block(acceptance=0.0, clean=0, accepted=0, cost=None, tokens=None),
            ),
            payload("baseline", overall=block()),
        ],
        corpus_version="corpus-1",
    )

    empty = next(entry for entry in board.entries if entry.agent == "empty")
    baseline = next(entry for entry in board.entries if entry.agent == "baseline")
    assert empty.cost_per_accepted_fix is None
    assert baseline.rank == 1
    assert empty.rank == 2


def test_two_verdict_sources_are_not_ranked_together() -> None:
    with pytest.raises(LeaderboardError, match="different verdict sources"):
        build_leaderboard(
            [payload("baseline", source="judge"), payload("mini", source="human")],
            corpus_version="corpus-1",
        )


def test_an_unrecorded_dimension_is_refused_with_what_the_corpus_has() -> None:
    with pytest.raises(LeaderboardError, match="cannot rank by 'language'"):
        build_leaderboard([payload("baseline")], corpus_version="corpus-1", dimension="language")


def test_an_empty_corpus_has_no_board() -> None:
    with pytest.raises(LeaderboardError, match="at least one snapshot"):
        build_leaderboard([], corpus_version="corpus-1")


def test_the_difficulty_dimension_ranks_each_group_on_its_own() -> None:
    board = build_leaderboard(
        [
            payload(
                "baseline",
                difficulty={
                    "easy": block(decided=6, clean=5),
                    "hard": block(decided=6, clean=2),
                },
            ),
            payload(
                "mini",
                difficulty={
                    "easy": block(decided=6, clean=3),
                    "hard": block(decided=6, clean=4),
                },
            ),
        ],
        corpus_version="corpus-1",
        dimension="difficulty",
    )

    groups = board.by_group()
    assert [entry.agent for entry in groups["easy"] if entry.ranked] == ["baseline", "mini"]
    assert [entry.agent for entry in groups["hard"] if entry.ranked] == ["mini", "baseline"]


def test_the_board_names_the_revision_and_the_configuration_it_ranked() -> None:
    board = build_leaderboard(
        [payload("baseline", config_hash="c" * 64)],
        corpus_version="corpus-1",
    )
    text = render_markdown(board)

    assert "corpus-1" in text
    assert "c" * 64 in text
    assert "b" * 64 in text
    assert "judge" in text
    assert "V1.1" not in text


def test_a_legacy_payload_is_read_without_inventing_a_group() -> None:
    legacy = {
        "agent": "old",
        "cost_basis": "list_price",
        "pricing_version": "v",
        "denominators": {
            "pairs": 10,
            "reviewed_pairs": 10,
            "accepted": 6,
            "regression_free_accepted": 5,
        },
        "metrics": {
            "human_acceptance_rate": 0.6,
            "human_acceptance_ci": {"low": 0.3, "high": 0.85},
            "regression_free_acceptance_rate": 0.5,
            "tokens_per_accepted_fix": "1.00",
            "cost_per_accepted_fix": "0.10",
            "median_time_to_repair_s": 1.0,
        },
    }

    board = build_leaderboard([legacy], corpus_version="corpus-1")

    entry = board.entries[0]
    assert entry.decided == 10
    assert entry.acceptance_rate == 0.6
    assert entry.rank == 1
    assert board.verdict_source == "human"

    by_difficulty = build_leaderboard([legacy], corpus_version="corpus-1", dimension="difficulty")
    assert by_difficulty.entries == (), "a snapshot without groups has nothing to group"


def test_the_json_payload_carries_the_same_numbers() -> None:
    board = build_leaderboard([payload("baseline")], corpus_version="corpus-1")

    described = as_payload(board)

    assert described["corpus_version"] == "corpus-1"
    assert described["dimension"] == "overall"
    entries = described["entries"]
    assert isinstance(entries, list) and entries
    assert entries[0]["agent"] == "baseline"
    assert entries[0]["decided_pairs"] == 10
