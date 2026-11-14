from __future__ import annotations

from pathlib import Path

import pytest

from fixjury.report.charts import corpus_split, render_charts, render_regression_split

DENOMINATORS: dict[str, object] = {
    "pairs": 10,
    "reviewed_pairs": 10,
    "accepted": 4,
    "regression_free_accepted": 3,
    "automated_pass_pairs": 6,
    "declared_success_runs": 8,
    "missing_heldout_pairs": 1,
}

METRICS: dict[str, object] = {
    "human_acceptance_rate": 0.4,
    "human_acceptance_ci": {"low": 0.168, "high": 0.687},
    "regression_free_acceptance_rate": 0.3,
    "regression_free_acceptance_ci": {"low": 0.108, "high": 0.603},
    "task_success_rate": 0.6,
    "tokens_per_accepted_fix": "1250.00",
    "cost_per_accepted_fix": "0.4000",
    "median_time_to_repair_s": 42.5,
    "false_completion_rate": 0.5,
    "tokens_total": 5000,
    "cost_total": "1.6000",
}

PAYLOAD: dict[str, object] = {
    "agent": "baseline",
    "cost_basis": "list_price",
    "pricing_version": "unpriced-2026-11",
    "denominators": DENOMINATORS,
    "exclusions": {"infra_failed": 2},
    "metrics": METRICS,
}


def test_every_chart_is_written(tmp_path: Path) -> None:
    charts = render_charts([PAYLOAD], tmp_path)
    charts["regressions"] = render_regression_split([PAYLOAD], tmp_path)

    assert set(charts) == {"acceptance", "regression_free", "cost", "regressions"}
    for path in charts.values():
        assert path.exists()
        assert path.stat().st_size > 1000, "a real PNG, not an empty file"
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_null_cost_is_drawn_as_a_gap_not_as_zero(tmp_path: Path) -> None:
    payload = dict(PAYLOAD)
    payload["metrics"] = {**METRICS, "cost_per_accepted_fix": None}

    charts = render_charts([payload], tmp_path)

    assert charts["cost"].exists()


def test_two_agents_fit_on_one_chart(tmp_path: Path) -> None:
    other = {**PAYLOAD, "agent": "swe-agent"}

    charts = render_charts([PAYLOAD, other], tmp_path)

    assert charts["acceptance"].exists()


def test_the_corpus_split_is_computed_once_and_adds_up_to_one() -> None:
    split = corpus_split(PAYLOAD)

    assert split == {"regression_free": 0.3, "with_regression": 0.1, "not_accepted": 0.6}
    assert sum(split.values()) == pytest.approx(1.0)


def test_a_zero_denominator_gives_an_empty_split_not_a_division_error() -> None:
    payload = {**PAYLOAD, "denominators": {**DENOMINATORS, "pairs": 0, "reviewed_pairs": 0}}

    assert set(corpus_split(payload).values()) == {0.0}


def test_a_regression_free_count_above_the_accepted_count_is_clamped() -> None:
    payload = {
        **PAYLOAD,
        "denominators": {**DENOMINATORS, "accepted": 2, "regression_free_accepted": 3},
    }

    split = corpus_split(payload)

    assert split["regression_free"] == pytest.approx(0.2)
    assert split["with_regression"] == 0.0
    assert sum(split.values()) == pytest.approx(1.0), "a stack that ends past 100% is a lie"
