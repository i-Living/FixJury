"""Analytics over published snapshots (PLAN_V1.1 §5.2)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from fixjury.metrics.analytics import AnalyticsError, SnapshotRef, build_analytics
from fixjury.report.quality import render_analytics

DAY = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def payload(
    *,
    acceptance: float | None = 0.5,
    decided: int = 10,
    accepted: int = 5,
    tokens: int = 1000,
    cost: str = "1.0000",
    judge_tokens: int = 500,
    judge_cost: str = "0.1000",
    verdict_source: str = "judge",
) -> dict[str, object]:
    return {
        "agent": "baseline",
        "cost_basis": "list_price",
        "pricing_version": "v1",
        "verdict_source": verdict_source,
        "denominators": {
            "pairs": 12,
            "decided_pairs": decided,
            "accepted": accepted,
            "regression_free_accepted": accepted,
        },
        "metrics": {
            "acceptance_rate": acceptance,
            "regression_free_acceptance_rate": acceptance,
            "tokens_total": tokens,
            "cost_total": cost,
            "cost_per_accepted_fix": cost,
            "tokens_per_accepted_fix": "200.00",
            "median_time_to_repair_s": 42.0,
            "judge_tokens_total": judge_tokens,
            "judge_cost_total": judge_cost,
        },
        "judge": {"evaluations": 10, "escalated": 2, "judge_config_hashes": ["h"]},
        "calibration": {
            "labeled_runs": 10,
            "false_acceptance": {"count": 0, "of": 5, "rate": 0.0},
            "false_rejection": {"count": 1, "of": 5, "rate": 0.2},
            "brier": 0.18,
        },
    }


def ref(
    *,
    agent: str = "baseline",
    when: datetime = DAY,
    snapshot_id: str = "s1",
    cost_basis: str = "list_price",
    pricing_version: str | None = "v1",
    body: dict[str, object] | None = None,
) -> SnapshotRef:
    return SnapshotRef(
        snapshot_id=snapshot_id,
        agent=agent,
        computed_at=when,
        verdict_source=str((body or payload()).get("verdict_source", "human")),
        config_hash="c" * 64,
        cost_basis=cost_basis,
        pricing_version=pricing_version,
        payload=body or payload(),
    )


def test_the_newest_snapshot_per_agent_is_the_standing() -> None:
    report = build_analytics(
        [
            ref(when=DAY, snapshot_id="old", body=payload(acceptance=0.1)),
            ref(when=DAY + timedelta(days=1), snapshot_id="new", body=payload(acceptance=0.9)),
        ]
    )

    assert report.agents[0].snapshot_id == "new"
    assert report.agents[0].acceptance_rate == 0.9
    # History keeps both, oldest first: a snapshot is a point in time, not a replacement.
    assert [entry.snapshot_id for entry in report.history] == ["old", "new"]


def test_a_window_selects_snapshots_and_refuses_an_impossible_one() -> None:
    report = build_analytics(
        [
            ref(when=DAY, snapshot_id="inside"),
            ref(when=DAY + timedelta(days=10), snapshot_id="outside"),
        ],
        since=date(2026, 11, 9),
        until=date(2026, 11, 11),
    )

    assert [entry.snapshot_id for entry in report.history] == ["inside"]

    with pytest.raises(AnalyticsError, match="starts after it ends"):
        build_analytics([ref()], since=date(2026, 11, 11), until=date(2026, 11, 10))


def test_an_empty_selection_is_refused_with_the_reason() -> None:
    with pytest.raises(AnalyticsError, match="no snapshot matches"):
        build_analytics([ref()], agent="mini")


def test_totals_are_published_only_when_the_snapshots_share_a_basis_and_version() -> None:
    comparable = build_analytics([ref(agent="baseline"), ref(agent="mini", snapshot_id="s2")])
    assert comparable.totals is not None
    assert comparable.totals["accepted"] == 10
    assert comparable.totals["cost_total"] == "2.0000"

    mixed = build_analytics(
        [
            ref(agent="baseline"),
            ref(agent="mini", snapshot_id="s2", pricing_version="v2"),
        ]
    )
    assert mixed.totals is None
    assert any("different cost bases" in note or "pricing versions" in note for note in mixed.notes)


def test_a_rate_is_never_averaged_across_agents() -> None:
    report = build_analytics(
        [
            ref(agent="baseline", body=payload(acceptance=0.2)),
            ref(agent="mini", snapshot_id="s2", body=payload(acceptance=0.8)),
        ]
    )

    assert {agent.agent: agent.acceptance_rate for agent in report.agents} == {
        "baseline": 0.2,
        "mini": 0.8,
    }
    assert any("never averaged" in note for note in report.notes)


def test_a_v1_snapshot_is_read_under_its_old_names() -> None:
    legacy = {
        "agent": "old",
        "cost_basis": "list_price",
        "pricing_version": "v0",
        "denominators": {"pairs": 4, "reviewed_pairs": 4, "accepted": 2},
        "metrics": {"human_acceptance_rate": 0.5, "regression_free_acceptance_rate": 0.25},
    }

    report = build_analytics([ref(snapshot_id="legacy", pricing_version="v0", body=legacy)])

    assert report.agents[0].decided == 4
    assert report.agents[0].acceptance_rate == 0.5
    assert report.agents[0].verdict_source == "human", "a snapshot without the key is human"
    assert report.agents[0].judge_cost_total == 0


def test_the_judge_statistics_come_from_the_snapshot_blocks() -> None:
    report = build_analytics([ref()])

    agent = report.agents[0]
    assert agent.evaluations == 10
    assert agent.escalated == 2
    assert agent.judge_tokens_total == 500
    assert agent.judge_cost_total == Decimal("0.1000")
    assert agent.false_acceptance_rate == 0.0
    assert agent.false_rejection_rate == 0.2
    assert agent.brier == 0.18


def test_each_agent_carries_the_basis_and_version_its_money_was_priced_under() -> None:
    report = build_analytics(
        [
            ref(agent="baseline", body=payload(cost="1.0000")),
            ref(
                agent="mini",
                snapshot_id="s2",
                cost_basis="amortized",
                pricing_version="v2",
                body=payload(cost="2.0000"),
            ),
        ]
    )

    baseline, mini = report.agents
    assert (baseline.cost_basis, baseline.pricing_version) == ("list_price", "v1")
    assert (mini.cost_basis, mini.pricing_version) == ("amortized", "v2")

    text = render_analytics(report)
    assert "1.0000 (list_price / v1)" in text
    assert "2.0000 (amortized / v2)" in text
