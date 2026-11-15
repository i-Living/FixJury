from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest

from fixjury.evaluation.contracts import (
    CheckResult,
    CheckStatus,
    EvaluationConfiguration,
    EvaluationFinding,
    EvaluationRequest,
)
from fixjury.evaluation.scoring import (
    DEFAULT_WEIGHTS,
    ScoringError,
    ScoringProfile,
    assemble_result,
    critical_failures,
    decide_verdict,
    evidence_gaps,
    load_profile,
    score,
)
from fixjury.models import (
    Criterion,
    EvaluationVerdict,
    FindingSeverity,
    FindingSource,
)
from fixjury.tasks.schema import CheckTool

PROFILE_PATH = Path(__file__).resolve().parents[2] / "config" / "evaluation.yaml"
DIFF = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-broken\n+fixed\n"


def profile() -> ScoringProfile:
    return ScoringProfile(name="default", version="1", weights=dict(DEFAULT_WEIGHTS))


def check(
    name: str = "pytest tests",
    tool: CheckTool = CheckTool.pytest,
    status: CheckStatus = CheckStatus.PASSED,
    exit_code: int = 0,
) -> CheckResult:
    return CheckResult(name=name, tool=tool, status=status, exit_code=exit_code)


def heldout(status: CheckStatus = CheckStatus.PASSED) -> CheckResult:
    return check(name="held-out", status=status, exit_code=1 if status is CheckStatus.FAILED else 0)


def request(
    checks: tuple[CheckResult, ...] = (),
    *,
    policy_pass: bool = True,
    violations: tuple[str, ...] = (),
    confidence_threshold: float = 0.6,
) -> EvaluationRequest:
    return EvaluationRequest(
        subject_ref="T-001",
        run_id=uuid.uuid4(),
        base_commit="a" * 40,
        diff=DIFF,
        diff_sha256=hashlib.sha256(DIFF.encode("utf-8")).hexdigest(),
        spec_text="# Task T-001\n",
        checks=checks,
        policy_pass=policy_pass,
        policy_violations=violations,
        config=EvaluationConfiguration(confidence_threshold=confidence_threshold),
    )


def every_criterion(value: float) -> dict[Criterion, float]:
    return {criterion: value for criterion in Criterion}


def test_the_configured_profile_is_the_one_the_specification_fixes() -> None:
    configured = load_profile(PROFILE_PATH)

    assert configured.weights == DEFAULT_WEIGHTS
    assert configured.version == "1"


def test_a_missing_profile_is_an_error_not_a_fallback(tmp_path: Path) -> None:
    with pytest.raises(ScoringError, match="cannot read"):
        load_profile(tmp_path / "absent.yaml")


def test_an_unknown_profile_name_is_an_error() -> None:
    with pytest.raises(ScoringError, match="no profile named"):
        load_profile(PROFILE_PATH, name="invented")


def test_weights_that_do_not_add_up_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "evaluation.yaml"
    path.write_text(
        "profiles:\n  default:\n    version: '1'\n    weights:\n      correctness: 90\n",
        encoding="utf-8",
    )

    with pytest.raises(ScoringError, match="is invalid"):
        load_profile(path)


def test_the_overall_score_follows_the_weights() -> None:
    scores = every_criterion(100.0)
    scores[Criterion.security] = 0.0

    value = score(profile=profile(), checks=(check(), heldout()), judge_scores=scores)

    assert value.overall == 90.0
    assert value.profile_version == "1"


def test_scoring_is_deterministic_whatever_order_the_judge_scores_arrive_in() -> None:
    forward = {criterion: 80.0 for criterion in Criterion}
    backward = {criterion: 80.0 for criterion in reversed(list(Criterion))}

    first = score(profile=profile(), checks=(check(),), judge_scores=forward)
    second = score(profile=profile(), checks=(check(),), judge_scores=backward)

    assert first.model_dump() == second.model_dump()


def test_the_judge_may_lower_correctness_but_never_raise_it() -> None:
    lowered = score(
        profile=profile(),
        checks=(check(status=CheckStatus.FAILED, exit_code=1),),
        judge_scores={Criterion.correctness: 60.0},
    )
    raised = score(
        profile=profile(),
        checks=(check(status=CheckStatus.FAILED, exit_code=1),),
        judge_scores={Criterion.correctness: 100.0},
    )

    assert lowered.criteria[0].score == 0.0
    assert raised.criteria[0].score == 0.0


