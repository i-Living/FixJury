"""Turning several assessments into one (SPEC §4.2, §4.5).

The policy is deliberately blunt and stated out loud: any independent judge that rejects
rejects, because a rejection is a claim about a specific defect and a second opinion does
not make the defect disappear. Only a unanimous acceptance is an acceptance, and anything
else is ``INCONCLUSIVE`` — the platform never averages a tie into a pass.

Scores are averaged per criterion so the stored score reflects every judge that answered;
findings are unioned, because two judges spotting different problems both spotted
something.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fixjury.evaluation.contracts import EvaluationFinding
from fixjury.judge.evaluator import Assessment
from fixjury.models import Criterion, EvaluationVerdict


@dataclass(frozen=True)
class Aggregation:
    """The combined reading of every assessment of one patch."""

    verdict: EvaluationVerdict | None
    confidence: float
    judge_scores: dict[Criterion, float] = field(default_factory=dict)
    findings: tuple[EvaluationFinding, ...] = ()
    disagreement: bool = False


def aggregate(assessments: tuple[Assessment, ...]) -> Aggregation:
    """Combine assessments; with none, there is nothing to decide."""

    if not assessments:
        return Aggregation(verdict=None, confidence=0.0)

    verdicts = [assessment.response.verdict for assessment in assessments]
    if any(verdict is EvaluationVerdict.REJECTED for verdict in verdicts):
        verdict = EvaluationVerdict.REJECTED
    elif all(verdict is EvaluationVerdict.ACCEPTED for verdict in verdicts):
        verdict = EvaluationVerdict.ACCEPTED
    else:
        verdict = EvaluationVerdict.INCONCLUSIVE

    confidence = round(
        sum(assessment.response.confidence for assessment in assessments) / len(assessments), 2
    )

    totals: dict[Criterion, list[float]] = {}
    for assessment in assessments:
        for item in assessment.response.criteria:
            totals.setdefault(item.criterion, []).append(item.score)
    judge_scores = {
        criterion: round(sum(values) / len(values), 2) for criterion, values in totals.items()
    }

    findings: list[EvaluationFinding] = []
    seen: set[tuple[str, str]] = set()
    for assessment in assessments:
        for finding in assessment.findings:
            key = (finding.evidence_ref, finding.description)
            if key in seen:
                continue
            seen.add(key)
            findings.append(finding)

    return Aggregation(
        verdict=verdict,
        confidence=confidence,
        judge_scores=judge_scores,
        findings=tuple(findings),
        disagreement=len(set(verdicts)) > 1,
    )


__all__ = ["Aggregation", "aggregate"]
