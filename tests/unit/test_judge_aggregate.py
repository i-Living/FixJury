from __future__ import annotations

import json
from decimal import Decimal

from fixjury.evaluation.contracts import EvaluationFinding
from fixjury.judge.aggregate import aggregate
from fixjury.judge.evaluator import Assessment
from fixjury.judge.schema import JudgeResponse
from fixjury.models import Criterion, EvaluationVerdict, FindingSeverity, FindingSource


def assessment(
    *,
    verdict: EvaluationVerdict = EvaluationVerdict.ACCEPTED,
    confidence: float = 0.9,
    score: float = 90.0,
    findings: tuple[EvaluationFinding, ...] = (),
    judge_id: str = "primary",
) -> Assessment:
    response = JudgeResponse.model_validate(
        {
            "verdict": verdict.value,
            "confidence": confidence,
            "criteria": [
                {"criterion": criterion.value, "score": score, "rationale": "checked"}
                for criterion in Criterion
            ],
            "findings": [finding.model_dump(mode="json") for finding in findings],
            "reasoning_summary": "…",
        }
    )
    return Assessment(
        judge_id=judge_id,
        provider="fake",
        model="a-stronger-model",
        prompt_version="abc123",
        response=response,
        raw=json.dumps({"verdict": verdict.value}),
        tokens_in=10,
        tokens_out=5,
        cost_list_usd=Decimal("0.0001"),
        latency_ms=10,
    )


def finding(evidence_ref: str, description: str = "same problem") -> EvaluationFinding:
    return EvaluationFinding(
        severity=FindingSeverity.major,
        category=Criterion.correctness,
        description=description,
        evidence_ref=evidence_ref,
        impact="the bug stays",
        source=FindingSource.judge,
    )


def test_no_assessment_yields_no_verdict() -> None:
    result = aggregate(())

    assert result.verdict is None
    assert result.confidence == 0.0


def test_a_unanimous_acceptance_is_an_acceptance() -> None:
    result = aggregate((assessment(), assessment(judge_id="judge-1")))

    assert result.verdict is EvaluationVerdict.ACCEPTED
    assert result.disagreement is False


def test_one_rejection_rejects_even_against_an_acceptance() -> None:
    result = aggregate(
        (assessment(), assessment(verdict=EvaluationVerdict.REJECTED, judge_id="judge-1"))
    )

    assert result.verdict is EvaluationVerdict.REJECTED
    assert result.disagreement is True


def test_an_acceptance_beside_an_inconclusive_is_inconclusive() -> None:
    result = aggregate(
        (assessment(), assessment(verdict=EvaluationVerdict.INCONCLUSIVE, judge_id="judge-1"))
    )

    assert result.verdict is EvaluationVerdict.INCONCLUSIVE


def test_scores_are_averaged_over_the_judges_that_answered() -> None:
    result = aggregate((assessment(score=80.0), assessment(score=90.0, judge_id="judge-1")))

    assert result.judge_scores[Criterion.correctness] == 85.0
    assert len(result.judge_scores) == len(Criterion)


def test_confidence_is_the_mean_of_the_judges() -> None:
    result = aggregate((assessment(confidence=0.4), assessment(confidence=0.8, judge_id="judge-1")))

    assert result.confidence == 0.6


def test_the_same_finding_from_two_judges_is_kept_once() -> None:
    result = aggregate(
        (
            assessment(findings=(finding("diff:app.py:12"),)),
            assessment(judge_id="judge-1", findings=(finding("diff:app.py:12"),)),
        )
    )

    assert len(result.findings) == 1


def test_findings_about_different_lines_are_all_kept() -> None:
    result = aggregate(
        (
            assessment(findings=(finding("diff:app.py:12"),)),
            assessment(judge_id="judge-1", findings=(finding("diff:app.py:40"),)),
        )
    )

    assert len(result.findings) == 2