def test_a_failed_check_is_a_confirmed_critical_finding() -> None:
    failures = critical_failures(checks=(check(status=CheckStatus.FAILED, exit_code=1),))

    assert len(failures) == 1
    assert failures[0].confirmed is True
    assert failures[0].source is FindingSource.automated
    assert failures[0].evidence_ref == "check:pytest tests"
    assert failures[0].category is Criterion.correctness


def test_a_check_that_could_not_run_is_a_gap_not_a_failure() -> None:
    assert critical_failures(checks=(check(status=CheckStatus.ERROR, exit_code=-1),)) == ()
    assert evidence_gaps((check(status=CheckStatus.ERROR, exit_code=-1),)) == (
        "check 'pytest tests' could not execute",
    )


def test_a_failed_check_rejects_even_when_the_judge_accepts() -> None:
    result = assemble_result(
        request((check(status=CheckStatus.FAILED, exit_code=1), heldout())),
        profile=profile(),
        judge_verdict=EvaluationVerdict.ACCEPTED,
        judge_scores=every_criterion(95.0),
        confidence=0.95,
    )

    assert result.verdict is EvaluationVerdict.REJECTED
    assert result.judge_verdict is EvaluationVerdict.ACCEPTED
    assert any(finding.confirmed for finding in result.findings)


def test_a_check_that_could_not_run_leaves_the_result_inconclusive() -> None:
    result = assemble_result(
        request((check(status=CheckStatus.ERROR, exit_code=-1), heldout())),
        profile=profile(),
        judge_verdict=EvaluationVerdict.ACCEPTED,
        judge_scores=every_criterion(90.0),
        confidence=0.9,
    )

    assert result.verdict is EvaluationVerdict.INCONCLUSIVE
    assert any("could not execute" in limitation for limitation in result.limitations)


def test_without_a_judge_the_result_is_inconclusive_and_says_so() -> None:
    result = assemble_result(
        request((check(), heldout())),
        profile=profile(),
    )

    assert result.verdict is EvaluationVerdict.INCONCLUSIVE
    assert result.judge_verdict is None
    assert "no judge assessed this patch" in result.limitations
    # The automated criteria still carry their weight; the missing judge assessment is
    # what makes the result inconclusive, not a fabricated zero everywhere.
    assert result.score.overall == 50.0


def test_low_confidence_is_inconclusive_rather_than_a_verdict() -> None:
    verdict, reasons = decide_verdict(
        judge_verdict=EvaluationVerdict.ACCEPTED,
        score_value=score(
            profile=profile(),
            checks=(check(), heldout()),
            judge_scores=every_criterion(90.0),
        ),
        checks=(check(), heldout()),
        confidence=0.2,
        config=EvaluationConfiguration(confidence_threshold=0.6),
    )

    assert verdict is EvaluationVerdict.INCONCLUSIVE
    assert any("confidence" in reason for reason in reasons)


def test_a_policy_violation_is_a_confirmed_requirement_failure() -> None:
    failures = critical_failures(
        checks=(check(),),
        policy_pass=False,
        policy_violations=("EXTRA_FILES: touched benchmarks/heldout/T-001",),
    )

    assert len(failures) == 1
    assert failures[0].category is Criterion.requirement_compliance
    assert failures[0].confirmed is True


def test_a_clean_patch_the_judge_accepts_is_accepted() -> None:
    result = assemble_result(
        request((check(), check("ruff check src", CheckTool.ruff), heldout())),
        profile=profile(),
        judge_verdict=EvaluationVerdict.ACCEPTED,
        judge_scores=every_criterion(88.0),
        confidence=0.85,
    )

    assert result.verdict is EvaluationVerdict.ACCEPTED
    assert result.judge_verdict is EvaluationVerdict.ACCEPTED
    assert result.limitations == ()
    assert result.config_hash


def test_an_evidenced_judge_finding_is_kept_next_to_the_automated_ones() -> None:
    finding = EvaluationFinding(
        severity=FindingSeverity.major,
        category=Criterion.maintainability,
        description="the fix duplicates the parser instead of extending it",
        evidence_ref="diff:app.py:40",
        impact="the next change has to be made twice",
        source=FindingSource.judge,
    )

    result = assemble_result(
        request((check(), heldout())),
        profile=profile(),
        judge_verdict=EvaluationVerdict.REJECTED,
        judge_scores=every_criterion(60.0),
        findings=(finding,),
        confidence=0.8,
    )

    assert result.verdict is EvaluationVerdict.REJECTED
    assert result.findings == (finding,)
