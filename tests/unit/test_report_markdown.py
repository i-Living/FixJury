from __future__ import annotations

from decimal import Decimal

import pytest

from fixjury.report.markdown import render_agent_table, render_report

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


def test_the_table_row_carries_every_headline_number() -> None:
    row = render_agent_table(PAYLOAD)

    # A payload written before V1.1 has no verdict_source: it is a human series.
    assert row.startswith(
        "| baseline | human (V1 snapshot) | 40.0% | 16.8-68.7% | 30.0% | 60.0% | 4/10 |"
    )
    assert "1250.00" in row
    assert "0.4000" in row
    assert "42.50 s" in row
    assert "50.0%" in row


def test_an_unreviewed_corpus_reports_null_acceptance() -> None:
    """A pair nobody reviewed is unknown, not rejected: the row must say null."""

    payload: dict[str, object] = {
        **PAYLOAD,
        "denominators": {**DENOMINATORS, "reviewed_pairs": 0, "accepted": 0},
        "metrics": {
            **METRICS,
            "human_acceptance_rate": None,
            "human_acceptance_ci": None,
            "regression_free_acceptance_rate": None,
            "regression_free_acceptance_ci": None,
            "false_completion_rate": None,
        },
    }

    row = render_agent_table(payload)

    assert row.startswith("| baseline | human (V1 snapshot) | null | null | null | 60.0% | 0/0 |")
    assert "0.0%" not in row.split("|")[2]


def test_a_null_cost_is_rendered_as_null_not_zero() -> None:
    payload: dict[str, object] = dict(PAYLOAD)
    payload["denominators"] = {**DENOMINATORS, "accepted": 0}
    payload["metrics"] = {
        **METRICS,
        "cost_per_accepted_fix": None,
        "tokens_per_accepted_fix": None,
        "false_completion_rate": None,
    }

    row = render_agent_table(payload)

    assert "| null |" in row
    assert "0.0000" not in row


def test_the_report_labels_the_cost_basis() -> None:
    text = render_report([PAYLOAD], basis="list_price")

    assert "list price" in text
    assert "not an invoice" in text
    assert "| Agent | Verdict source | Acceptance | 95% CI |" in text


def test_the_report_shows_exclusions_and_pricing_version() -> None:
    text = render_report([PAYLOAD])

    assert "infra_failed=2" in text
    assert "unpriced-2026-11" in text
    assert "missing held-out" in text.lower()


def test_an_empty_report_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one snapshot"):
        render_report([])


def test_two_agents_get_two_rows() -> None:
    other = {**PAYLOAD, "agent": "swe-agent"}

    text = render_report([PAYLOAD, other])

    assert text.count("| baseline ") == 2
    assert text.count("| swe-agent ") == 2


def test_decimal_values_are_formatted_not_printed_raw() -> None:
    payload: dict[str, object] = dict(PAYLOAD)
    payload["metrics"] = {**METRICS, "cost_per_accepted_fix": Decimal("0.4")}

    assert "0.40" in render_agent_table(payload)


def test_judge_usage_is_reported_beside_the_agent_totals() -> None:
    """A judge bill has to be visible, or "cost per accepted fix" hides half of what was spent."""

    payload: dict[str, object] = {
        **PAYLOAD,
        "verdict_source": "judge",
        "judge": {
            "evaluations": 2,
            "escalated": 1,
            "tokens_total": 2000,
            "cost_total": "0.2500",
            "pricing_versions": ["unpriced-2026-11"],
        },
    }

    text = render_report([payload])

    assert "## Judge usage" in text
    assert "| baseline | 2 | 1 | 2000 | 0.2500 | unpriced-2026-11 |" in text
    assert "5000" in text, "the agent's own tokens stay in their own column"


def test_an_amortized_report_says_the_judge_cost_is_still_a_list_price() -> None:
    """The two bases answer different questions; a table cannot lend its label to the other."""

    payload: dict[str, object] = {
        **PAYLOAD,
        "cost_basis": "amortized",
        "verdict_source": "judge",
        "judge": {
            "evaluations": 1,
            "escalated": 0,
            "tokens_total": 100,
            "cost_total": "0.0100",
            "pricing_versions": ["v"],
        },
    }

    text = render_report([payload], basis="amortized")

    assert "amortized" in text
    assert "Judge cost (list price)" in text
    assert "Agent cost (selected basis)" in text
    assert "The judge's cost is always a list-price reference" in text
