"""The scoring engine and the acceptance rules (PLAN_V1.1 §1.3).

Pure functions: checks and judge assessments go in, a score and a verdict come out. The
same inputs and the same profile always produce the same number — criteria are walked in
their enum order, never in dictionary order.

Two rules carry the weight of the release:

- A confirmed automated failure can only deny acceptance. The judge can lower a score, and
  its own rejection stands, but it can never turn a failed check into an accepted patch.
- Missing evidence produces ``INCONCLUSIVE``, never acceptance. A check that could not run
  and a criterion nobody assessed are both gaps, and a gap is not a pass.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from fixjury.evaluation.contracts import (
    HELDOUT_NAME,
    AssessmentSummary,
    CheckResult,
    CheckStatus,
    CriterionScore,
    EvaluationConfiguration,
    EvaluationFinding,
    EvaluationRequest,
    EvaluationResult,
    EvaluationScore,
)
from fixjury.models import Criterion, EvaluationVerdict, FindingSeverity, FindingSource
from fixjury.tasks.schema import CheckTool

#: SPEC §5 default profile. A configured profile overrides it; the numbers are the same.
DEFAULT_WEIGHTS: dict[Criterion, float] = {
    Criterion.correctness: 30.0,
    Criterion.regression_safety: 20.0,
    Criterion.code_quality: 15.0,
    Criterion.maintainability: 10.0,
    Criterion.security: 10.0,
    Criterion.minimality: 5.0,
    Criterion.requirement_compliance: 10.0,
}

#: Criteria an automated check answers; the judge may only lower them.
AUTOMATED_CRITERIA: frozenset[Criterion] = frozenset(
    {Criterion.correctness, Criterion.regression_safety}
)

CHECK_CRITERIA: dict[CheckTool, Criterion] = {
    CheckTool.pytest: Criterion.correctness,
    CheckTool.coverage: Criterion.correctness,
    CheckTool.ruff: Criterion.code_quality,
    CheckTool.mypy: Criterion.code_quality,
}


class ScoringError(RuntimeError):
    """The profile cannot be used: missing, malformed or not a percentage."""


class ScoringProfile(BaseModel):
    """A named, versioned set of weights. Changing a weight is changing the version."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=32)
    weights: dict[Criterion, float]

    @model_validator(mode="after")
    def _weights_are_a_percentage(self) -> ScoringProfile:
        missing = sorted(criterion.value for criterion in set(Criterion) - set(self.weights))
        if missing:
            raise ValueError(f"profile {self.name!r} has no weight for: {', '.join(missing)}")
        negative = sorted(item.value for item, weight in self.weights.items() if weight < 0)
        if negative:
            raise ValueError(
                f"profile {self.name!r} has negative weights for: {', '.join(negative)}"
            )
        total = sum(self.weights.values())
        if abs(total - 100.0) > 1e-6:
            raise ValueError(f"profile {self.name!r} weights add up to {total}, not 100")
        return self


