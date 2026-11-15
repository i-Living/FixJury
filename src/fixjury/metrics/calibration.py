"""Judge calibration (PLAN_V1.1 §2.7), computed from stored rows.

Calibration asks a question the acceptance metrics do not: when the judge says
``ACCEPTED``, how often is it right? The reference label is the held-out suite — the one
signal no model produced, and the one the benchmark already trusts to call a fix
regression-free. Human verdicts are reported beside it as a sparse second opinion: with
decision 1 the standard workflow has no human reviewers, so a rate is never computed from
labels that do not exist.

Five numbers leave this module, each with its denominator: false acceptance, false
rejection, inter-judge agreement, confidence calibration and escalation frequency. Every
one of them is ``None`` rather than zero when nothing was measured, and every one of them
is computed here, once, into the snapshot the report and the page read.

The unit is the **run**, not the pair. A retry is a second run, judged on its own, so it is
a second piece of evidence about the judge — while acceptance is measured per pair, because
an agent that retries must not look better than one that does not. The two count different
things on purpose, and every field here says ``runs`` so a reader cannot take one for the
other.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from fixjury.metrics.compute import Attempt

#: The label a judge is scored against. Named in the payload so a reader never has to
#: guess whether "known incorrect" meant a failing test or a reviewer's opinion.
REFERENCE_LABEL = "held-out suite pass/fail"

#: Slack for a comparison that is exact in decimal and not in binary floating point.
TOLERANCE = 1e-9


@dataclass(frozen=True)
class CalibrationThresholds:
    """The lines a judge has to stay under before its acceptance means something.

    They are targets a calibration run reports against, not gates that stop a benchmark:
    a corpus where the judge misses one is a corpus whose judge needs a better prompt, and
    hiding that number would be worse than publishing it.
    """

    false_acceptance_rate_max: float = 0.10
    false_rejection_rate_max: float = 0.20
    disagreement_rate_max: float = 0.30


THRESHOLDS = CalibrationThresholds()

CONFIDENCE_BINS: tuple[tuple[float, float], ...] = (
    (0.0, 0.2),
    (0.2, 0.4),
    (0.4, 0.6),
    (0.6, 0.8),
    (0.8, 1.0),
)


@dataclass(frozen=True)
class Rate:
    """A count, its denominator and the rate, or nothing when nothing was counted."""

    count: int = 0
    of: int = 0

    @property
    def rate(self) -> float | None:
        return self.count / self.of if self.of else None


@dataclass(frozen=True)
class ConfidenceBin:
    low: float
    high: float
    count: int
    claimed: float | None
    observed: float | None


@dataclass(frozen=True)
class CalibrationReport:
    """What the judge did, measured against labels it did not produce."""

    reference: str
    labeled_runs: int
    unlabeled_runs: int
    inconclusive_runs: int
    false_acceptance: Rate
    false_rejection: Rate
    agreement: Rate
    escalation: Rate
    brier: float | None
    bins: tuple[ConfidenceBin, ...]
    thresholds: CalibrationThresholds
    meets_thresholds: bool | None

    def violations(self) -> tuple[str, ...]:
        """Which lines the judge crossed, in words a report can print."""

        checked = []
        for name, measured, limit in (
            ("false acceptance", self.false_acceptance, self.thresholds.false_acceptance_rate_max),
            ("false rejection", self.false_rejection, self.thresholds.false_rejection_rate_max),
            ("judge disagreement", self.agreement, self.thresholds.disagreement_rate_max),
        ):
            rate = measured.rate
            if rate is None:
                continue
            # Agreement is the inverse of disagreement: it must clear 1 - the line.
            # `1.0 - 0.7` is 0.30000000000000004 in binary floating point, so a rate exactly at
            # the line has to compare with a tolerance or a legal value reads as a breach.
            if name == "judge disagreement":
                disagreement = 1.0 - rate
                if disagreement > limit + TOLERANCE:
                    checked.append(f"{name} {disagreement * 100:.1f}% above {limit * 100:.0f}%")
                continue
            if rate > limit + TOLERANCE:
                checked.append(f"{name} {rate * 100:.1f}% above {limit * 100:.0f}%")
        return tuple(checked)


def _reference_correct(run: Attempt) -> bool | None:
    """The held-out answer, or ``None`` when the pair was never tested.

    ``policy_pass`` is deliberately not part of the label: calibration is about the patch's
    behaviour, and a path violation is a rule the judge is told about separately.
    """

    return run.heldout_pass


def _judge_verdict(run: Attempt) -> str | None:
    """What the judge said, not what the critical-failure rules made of it.

    ``Attempt.judge_verdict`` is the final verdict: a failed held-out suite becomes a
    critical finding and the engine stores ``REJECTED`` whatever the judge thought. Scored
    against that, a judge that accepted a patch the held-out suite then failed would look
    like a correct rejection, and the false acceptance rate would understate exactly the
    mistake it exists to count.
    """

    return run.judge_assessment


def _labelled(attempts: Sequence[Attempt]) -> list[Attempt]:
    """Runs a judge ruled on and a held-out suite answered."""

    return [
        run
        for run in attempts
        if _judge_verdict(run) in {"ACCEPTED", "REJECTED"} and _reference_correct(run) is not None
    ]


def false_acceptance(attempts: Sequence[Attempt]) -> Rate:
    """Accepted by the judge, failed by the held-out suite, counted per run.

    The dangerous direction: a patch nobody tested was called fixed. A pair whose held-out
    suite could not run has no label and is not counted either way.
    """

    labelled = _labelled(attempts)
    wrong = [run for run in labelled if _reference_correct(run) is False]
    return Rate(count=sum(1 for run in wrong if _judge_verdict(run) == "ACCEPTED"), of=len(wrong))


def false_rejection(attempts: Sequence[Attempt]) -> Rate:
    """Rejected by the judge, passed by the held-out suite.

    Costly but safe: a correct fix called broken. The frame is every labelled pair the
    held-out suite passed, so the denominator is the pairs there was something to miss.
    """

    labelled = _labelled(attempts)
    right = [run for run in labelled if _reference_correct(run) is True]
    return Rate(count=sum(1 for run in right if _judge_verdict(run) == "REJECTED"), of=len(right))


def inconclusive_rate(attempts: Sequence[Attempt]) -> Rate:
    """Evaluations with no usable verdict, over every evaluation there is."""

    evaluated = [run for run in attempts if run.judge_evaluations > 0]
    undecided = sum(1 for run in evaluated if run.judge_verdict not in {"ACCEPTED", "REJECTED"})
    return Rate(count=undecided, of=len(evaluated))


def agreement(attempts: Sequence[Attempt]) -> Rate:
    """Share of judge pairs that reached the same verdict.

    Measured inside one evaluation, where the judges saw the same evidence; a run judged
    once contributes nothing, because there is nothing to agree with. Disagreement is
    ``1 - rate``, and that is the number the threshold is written against.
    """

    compared = 0
    agreeing = 0
    for run in attempts:
        verdicts = run.judge_verdicts
        for index, left in enumerate(verdicts):
            for right in verdicts[index + 1 :]:
                compared += 1
                agreeing += left == right
    return Rate(count=agreeing, of=compared)


def escalation_rate(attempts: Sequence[Attempt]) -> Rate:
    """Evaluations that asked another judge, over every evaluation."""

    evaluated = [run for run in attempts if run.judge_evaluations > 0]
    return Rate(count=sum(1 for run in evaluated if run.judge_escalated), of=len(evaluated))


def _confidence(run: Attempt) -> float | None:
    """The judge's confidence in its own verdict, when both a verdict and a label exist."""

    if run.judge_confidence is None or _reference_correct(run) is None:
        return None
    return float(run.judge_confidence)


