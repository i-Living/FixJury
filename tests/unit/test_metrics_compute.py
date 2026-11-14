from __future__ import annotations

from decimal import Decimal

import pytest

from fixjury.metrics.compute import (
    Attempt,
    as_payload,
    compute_metrics,
    median,
    wilson_interval,
)


def attempt(task_id: str, **kwargs: object) -> Attempt:
    defaults: dict[str, object] = {
        "agent": "baseline",
        "status": "finished",
        "diff_sha256": "a" * 64,
        "has_diff": True,
        "tokens_in": 100,
        "tokens_out": 50,
        "automated_pass": True,
        "heldout_pass": True,
        "policy_pass": True,
    }
    defaults.update(kwargs)
    return Attempt(task_id=task_id, **defaults)  # type: ignore[arg-type]


def test_an_empty_corpus_is_not_a_division_by_zero() -> None:
    report = compute_metrics([], agent="baseline")

    assert report.pairs == 0
    # An empty corpus has no acceptance rate; zero would claim that nothing passed review.
    assert report.human_acceptance_rate is None
    assert report.human_acceptance_ci is None
    assert report.tokens_per_accepted_fix is None
    assert report.cost_per_accepted_fix is None


def test_a_pair_is_a_task_not_an_attempt() -> None:
    report = compute_metrics(
        [
            attempt("T-1", declared_success=False),
            attempt("T-1", declared_success=False),
            attempt("T-2", declared_success=False),
        ],
        agent="baseline",
    )

    assert report.pairs == 2


def test_an_infra_failed_run_is_not_an_attempt() -> None:
    report = compute_metrics(
        [attempt("T-1", status="infra_failed", diff_sha256=None)],
        agent="baseline",
    )

    assert report.pairs == 0
    assert report.exclusions == {"infra_failed": 1}


def test_a_finished_run_without_a_diff_counts_as_a_failed_pair() -> None:
    """A no-op is a result: leaving it out would make a silent agent look better."""

    report = compute_metrics(
        [attempt("T-1", diff_sha256=None, has_diff=False, heldout_pass=None)], agent="baseline"
    )

    assert report.pairs == 1
    assert report.no_patch_pairs == 1
    # A pair with no patch cannot have a held-out result, and that is not a defect.
    assert report.missing_heldout_pairs == 0


def test_acceptance_counts_pairs_with_any_accepted_attempt() -> None:
    report = compute_metrics(
        [
            attempt("T-1", human_verdict="reject"),
            attempt("T-1", human_verdict="accept"),
            attempt("T-2", human_verdict="reject"),
        ],
        agent="baseline",
    )

    assert report.pairs == 2
    assert report.accepted == 1
    assert report.human_acceptance_rate == 0.5


def test_regression_free_requires_acceptance_and_heldout_evidence() -> None:
    report = compute_metrics(
        [
            attempt("T-1", human_verdict="accept", heldout_pass=True),
            attempt("T-2", human_verdict="accept", heldout_pass=None),
            attempt("T-3", human_verdict="accept", heldout_pass=False),
        ],
        agent="baseline",
    )

    assert report.accepted == 3
    assert report.regression_free_accepted == 1
    assert report.missing_heldout_pairs == 1


def test_tokens_per_accepted_fix_counts_every_run_of_the_pair() -> None:
    report = compute_metrics(
        [
            attempt("T-1", human_verdict="accept", tokens_in=1000, tokens_out=0),
            attempt("T-1", human_verdict="reject", tokens_in=500, tokens_out=500),
        ],
        agent="baseline",
    )

    assert report.tokens_total == 2000
    assert report.tokens_per_accepted_fix == Decimal("2000.00")


def test_cost_per_accepted_fix_is_null_when_nothing_is_accepted() -> None:
    report = compute_metrics(
        [attempt("T-1", human_verdict="reject", cost_list_usd=Decimal("1.5"))],
        agent="baseline",
    )

    assert report.cost_per_accepted_fix is None
    assert report.cost_total == Decimal("1.5")


def test_cost_per_accepted_fix_divides_by_accepted_pairs() -> None:
    report = compute_metrics(
        [
            attempt("T-1", human_verdict="accept", cost_list_usd=Decimal("2.0")),
            attempt("T-2", human_verdict="reject", cost_list_usd=Decimal("2.0")),
        ],
        agent="baseline",
    )

    assert report.cost_per_accepted_fix == Decimal("4.0000")


def test_task_success_rate_counts_automated_pass_pairs() -> None:
    report = compute_metrics(
        [
            attempt("T-1", automated_pass=True),
            attempt("T-2", automated_pass=False),
        ],
        agent="baseline",
    )

    assert report.automated_pass_pairs == 1
    assert report.task_success_rate == 0.5


def test_false_completion_uses_declared_success_as_denominator() -> None:
    report = compute_metrics(
        [
            attempt("T-1", declared_success=True, human_verdict="reject"),
            attempt("T-2", declared_success=True, human_verdict="accept"),
            attempt("T-3", declared_success=None, human_verdict="reject"),
        ],
        agent="baseline",
    )

    assert report.declared_success_runs == 2
    assert report.false_completion_rate == 0.5


def test_false_completion_is_null_without_a_signal() -> None:
    report = compute_metrics([attempt("T-1", declared_success=None)], agent="baseline")

    assert report.false_completion_rate is None


def test_median_time_to_repair_uses_accepted_attempts() -> None:
    report = compute_metrics(
        [
            attempt("T-1", human_verdict="accept", duration_s=10.0),
            attempt("T-2", human_verdict="accept", duration_s=30.0),
            attempt("T-3", human_verdict="reject", duration_s=1000.0),
        ],
        agent="baseline",
    )

    assert report.median_time_to_repair_s == 20.0


def test_median_handles_even_and_odd_counts() -> None:
    assert median([1.0, 2.0, 3.0]) == 2.0
    assert median([1.0, 2.0, 3.0, 4.0]) == 2.5
    assert median([]) is None


def test_wilson_interval_brackets_the_rate() -> None:
    interval = wilson_interval(8, 10)

    assert interval is not None
    assert 0.49 < interval.low < 0.5
    assert 0.94 < interval.high < 0.95


def test_wilson_interval_with_no_observations_is_none() -> None:
    assert wilson_interval(0, 0) is None


def test_the_payload_carries_denominators_and_basis() -> None:
    report = compute_metrics(
        [attempt("T-1", human_verdict="accept", cost_list_usd=Decimal("0.5"))], agent="baseline"
    )

    payload = as_payload(report, cost_basis="list_price", pricing_version="unpriced-2026-11")

    assert payload["agent"] == "baseline"
    assert payload["cost_basis"] == "list_price"
    assert payload["pricing_version"] == "unpriced-2026-11"
    denominators = payload["denominators"]
    assert isinstance(denominators, dict)
    assert denominators["pairs"] == 1
    metrics = payload["metrics"]
    assert isinstance(metrics, dict)
    assert metrics["cost_per_accepted_fix"] == "0.5000"


@pytest.mark.parametrize("status", ["queued", "running", "cancelled"])
def test_only_finished_runs_count_as_attempts(status: str) -> None:
    report = compute_metrics([attempt("T-1", status=status)], agent="baseline")

    assert report.pairs == 0