def load_profile(path: Path, *, name: str = "default") -> ScoringProfile:
    """Read one profile from the evaluation configuration file.

    A missing file is an error, not a fallback: silently scoring with the built-in
    weights would make a result incomparable with the configuration it claims.
    """

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ScoringError(f"{path}: cannot read the evaluation profile: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ScoringError(f"{path}: invalid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ScoringError(f"{path}: the file must contain a mapping")
    profiles = raw.get("profiles")
    if not isinstance(profiles, dict):
        raise ScoringError(f"{path}: no 'profiles' mapping")
    entry = profiles.get(name)
    if not isinstance(entry, dict):
        raise ScoringError(f"{path}: no profile named {name!r}")
    weights = entry.get("weights")
    if not isinstance(weights, dict):
        raise ScoringError(f"{path}: profile {name!r} has no 'weights' mapping")
    try:
        return ScoringProfile(
            name=name,
            version=str(entry.get("version", "1")),
            weights={Criterion(key): float(value) for key, value in weights.items()},
        )
    except (ValueError, TypeError) as exc:
        raise ScoringError(f"{path}: profile {name!r} is invalid: {exc}") from exc


def _relevant(checks: Sequence[CheckResult], criterion: Criterion) -> tuple[CheckResult, ...]:
    if criterion is Criterion.correctness:
        return tuple(
            check
            for check in checks
            if check.tool in (CheckTool.pytest, CheckTool.coverage) and check.name != HELDOUT_NAME
        )
    if criterion is Criterion.regression_safety:
        return tuple(check for check in checks if check.name == HELDOUT_NAME)
    return ()


def _automated_score(
    checks: Sequence[CheckResult], criterion: Criterion
) -> tuple[float, str] | None:
    """The score a check set proves, or ``None`` when it proves nothing.

    ``ERROR`` yields no score on purpose: the check could not run, so the patch is not
    worse, it is unmeasured. ``SKIPPED`` is ignored for the same reason — a check nobody
    ran is not a check that passed.
    """

    evidence = _relevant(checks, criterion)
    if not evidence:
        return None
    if any(check.status is CheckStatus.ERROR for check in evidence):
        return None
    decisive = [check for check in evidence if check.status is not CheckStatus.SKIPPED]
    if not decisive:
        return None
    if any(check.status is CheckStatus.FAILED for check in decisive):
        return 0.0, "a required check failed"
    return 100.0, "every required check passed"


def criterion_scores(
    *,
    profile: ScoringProfile,
    checks: Sequence[CheckResult],
    judge_scores: Mapping[Criterion, float],
) -> tuple[CriterionScore, ...]:
    """Score every criterion of the profile, in enum order, judge input taken as given."""

    scores: list[CriterionScore] = []
    for criterion in Criterion:
        weight = profile.weights[criterion]
        if criterion in AUTOMATED_CRITERIA:
            automated = _automated_score(checks, criterion)
            if automated is None:
                score, rationale = 0.0, "no automated evidence"
            else:
                score, rationale = automated
                judged = judge_scores.get(criterion)
                if judged is not None and judged < score:
                    score, rationale = judged, "lowered by an evidenced judge finding"
        else:
            judged = judge_scores.get(criterion)
            if judged is None:
                score, rationale = 0.0, "no judge assessment"
            else:
                score, rationale = judged, "judge assessment"
        scores.append(
            CriterionScore(
                criterion=criterion,
                score=round(score, 2),
                weight=weight,
                rationale=rationale,
                evidence_refs=tuple(
                    f"check:{check.name}" for check in _relevant(checks, criterion)
                ),
            )
        )
    return tuple(scores)


def score(
    *,
    profile: ScoringProfile,
    checks: Sequence[CheckResult],
    judge_scores: Mapping[Criterion, float],
) -> EvaluationScore:
    """The weighted score. Deterministic: the criteria are always walked in enum order."""

    criteria = criterion_scores(profile=profile, checks=checks, judge_scores=judge_scores)
    overall = round(sum(item.score * item.weight for item in criteria) / 100.0, 2)
    return EvaluationScore(
        overall=overall,
        criteria=criteria,
        profile=profile.name,
        profile_version=profile.version,
    )


def _check_finding(check: CheckResult) -> EvaluationFinding:
    # A failed held-out suite is a regression, not a wrong fix: the task's own tests said
    # the patch works, and the post-fix tests say it broke something else.
    criterion = (
        Criterion.regression_safety if check.name == HELDOUT_NAME else CHECK_CRITERIA[check.tool]
    )
    return EvaluationFinding(
        severity=FindingSeverity.critical,
        category=criterion,
        description=f"{check.name} exited {check.exit_code}",
        evidence_ref=f"check:{check.name}",
        impact="the patch does not pass a check the task requires",
        source=FindingSource.automated,
        confirmed=True,
    )


def critical_failures(
    *,
    checks: Sequence[CheckResult],
    findings: Sequence[EvaluationFinding] = (),
    policy_pass: bool = True,
    policy_violations: Sequence[str] = (),
) -> tuple[EvaluationFinding, ...]:
    """Every confirmed critical failure: failed checks, policy violations, proven findings.

    ``ERROR`` checks are deliberately absent: an environment that could not run a check
    proves nothing about the patch.
    """

    failures = [_check_finding(check) for check in checks if check.status is CheckStatus.FAILED]
    if not policy_pass:
        detail = "; ".join(policy_violations) or "the diff leaves the paths the task allows"
        failures.append(
            EvaluationFinding(
                severity=FindingSeverity.critical,
                category=Criterion.requirement_compliance,
                description=f"the diff violates the task's path policy: {detail}",
                evidence_ref="policy:paths",
                impact="the patch changes files the task does not allow",
                source=FindingSource.automated,
                confirmed=True,
            )
        )
    failures.extend(
        finding
        for finding in findings
        if finding.confirmed and finding.severity is FindingSeverity.critical
    )
    return tuple(failures)


def evidence_gaps(checks: Sequence[CheckResult]) -> tuple[str, ...]:
    """Reasons the evidence is not enough to decide anything."""

    gaps = [
        f"check {check.name!r} could not execute"
        for check in checks
        if check.status is CheckStatus.ERROR
    ]
    if not checks:
        gaps.append("no automated check ran")
    return tuple(gaps)


def decide_verdict(
    *,
    judge_verdict: EvaluationVerdict | None,
    score_value: EvaluationScore,
    checks: Sequence[CheckResult],
    findings: Sequence[EvaluationFinding] = (),
    policy_pass: bool = True,
    policy_violations: Sequence[str] = (),
    confidence: float,
    config: EvaluationConfiguration,
) -> tuple[EvaluationVerdict, tuple[str, ...]]:
    """The final verdict and the reasons it is not a clean acceptance.

    Order matters: a confirmed failure comes first, so no missing assessment can hide one.
    """

    failures = critical_failures(
        checks=checks,
        findings=findings,
        policy_pass=policy_pass,
        policy_violations=policy_violations,
    )
    if failures:
        return EvaluationVerdict.REJECTED, tuple(failure.description for failure in failures)

    reasons: list[str] = []
    if judge_verdict is None:
        reasons.append("no judge assessed this patch")
    reasons.extend(evidence_gaps(checks))
    unassessed = [
        item.criterion.value
        for item in score_value.criteria
        if item.rationale in ("no automated evidence", "no judge assessment")
    ]
    if unassessed:
        reasons.append(f"no evidence for: {', '.join(unassessed)}")
    if confidence < config.confidence_threshold:
        reasons.append(
            f"confidence {confidence:.2f} is below the configured {config.confidence_threshold:.2f}"
        )
    if reasons:
        return EvaluationVerdict.INCONCLUSIVE, tuple(reasons)
    # Unreachable in practice: a missing verdict already added a reason above. Stated
    # explicitly so the type checker and a future reader both see the branch.
    if judge_verdict is None:
        return EvaluationVerdict.INCONCLUSIVE, ("no judge assessed this patch",)
    return judge_verdict, ()


def assemble_result(
    request: EvaluationRequest,
    *,
    profile: ScoringProfile,
    judge_verdict: EvaluationVerdict | None = None,
    judge_scores: Mapping[Criterion, float] | None = None,
    findings: Sequence[EvaluationFinding] = (),
    assessments: Sequence[AssessmentSummary] = (),
    confidence: float = 0.0,
    limitations: Sequence[str] = (),
    escalated: bool = False,
    reasoning_summary: str = "",
) -> EvaluationResult:
    """Build the stored result from the checks and whatever the judge said.

    Without a judge this returns ``INCONCLUSIVE``: the automated evidence is recorded, and
    the evaluation says out loud that no assessment exists instead of inventing one.
    """

    checks = request.checks
    judge_scores = judge_scores or {}
    if profile.name != request.config.profile:
        # The stored result names the profile it was scored with and the hash of the
        # configuration it ran under. If those two disagree, one of them is a lie.
        raise ScoringError(
            f"profile {profile.name!r} does not match the configured profile "
            f"{request.config.profile!r}"
        )
    score_value = score(profile=profile, checks=checks, judge_scores=judge_scores)
    verdict, reasons = decide_verdict(
        judge_verdict=judge_verdict,
        score_value=score_value,
        checks=checks,
        findings=findings,
        policy_pass=request.policy_pass,
        policy_violations=request.policy_violations,
        confidence=confidence,
        config=request.config,
    )
    failures = critical_failures(
        checks=checks,
        findings=findings,
        policy_pass=request.policy_pass,
        policy_violations=request.policy_violations,
    )
    seen = {(finding.evidence_ref, finding.source) for finding in findings}
    merged = list(findings)
    # Dedupe only against an automated finding that is already there: a judge finding can
    # cite the same check, and letting it stand in for the confirmed one would hide the
    # failure the check proved.
    merged.extend(
        failure for failure in failures if (failure.evidence_ref, failure.source) not in seen
    )
    return EvaluationResult(
        verdict=verdict,
        judge_verdict=judge_verdict,
        score=score_value,
        confidence=confidence,
        findings=tuple(merged),
        assessments=tuple(assessments),
        limitations=tuple(limitations) + reasons,
        escalated=escalated,
        reasoning_summary=reasoning_summary,
        config_hash=request.config.config_hash(),
    )
