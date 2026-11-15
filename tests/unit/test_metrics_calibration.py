"""Calibration arithmetic (PLAN_V1.1 §2.7), pinned with hand-counted fixtures.

The numbers matter more than the shape: a false acceptance is a patch the held-out suite
failed and the judge called fixed, so every denominator here is a pair the held-out suite
actually answered.
"""

from __future__ import annotations

import pytest

from fixjury.metrics.calibration import (
    THRESHOLDS,
    CalibrationThresholds,
    agreement,
    as_calibration_payload,
    calibrate,
    escalation_rate,
    false_acceptance,
    false_rejection,
    inconclusive_rate,
)
from fixjury.metrics.compute import Attempt


def judged(
    task_id: str,
    *,
    verdict: str | None = "ACCEPTED",
    heldout: bool | None = True,
    confidence: float | None = 0.8,
    votes: tuple[str, ...] = (),
    escalated: bool = False,
    status: str = "finished",
) -> Attempt:
    """One judged attempt: the judge's verdict, its votes and the label it is scored against."""

    return Attempt(
        task_id=task_id,
        agent="baseline",
        status=status,
        has_diff=True,
        heldout_pass=heldout,
        policy_pass=True,
        judge_verdict=verdict,
        judge_confidence=confidence,
        judge_verdicts=votes or ((verdict,) if verdict else ()),
        judge_escalated=escalated,
        judge_evaluations=1 if verdict else 0,
    )


def test_false_acceptance_counts_wrong_accepts_over_failed_pairs() -> None:
    rate = false_acceptance(
        [
            judged("T-1", verdict="ACCEPTED", heldout=False),
            judged("T-2", verdict="REJECTED", heldout=False),
            judged("T-3", verdict="ACCEPTED", heldout=True),
        ]
    )

    assert rate.count == 1
    assert rate.of == 2, "the denominator is the pairs the held-out suite failed"
    assert rate.rate == pytest.approx(0.5)


def test_false_rejection_counts_wrong_rejects_over_passing_pairs() -> None:
    rate = false_rejection(
        [
            judged("T-1", verdict="REJECTED", heldout=True),
            judged("T-2", verdict="ACCEPTED", heldout=True),
            judged("T-3", verdict="REJECTED", heldout=False),
        ]
    )

    assert rate.count == 1
    assert rate.of == 2
    assert rate.rate == pytest.approx(0.5)


def test_a_pair_the_heldout_never_answered_is_unlabelled_not_a_judge_error() -> None:
    report = calibrate(
        [
            judged("T-1", verdict="ACCEPTED", heldout=None),
            judged("T-2", verdict="ACCEPTED", heldout=True),
        ]
    )

    assert report.labeled_pairs == 1
    assert report.unlabeled_pairs == 1
    assert report.false_acceptance.of == 0, "no failed pair, no false acceptance to measure"


def test_an_inconclusive_evaluation_is_counted_and_never_a_rejection() -> None:
    rate = inconclusive_rate(
        [
            judged("T-1", verdict="INCONCLUSIVE", heldout=True),
            judged("T-2", verdict="ACCEPTED", heldout=True),
        ]
    )

    assert rate.count == 1
    assert rate.of == 2
    assert false_rejection([judged("T-1", verdict="INCONCLUSIVE", heldout=True)]).count == 0


def test_agreement_is_measured_between_judges_of_one_evaluation() -> None:
    rate = agreement(
        [
            judged("T-1", votes=("ACCEPTED", "ACCEPTED")),
            judged("T-2", votes=("ACCEPTED", "REJECTED", "ACCEPTED")),
            # One judge is nothing to agree with, so it contributes no comparison.
            judged("T-3", votes=("ACCEPTED",)),
        ]
    )

    assert rate.count == 2, "one agreeing pair in the first, one in (A,R),(A,A),(R,A)"
    assert rate.of == 4
    assert rate.rate == pytest.approx(0.5)


def test_a_single_judge_corpus_has_no_agreement_rate() -> None:
    rate = agreement([judged("T-1", votes=("ACCEPTED",))])

    assert rate.of == 0
    assert rate.rate is None, "no comparison is not perfect agreement"


def test_escalation_frequency_is_over_evaluations() -> None:
    rate = escalation_rate(
        [
            judged("T-1", escalated=True),
            judged("T-2"),
            judged("T-3", verdict=None),
        ]
    )

    assert rate.count == 1
    assert rate.of == 2


def test_brier_scores_confident_mistakes_worse_than_unsure_ones() -> None:
    confident = calibrate([judged("T-1", verdict="ACCEPTED", heldout=False, confidence=0.95)])
    unsure = calibrate([judged("T-1", verdict="ACCEPTED", heldout=False, confidence=0.55)])

    assert confident.brier == pytest.approx(0.9025, abs=0.001)
    assert unsure.brier == pytest.approx(0.3025, abs=0.001)
    assert confident.brier > unsure.brier


def test_confidence_bins_report_claimed_against_observed() -> None:
    report = calibrate(
        [
            judged("T-1", verdict="ACCEPTED", heldout=True, confidence=0.9),
            judged("T-2", verdict="ACCEPTED", heldout=False, confidence=0.9),
        ]
    )

    top = report.bins[-1]
    assert top.count == 2
    assert top.claimed == pytest.approx(0.9)
    assert top.observed == pytest.approx(0.5)


def test_nothing_judged_means_thresholds_are_not_measured_not_met() -> None:
    report = calibrate([judged("T-1", verdict=None)])

    assert report.meets_thresholds is None
    assert report.violations() == ()


def test_thresholds_are_cleared_by_a_judge_that_was_right() -> None:
    report = calibrate(
        [
            judged("T-1", verdict="ACCEPTED", heldout=True),
            judged("T-2", verdict="REJECTED", heldout=False),
        ]
    )

    assert report.meets_thresholds is True
    assert report.violations() == ()


def test_a_false_acceptance_above_the_line_is_reported_in_words() -> None:
    report = calibrate(
        [
            judged("T-1", verdict="ACCEPTED", heldout=False),
            judged("T-2", verdict="REJECTED", heldout=False),
        ],
        thresholds=CalibrationThresholds(),
    )

    assert report.meets_thresholds is False
    assert report.violations()
    assert "false acceptance" in report.violations()[0]
    assert THRESHOLDS.false_acceptance_rate_max == CalibrationThresholds().false_acceptance_rate_max


def test_judge_disagreement_is_read_against_its_own_line() -> None:
    report = calibrate(
        [
            judged("T-1", heldout=True, votes=("ACCEPTED", "REJECTED")),
            judged("T-2", heldout=True, votes=("ACCEPTED", "REJECTED")),
        ]
    )

    assert report.agreement.rate == 0.0
    assert report.meets_thresholds is False
    assert any("disagreement" in item for item in report.violations())


def test_the_payload_carries_counts_and_denominators_never_bare_rates() -> None:
    payload = as_calibration_payload(
        calibrate([judged("T-1", verdict="ACCEPTED", heldout=False, confidence=0.7)])
    )

    assert payload["reference"] == "held-out suite pass/fail"
    false_accept = payload["false_acceptance"]
    assert isinstance(false_accept, dict)
    assert false_accept == {"count": 1, "of": 1, "rate": 1.0}
    assert payload["meets_thresholds"] is False
    assert isinstance(payload["bins"], list)