def _was_right(run: Attempt) -> bool:
    """False when judging a patch correct was wrong about the held-out outcome."""

    return (_judge_verdict(run) == "ACCEPTED") == _reference_correct(run)


def _brier(attempts: Sequence[Attempt]) -> float | None:
    """Mean squared error between the judge's confidence and whether it was right.

    Confidence is read as a probability of being correct, which is what makes the number
    comparable across models. A judge that is confidently wrong scores worse than one that
    is unsure and wrong.
    """

    scored = [
        (confidence, _was_right(run))
        for run in _labelled(attempts)
        if (confidence := _confidence(run)) is not None
    ]
    if not scored:
        return None
    total = sum((confidence - (1.0 if right else 0.0)) ** 2 for confidence, right in scored)
    return round(total / len(scored), 4)


def _bins(attempts: Sequence[Attempt]) -> tuple[ConfidenceBin, ...]:
    """Claimed confidence against the observed accuracy in each band."""

    scored = [
        (confidence, _was_right(run))
        for run in _labelled(attempts)
        if (confidence := _confidence(run)) is not None
    ]
    bins: list[ConfidenceBin] = []
    for low, high in CONFIDENCE_BINS:
        # The last band includes 1.0; the others are half-open so a confidence lands once.
        members = [
            (confidence, right)
            for confidence, right in scored
            if low <= confidence < high or (high == 1.0 and confidence >= low)
        ]
        if not members:
            bins.append(ConfidenceBin(low=low, high=high, count=0, claimed=None, observed=None))
            continue
        claimed = sum(confidence for confidence, _ in members) / len(members)
        observed = sum(1 for _, right in members if right) / len(members)
        bins.append(
            ConfidenceBin(
                low=low,
                high=high,
                count=len(members),
                claimed=round(claimed, 4),
                observed=round(observed, 4),
            )
        )
    return tuple(bins)


