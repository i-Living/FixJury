from __future__ import annotations

import hashlib
import uuid

import pytest
from pydantic import ValidationError

from fixjury.evaluation.contracts import (
    AssessmentSummary,
    CheckResult,
    CheckStatus,
    CriterionScore,
    EvaluationConfiguration,
    EvaluationContext,
    EvaluationFinding,
    EvaluationRequest,
    EvaluationResult,
    EvaluationScore,
    JudgeMode,
)
from fixjury.models import Criterion, EvaluationVerdict, FindingSeverity, FindingSource
from fixjury.tasks.schema import CheckTool

DIFF = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-broken\n+fixed\n"
DIFF_SHA = hashlib.sha256(DIFF.encode("utf-8")).hexdigest()
BASE_COMMIT = "a" * 40


def request(**overrides: object) -> EvaluationRequest:
    payload: dict[str, object] = {
        "subject_ref": "T-001",
        "run_id": uuid.uuid4(),
        "base_commit": BASE_COMMIT,
        "diff": DIFF,
        "diff_sha256": DIFF_SHA,
        "spec_text": "# Task T-001\n",
    }
    payload.update(overrides)
    return EvaluationRequest.model_validate(payload)


def passing_check(name: str = "pytest tests", tool: CheckTool = CheckTool.pytest) -> CheckResult:
    return CheckResult(name=name, tool=tool, status=CheckStatus.PASSED, exit_code=0)


def score(criteria: tuple[CriterionScore, ...], overall: float) -> EvaluationScore:
    return EvaluationScore(
        overall=overall, criteria=criteria, profile="default", profile_version="1"
    )


def flat_score(value: float = 100.0) -> EvaluationScore:
    """Every criterion at the same value, with the SPEC weights."""

    weights = {
        Criterion.correctness: 30.0,
        Criterion.regression_safety: 20.0,
        Criterion.code_quality: 15.0,
        Criterion.maintainability: 10.0,
        Criterion.security: 10.0,
        Criterion.minimality: 5.0,
        Criterion.requirement_compliance: 10.0,
    }
    criteria = tuple(
        CriterionScore(criterion=criterion, score=value, weight=weight)
        for criterion, weight in weights.items()
    )
    return score(criteria, value)


def test_a_check_that_passed_cannot_carry_a_failing_exit_code() -> None:
    with pytest.raises(ValidationError, match="PASSED with exit code"):
        CheckResult(
            name="pytest tests", tool=CheckTool.pytest, status=CheckStatus.PASSED, exit_code=1
        )


def test_a_check_that_failed_cannot_carry_a_zero_exit_code() -> None:
    with pytest.raises(ValidationError, match="FAILED with exit code 0"):
        CheckResult(
            name="pytest tests", tool=CheckTool.pytest, status=CheckStatus.FAILED, exit_code=0
        )


def test_a_judge_finding_is_never_confirmed() -> None:
    with pytest.raises(ValidationError, match="never confirmed"):
        EvaluationFinding(
            severity=FindingSeverity.critical,
            category=Criterion.security,
            description="the patch interpolates user input into SQL",
            evidence_ref="diff:app.py:12",
            impact="an attacker can read any row",
            source=FindingSource.judge,
            confirmed=True,
        )


def test_a_finding_without_evidence_is_refused() -> None:
    with pytest.raises(ValidationError):
        EvaluationFinding(
            severity=FindingSeverity.major,
            category=Criterion.correctness,
            description="the fix looks wrong",
            evidence_ref="",
            impact="the bug stays",
            source=FindingSource.judge,
        )


def test_a_request_whose_digest_does_not_match_its_diff_is_refused() -> None:
    with pytest.raises(ValidationError, match="diff_sha256 does not match"):
        request(diff_sha256="b" * 64)


def test_a_request_needs_a_full_base_commit() -> None:
    with pytest.raises(ValidationError, match="40-character"):
        request(base_commit="abc123")


def test_the_configuration_hash_is_stable_and_covers_the_budgets() -> None:
    first = EvaluationConfiguration()
    second = EvaluationConfiguration()
    other = EvaluationConfiguration(token_budget=1000)

    assert first.config_hash() == second.config_hash()
    assert first.config_hash() != other.config_hash()
    assert first.mode is JudgeMode.adaptive


def test_weights_must_add_up_to_one_hundred() -> None:
    criteria = tuple(
        CriterionScore(criterion=criterion, score=100.0, weight=1.0) for criterion in Criterion
    )

    with pytest.raises(ValidationError, match="add up to 100"):
        EvaluationScore(overall=100.0, criteria=criteria, profile="default", profile_version="1")


def test_the_overall_score_must_match_the_criteria_it_claims() -> None:
    with pytest.raises(ValidationError, match="does not match the weighted criteria"):
        score(flat_score(80.0).criteria, 95.0)


def test_an_accepted_result_cannot_carry_a_confirmed_critical_finding() -> None:
    finding = EvaluationFinding(
        severity=FindingSeverity.critical,
        category=Criterion.correctness,
        description="pytest tests exited 1",
        evidence_ref="check:pytest tests",
        impact="the patch does not pass the task's tests",
        source=FindingSource.automated,
        confirmed=True,
    )

    with pytest.raises(ValidationError, match="confirmed critical findings"):
        EvaluationResult(
            verdict=EvaluationVerdict.ACCEPTED,
            score=flat_score(100.0),
            confidence=0.9,
            findings=(finding,),
            config_hash="c" * 64,
        )


def test_an_inconclusive_result_needs_no_evidence_to_exist() -> None:
    result = EvaluationResult(
        verdict=EvaluationVerdict.INCONCLUSIVE,
        judge_verdict=None,
        score=flat_score(0.0),
        confidence=0.0,
        config_hash="c" * 64,
    )

    assert result.verdict is EvaluationVerdict.INCONCLUSIVE


def test_the_judge_context_carries_no_agent_identity() -> None:
    context = EvaluationContext.from_request(request(checks=(passing_check(),)))

    assert not {"agent", "agent_version", "model", "run_id"} & set(EvaluationContext.model_fields)
    assert context.checks[0].status is CheckStatus.PASSED


def test_the_judge_context_reduces_the_held_out_suite_to_its_outcome() -> None:
    heldout = CheckResult(
        name="held-out",
        tool=CheckTool.pytest,
        status=CheckStatus.FAILED,
        exit_code=1,
        summary="assert user_cannot_see_other_tenants() failed",
    )
    context = EvaluationContext.from_request(request(checks=(passing_check(), heldout)))

    assert context.heldout_passed is False
    assert all(check.name != "held-out" for check in context.checks)
    assert "user_cannot_see_other_tenants" not in context.model_dump_json()


def test_an_assessment_summary_keeps_the_prompt_version() -> None:
    summary = AssessmentSummary(
        judge_id="primary",
        provider="opencode-go",
        model="a-stronger-model",
        prompt_version="j1",
        verdict=EvaluationVerdict.ACCEPTED,
        overall=82.0,
        confidence=0.8,
    )

    assert summary.prompt_version == "j1"
