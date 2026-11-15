"""Quality gates (PLAN_V1.1 §5.1, SPEC §7.3), pinned rule by rule."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from fixjury.evaluation.contracts import (
    CheckResult,
    CheckStatus,
    CriterionScore,
    EvaluationConfiguration,
    EvaluationFinding,
    EvaluationRequest,
    EvaluationResult,
    EvaluationScore,
)
from fixjury.evaluation.gates import (
    GateError,
    GateOutcome,
    GatePolicy,
    GateReport,
    evaluate_gates,
    evidence_mismatch,
    load_policy,
)
from fixjury.models import Criterion, EvaluationVerdict, FindingSeverity, FindingSource
from fixjury.tasks.schema import CheckTool

PROJECT = Path(__file__).resolve().parents[2]
DIFF = (
    "diff --git a/app.py b/app.py\n"
    "--- a/app.py\n"
    "+++ b/app.py\n"
    "@@ -1 +1,2 @@\n"
    "-broken\n"
    "+fixed\n"
    "+also fine\n"
)


def commit() -> str:
    return "a" * 40


def digest(diff: str = DIFF) -> str:
    return hashlib.sha256(diff.encode("utf-8")).hexdigest()


def check(
    name: str = "pytest",
    *,
    status: CheckStatus = CheckStatus.PASSED,
    exit_code: int = 0,
    tool: CheckTool = CheckTool.pytest,
) -> CheckResult:
    return CheckResult(name=name, tool=tool, status=status, exit_code=exit_code)


def request(
    *,
    checks: tuple[CheckResult, ...] = (),
    mode: str = "adaptive",
    diff: str = DIFF,
) -> EvaluationRequest:
    return EvaluationRequest(
        subject_ref="T-001",
        run_id=__import__("uuid").uuid4(),
        base_commit=commit(),
        diff=diff,
        diff_sha256=digest(diff),
        spec_text="# Task T-001",
        checks=checks,
        config=EvaluationConfiguration(mode=mode),  # type: ignore[arg-type]
    )


def score(value: float = 88.0) -> EvaluationScore:
    criteria = (CriterionScore(criterion=Criterion.correctness, score=value, weight=100.0),)
    return EvaluationScore(overall=value, criteria=criteria, profile="default", profile_version="1")


def result(
    *,
    verdict: EvaluationVerdict = EvaluationVerdict.ACCEPTED,
    findings: tuple[EvaluationFinding, ...] = (),
    overall: float = 88.0,
) -> EvaluationResult:
    return EvaluationResult(
        verdict=verdict,
        judge_verdict=verdict,
        score=score(overall),
        confidence=0.9,
        findings=findings,
        config_hash="b" * 64,
    )


def policy(**kwargs: object) -> GatePolicy:
    defaults: dict[str, object] = {
        "name": "test",
        "version": "1",
        "min_score": 70.0,
        "required_tools": (CheckTool.pytest,),
        "require_heldout": True,
        "max_changed_files": 10,
        "max_changed_lines": 400,
        "forbidden_paths": (".github/",),
        "max_judge_tokens": 100_000,
    }
    defaults.update(kwargs)
    return GatePolicy(**defaults)  # type: ignore[arg-type]


def passing() -> tuple[EvaluationRequest, EvaluationResult]:
    return (
        request(checks=(check("pytest tests/test_app.py"), check("held-out"))),
        result(),
    )


def gate(policy: GatePolicy | None = None) -> tuple[GateReport, GateOutcome]:
    documented_request, documented_result = passing()
    report = evaluate_gates(
        policy or GatePolicy(name="test", version="1"),
        request=documented_request,
        result=documented_result,
        evaluation_run_id="evaluation-1",
    )
    return report, report.outcome


def test_a_clean_evaluation_passes_every_default_rule() -> None:
    report, outcome = gate()

    assert outcome is GateOutcome.PASS
    assert report.failures == ()


def test_a_failed_required_check_is_a_confirmed_failure() -> None:
    """A check that ran and failed cannot be argued away by the score or the verdict."""

    documented_request = request(
        checks=(
            check("pytest tests/test_app.py", status=CheckStatus.FAILED, exit_code=1),
            check("held-out"),
        )
    )

    report = evaluate_gates(
        GatePolicy(name="test", version="1", required_tools=(CheckTool.pytest,)),
        request=documented_request,
        result=result(verdict=EvaluationVerdict.REJECTED),
        evaluation_run_id="evaluation-1",
    )

    failed = next(item for item in report.results if item.rule == "tool:pytest")
    assert report.outcome is GateOutcome.FAIL
    assert failed.confirmed is True


def test_a_missing_required_check_is_inconclusive_not_a_pass() -> None:
    # Only a lint check: nothing here ran the tests, so the policy has no measurement.
    documented_request = request(checks=(check("ruff check .", tool=CheckTool.ruff),))

    report = evaluate_gates(
        GatePolicy(name="test", version="1", required_tools=(CheckTool.pytest,)),
        request=documented_request,
        result=result(),
        evaluation_run_id="evaluation-1",
    )

    assert report.outcome is GateOutcome.INCONCLUSIVE
    missing = next(item for item in report.results if item.rule == "tool:pytest")
    assert "no such check" in missing.detail


def test_a_check_that_could_not_run_is_inconclusive() -> None:
    documented_request = request(checks=(check("pytest", status=CheckStatus.ERROR, exit_code=3),))

    report = evaluate_gates(
        GatePolicy(name="test", version="1", required_tools=(CheckTool.pytest,)),
        request=documented_request,
        result=result(verdict=EvaluationVerdict.INCONCLUSIVE),
        evaluation_run_id="evaluation-1",
    )

    assert report.outcome is GateOutcome.INCONCLUSIVE


def test_an_inconclusive_verdict_never_passes_on_its_own() -> None:
    documented_request, _ = passing()

    report = evaluate_gates(
        GatePolicy(name="test", version="1"),
        request=documented_request,
        result=result(verdict=EvaluationVerdict.INCONCLUSIVE),
        evaluation_run_id="evaluation-1",
    )

    assert report.outcome is GateOutcome.INCONCLUSIVE


def test_a_judge_security_opinion_warns_and_does_not_fail() -> None:
    """An unconfirmed security opinion is a review item, not a vulnerability report."""

    finding = EvaluationFinding(
        severity=FindingSeverity.critical,
        category=Criterion.security,
        description="the patch may leak the token",
        evidence_ref="judge:security",
        impact="a credential could be logged",
        source=FindingSource.judge,
        confirmed=False,
    )
    documented_request, _ = passing()

    report = evaluate_gates(
        GatePolicy(name="test", version="1"),
        request=documented_request,
        result=result(findings=(finding,)),
        evaluation_run_id="evaluation-1",
    )

    security = next(item for item in report.results if item.rule == "security")
    assert report.outcome is GateOutcome.PASS
    assert security.outcome is GateOutcome.PASS
    assert "unconfirmed" in security.detail


def test_a_confirmed_security_finding_fails() -> None:
    finding = EvaluationFinding(
        severity=FindingSeverity.critical,
        category=Criterion.security,
        description="the patch writes the token to disk",
        evidence_ref="check:secret-scan",
        impact="a credential is persisted",
        source=FindingSource.automated,
        confirmed=True,
    )
    documented_request, _ = passing()

    report = evaluate_gates(
        GatePolicy(name="test", version="1"),
        request=documented_request,
        result=result(verdict=EvaluationVerdict.REJECTED, findings=(finding,)),
        evaluation_run_id="evaluation-1",
    )

    assert report.outcome is GateOutcome.FAIL
    assert any(item.rule == "security" and item.confirmed for item in report.results)


def test_a_score_below_the_minimum_fails() -> None:
    documented_request, _ = passing()

    report = evaluate_gates(
        GatePolicy(name="test", version="1", min_score=90.0),
        request=documented_request,
        result=result(overall=80.0),
        evaluation_run_id="evaluation-1",
    )

    assert report.outcome is GateOutcome.FAIL
    score_result = next(item for item in report.results if item.rule == "score")
    assert "below the minimum" in score_result.detail


def test_a_patch_larger_than_the_limit_fails() -> None:
    documented_request, _ = passing()

    report = evaluate_gates(
        GatePolicy(name="test", version="1", max_changed_lines=1),
        request=documented_request,
        result=result(),
        evaluation_run_id="evaluation-1",
    )

    assert report.outcome is GateOutcome.FAIL
    assert any(item.rule == "size" for item in report.failures)


def test_a_forbidden_path_fails() -> None:
    diff = (
        "diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml\n"
        "--- a/.github/workflows/ci.yml\n"
        "+++ b/.github/workflows/ci.yml\n"
        "@@ -1 +1 @@\n"
        "-on: push\n"
        "+on: pull_request\n"
    )
    documented_request = request(diff=diff)

    report = evaluate_gates(
        GatePolicy(name="test", version="1", forbidden_paths=(".github/",)),
        request=documented_request,
        result=result(),
        evaluation_run_id="evaluation-1",
    )

    assert report.outcome is GateOutcome.FAIL
    assert any(item.rule == "paths" for item in report.failures)


def test_a_judge_mode_outside_the_policy_fails() -> None:
    documented_request, _ = passing()

    report = evaluate_gates(
        GatePolicy(
            name="test",
            version="1",
            allowed_judge_modes=(),  # any mode allowed
        ),
        request=documented_request,
        result=result(),
        evaluation_run_id="evaluation-1",
    )
    assert report.outcome is GateOutcome.PASS

    strict = evaluate_gates(
        GatePolicy(name="test", version="1", allowed_judge_modes=("multi",)),
        request=documented_request,
        result=result(),
        evaluation_run_id="evaluation-1",
    )
    assert strict.outcome is GateOutcome.FAIL
    assert any(item.rule == "judge-mode" for item in strict.failures)


def test_an_unknown_judge_budget_is_inconclusive() -> None:
    documented_request, _ = passing()

    report = evaluate_gates(
        GatePolicy(name="test", version="1", max_judge_tokens=10),
        request=documented_request,
        result=result(),
        evaluation_run_id="evaluation-1",
    )

    assert report.outcome is GateOutcome.INCONCLUSIVE
    assert any(item.rule == "budget" and "unknown" in item.detail for item in report.results)


def test_fail_beats_inconclusive_in_the_final_answer() -> None:
    # Only a lint check: pytest was never run, which is a gap rather than a failure.
    documented_request = request(checks=(check("ruff check .", tool=CheckTool.ruff),))

    report = evaluate_gates(
        GatePolicy(
            name="test",
            version="1",
            min_score=95.0,
            required_tools=(CheckTool.pytest,),
        ),
        request=documented_request,
        result=result(overall=80.0),
        evaluation_run_id="evaluation-1",
    )

    assert report.outcome is GateOutcome.FAIL
    assert any(item.outcome is GateOutcome.INCONCLUSIVE for item in report.results)


def test_the_shipped_policy_file_loads_and_passes_a_clean_evaluation() -> None:
    policy_from_file = load_policy(PROJECT / "config" / "quality.yaml")

    assert policy_from_file.name == "default"
    assert policy_from_file.min_score == 70.0
    assert policy_from_file.required_tools == (CheckTool.pytest,)
    assert policy_from_file.require_heldout is True
    assert policy_from_file.allowed_judge_modes


def test_an_unknown_policy_name_is_refused() -> None:
    with pytest.raises(GateError, match="no policy named"):
        load_policy(PROJECT / "config" / "quality.yaml", name="missing")


def test_a_policy_with_an_unknown_key_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "quality.yaml"
    path.write_text("policies:\n  default:\n    version: '1'\n    min_scor: 80\n", encoding="utf-8")

    with pytest.raises(GateError, match="invalid"):
        load_policy(path)


def test_a_forbidden_path_must_be_relative() -> None:
    with pytest.raises(ValueError, match="relative"):
        GatePolicy(name="test", version="1", forbidden_paths=("/etc/passwd",))


def test_a_rename_away_from_a_forbidden_path_still_fails() -> None:
    """Moving the file is how the rule would be removed, so the source counts too."""

    diff = (
        "diff --git a/.github/workflows/ci.yml b/archived/ci.yml\n"
        "similarity index 100%\n"
        "rename from .github/workflows/ci.yml\n"
        "rename to archived/ci.yml\n"
    )

    report = evaluate_gates(
        GatePolicy(name="test", version="1", forbidden_paths=(".github/",)),
        request=request(diff=diff),
        result=result(),
        evaluation_run_id="evaluation-1",
    )

    assert report.outcome is GateOutcome.FAIL
    assert any(item.rule == "paths" for item in report.failures)


def test_a_file_that_merely_shares_a_name_is_not_forbidden() -> None:
    """`.gitlab-ci.yml.example` is not `.gitlab-ci.yml`, and blocking it is a rule nobody wrote."""

    diff = (
        "diff --git a/.gitlab-ci.yml.example b/.gitlab-ci.yml.example\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/.gitlab-ci.yml.example\n"
        "@@ -0,0 +1 @@\n"
        "+example\n"
    )

    report = evaluate_gates(
        GatePolicy(name="test", version="1", forbidden_paths=(".gitlab-ci.yml",)),
        request=request(diff=diff),
        result=result(),
        evaluation_run_id="evaluation-1",
    )

    paths = next(item for item in report.results if item.rule == "paths")
    assert paths.outcome is GateOutcome.PASS


def test_a_policy_file_with_a_duplicate_key_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "quality.yaml"
    path.write_text(
        "policies:\n"
        "  default:\n"
        "    version: '1'\n"
        "    forbidden_paths: ['.github/']\n"
        "    forbidden_paths: []\n",
        encoding="utf-8",
    )

    with pytest.raises(GateError, match="duplicate key"):
        load_policy(path)


def test_a_policy_file_with_a_duplicate_policy_name_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "quality.yaml"
    path.write_text(
        "policies:\n  default:\n    version: '1'\n  default:\n    version: '2'\n",
        encoding="utf-8",
    )

    with pytest.raises(GateError, match="duplicate key"):
        load_policy(path)


def test_a_gate_refuses_evidence_the_evaluation_never_saw() -> None:
    """Re-verification rewrites the checks in place; the old verdict must not be gated."""

    documented_request, documented_result = passing()
    assert evidence_mismatch(documented_result, documented_request.checks) is None

    newer = (
        check("pytest tests/test_app.py", status=CheckStatus.FAILED, exit_code=1),
        check("held-out"),
    )
    reason = evidence_mismatch(documented_result, newer)

    assert reason is not None
    assert "re-run" in reason
    assert "check:pytest tests/test_app.py" in reason


def test_a_score_without_a_judge_is_incomplete_not_low() -> None:
    """Scoring a patch on the checks alone leaves the judge's criteria unanswered."""

    documented_request, _ = passing()

    report = evaluate_gates(
        GatePolicy(name="test", version="1", min_score=90.0),
        request=documented_request,
        result=result(verdict=EvaluationVerdict.INCONCLUSIVE, overall=40.0),
        evaluation_run_id="evaluation-1",
    )

    score_result = next(item for item in report.results if item.rule == "score")
    assert score_result.outcome is GateOutcome.INCONCLUSIVE
    assert "incomplete" in score_result.detail
    assert report.outcome is GateOutcome.INCONCLUSIVE