def calibrate(
    attempts: Sequence[Attempt], *, thresholds: CalibrationThresholds = THRESHOLDS
) -> CalibrationReport:
    """Every §2.7 number for one set of judged attempts."""

    evaluated = [run for run in attempts if run.judge_evaluations > 0]
    labelled = _labelled(attempts)
    report = CalibrationReport(
        reference=REFERENCE_LABEL,
        labeled_runs=len(labelled),
        unlabeled_runs=len(evaluated) - len(labelled),
        inconclusive_runs=len(evaluated)
        - sum(1 for run in evaluated if run.judge_verdict in {"ACCEPTED", "REJECTED"}),
        false_acceptance=false_acceptance(attempts),
        false_rejection=false_rejection(attempts),
        agreement=agreement(attempts),
        escalation=escalation_rate(attempts),
        brier=_brier(attempts),
        bins=_bins(attempts),
        thresholds=thresholds,
        meets_thresholds=None,
    )
    if not labelled:
        # One unlabelled pair is not a clean sheet: with no label there is no evidence that
        # the judge clears any line, so the answer is "not measured" rather than "yes".
        return report
    return replace(report, meets_thresholds=not report.violations())


def as_calibration_payload(report: CalibrationReport) -> dict[str, object]:
    """The ``calibration`` block of a snapshot payload."""

    def rate(measured: Rate) -> dict[str, object]:
        return {"count": measured.count, "of": measured.of, "rate": measured.rate}

    return {
        "reference": report.reference,
        "labeled_runs": report.labeled_runs,
        "unlabeled_runs": report.unlabeled_runs,
        "inconclusive_runs": report.inconclusive_runs,
        "false_acceptance": rate(report.false_acceptance),
        "false_rejection": rate(report.false_rejection),
        "agreement": rate(report.agreement),
        "escalation": rate(report.escalation),
        "brier": report.brier,
        "bins": [
            {
                "low": item.low,
                "high": item.high,
                "count": item.count,
                "claimed": item.claimed,
                "observed": item.observed,
            }
            for item in report.bins
        ],
        "thresholds": {
            "false_acceptance_rate_max": report.thresholds.false_acceptance_rate_max,
            "false_rejection_rate_max": report.thresholds.false_rejection_rate_max,
            "disagreement_rate_max": report.thresholds.disagreement_rate_max,
        },
        "meets_thresholds": report.meets_thresholds,
        "violations": list(report.violations()),
    }


__all__ = [
    "CONFIDENCE_BINS",
    "REFERENCE_LABEL",
    "THRESHOLDS",
    "CalibrationReport",
    "CalibrationThresholds",
    "ConfidenceBin",
    "Rate",
    "agreement",
    "as_calibration_payload",
    "calibrate",
    "escalation_rate",
    "false_acceptance",
    "false_rejection",
    "inconclusive_rate",
]
