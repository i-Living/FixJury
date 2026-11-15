"""Quality gates over a stored evaluation (PLAN_V1.1 §5.1, SPEC §7.3).

A gate is a rule about an evaluation the platform already made: it reads the result, the
request and the policy, and answers PASS, FAIL or INCONCLUSIVE. It never judges anything
itself — the verdict it reasons about was produced by the scoring engine and the AI Judge,
and re-deciding it here would give the platform two verdicts that can disagree.

Three rules carry the weight, and the order between them matters:

- a **confirmed** automated failure is a FAIL. A failed check the task requires cannot be
  talked out of by a score, a judge or a policy.
- a judge's finding is an opinion, never a confirmed failure. A policy that fails a patch on
  an unconfirmed security opinion turns a suggestion into a vulnerability report.
- missing evidence is INCONCLUSIVE, and INCONCLUSIVE is never a pass. A policy that cannot
  be evaluated is a policy that has not been satisfied.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from fixjury.evaluation.contracts import (
    HELDOUT_NAME,
    CheckResult,
    CheckStatus,
    EvaluationRequest,
    EvaluationResult,
    JudgeMode,
    digest,
)
from fixjury.models import EvaluationVerdict, FindingSeverity, FindingSource
from fixjury.tasks.schema import CheckTool
from fixjury.verify.policy import ChangedFile, parse_changed_files


class GateError(RuntimeError):
    """The policy cannot be loaded or the evaluation cannot be gated."""


class GateOutcome(StrEnum):
    """SPEC §7.3. ``INCONCLUSIVE`` is its own answer, never a softened ``PASS``."""

    PASS = "PASS"  # noqa: S105 - a gate outcome, not a credential
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


#: Strongest last: an overall outcome is the worst outcome any single gate produced.
_SEVERITY: dict[GateOutcome, int] = {
    GateOutcome.PASS: 0,
    GateOutcome.INCONCLUSIVE: 1,
    GateOutcome.FAIL: 2,
}

CONFIRMED_SEVERITIES: tuple[FindingSeverity, ...] = (
    FindingSeverity.critical,
    FindingSeverity.major,
)

#: Ordering for the policy's security threshold: a finding at or above it is reported.
_SEVERITY_ORDER: dict[FindingSeverity, int] = {
    FindingSeverity.info: 0,
    FindingSeverity.minor: 1,
    FindingSeverity.major: 2,
    FindingSeverity.critical: 3,
}


class GatePolicy(BaseModel):
    """One named, versioned set of rules a patch has to satisfy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=32)
    min_score: float = Field(default=0.0, ge=0.0, le=100.0)
    #: Tools a patch has to have been measured by. A tool, not a command name: the corpus
    #: writes its own commands (`pytest tests/test_x.py`), so a policy keyed on those would
    #: silently stop matching when a task changed its paths.
    required_tools: tuple[CheckTool, ...] = (CheckTool.pytest,)
    #: Whether the held-out suite has to exist and pass.
    require_heldout: bool = True
    security_severity: FindingSeverity = FindingSeverity.major
    max_changed_files: int | None = Field(default=None, ge=1)
    max_changed_lines: int | None = Field(default=None, ge=1)
    forbidden_paths: tuple[str, ...] = ()
    allowed_judge_modes: tuple[JudgeMode, ...] = ()
    max_judge_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _paths_are_relative(self) -> GatePolicy:
        for path in self.forbidden_paths:
            if not path or path.startswith("/") or ".." in path.split("/"):
                raise ValueError(f"forbidden_paths must be relative repository paths: {path!r}")
        return self


@dataclass(frozen=True)
class GateResult:
    """One rule's answer, with the evidence that produced it."""

    rule: str
    outcome: GateOutcome
    detail: str
    #: True only when a check proved the failure. A judgement is never confirmed.
    confirmed: bool = False


@dataclass(frozen=True)
class GateReport:
    """Every rule's answer and the outcome they add up to."""

    policy: GatePolicy
    evaluation_run_id: str
    outcome: GateOutcome
    results: tuple[GateResult, ...]

    @property
    def failures(self) -> tuple[GateResult, ...]:
        return tuple(item for item in self.results if item.outcome is GateOutcome.FAIL)

    @property
    def warnings(self) -> tuple[GateResult, ...]:
        """Rules that reported something without confirming it: an opinion, not a failure."""

        return tuple(
            item
            for item in self.results
            if item.outcome is not GateOutcome.PASS and not item.confirmed
        )


class _StrictLoader(yaml.SafeLoader):
    """A safe loader that refuses a key written twice.

    PyYAML keeps the last value silently, so `forbidden_paths: [".github/"]` followed by
    `forbidden_paths: []` would quietly remove the rule the author meant to keep.
    """

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[object, object]:
        seen: set[object] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    None, None, f"duplicate key {key!r} in a policy file", key_node.start_mark
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def load_policy(path: Path, *, name: str = "default") -> GatePolicy:
    """Read one named policy out of a YAML file, refusing anything malformed."""

    try:
        payload = yaml.load(path.read_text(encoding="utf-8"), Loader=_StrictLoader)  # noqa: S506
    except OSError as exc:
        raise GateError(f"{path}: cannot read file: {exc}") from exc
    except yaml.YAMLError as exc:
        raise GateError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(payload, dict):
        raise GateError(f"{path}: the policy file must contain a mapping")
    policies = payload.get("policies")
    if not isinstance(policies, dict) or name not in policies:
        raise GateError(f"{path}: no policy named {name!r}")
    raw = policies[name]
    if not isinstance(raw, dict):
        raise GateError(f"{path}: policy {name!r} must be a mapping")
    try:
        return GatePolicy.model_validate({"name": name, **raw})
    except ValidationError as exc:
        raise GateError(f"{path}: policy {name!r} is invalid: {exc}") from exc


def _check_by_name(checks: Sequence[CheckResult], name: str) -> CheckResult | None:
    return next((check for check in checks if check.name == name), None)


def _check_by_tool(checks: Sequence[CheckResult], tool: CheckTool) -> list[CheckResult]:
    return [check for check in checks if check.tool is tool]


def _one_check_rule(rule: str, check: CheckResult) -> GateResult:
    """One check's answer: a failure is confirmed, anything else that is not a pass is a gap."""

    if check.status is CheckStatus.FAILED:
        return GateResult(
            rule=rule,
            outcome=GateOutcome.FAIL,
            detail=f"{check.name} exited {check.exit_code}: {check.summary[:200]}",
            confirmed=True,
        )
    if check.status is not CheckStatus.PASSED:
        # ERROR (could not run) and SKIPPED are gaps, not passes: SPEC §5.
        return GateResult(
            rule=rule,
            outcome=GateOutcome.INCONCLUSIVE,
            detail=f"{check.name} reported {check.status.value}, so the check did not answer",
        )
    return GateResult(rule=rule, outcome=GateOutcome.PASS, detail=check.name)


def _gate_checks(
    policy: GatePolicy, checks: Sequence[CheckResult], result: EvaluationResult
) -> list[GateResult]:
    """Required tools and the held-out suite, then the confirmed findings they produced."""

    outcomes: list[GateResult] = []
    for tool in policy.required_tools:
        measured = _check_by_tool(checks, tool)
        rule = f"tool:{tool.value}"
        if not measured:
            outcomes.append(
                GateResult(
                    rule=rule,
                    outcome=GateOutcome.INCONCLUSIVE,
                    detail="the evaluation carries no such check, so nothing was measured",
                )
            )
            continue
        # Every instance of the tool has to pass: a task may run pytest twice, and one green
        # run does not cancel a red one.
        outcomes.extend(_one_check_rule(rule, check) for check in measured)

    if policy.require_heldout:
        heldout = _check_by_name(checks, HELDOUT_NAME)
        if heldout is None:
            outcomes.append(
                GateResult(
                    rule="held-out",
                    outcome=GateOutcome.INCONCLUSIVE,
                    detail="no held-out result was recorded for this patch",
                )
            )
        else:
            outcomes.append(_one_check_rule("held-out", heldout))

    blocking = [
        finding
        for finding in result.findings
        if finding.confirmed
        and finding.source is FindingSource.automated
        and finding.severity in CONFIRMED_SEVERITIES
    ]
    if blocking:
        refs = ", ".join(sorted({finding.evidence_ref for finding in blocking}))
        outcomes.append(
            GateResult(
                rule="confirmed-findings",
                outcome=GateOutcome.FAIL,
                detail=f"confirmed automated findings: {refs}",
                confirmed=True,
            )
        )
    return outcomes


def _gate_security(policy: GatePolicy, result: EvaluationResult) -> GateResult:
    """Confirmed security findings fail; a judge's security opinion only warns."""

    threshold = _SEVERITY_ORDER[policy.security_severity]
    confirmed = [
        finding
        for finding in result.findings
        if finding.source is FindingSource.automated
        and finding.confirmed
        and finding.category.value == "security"
        and _SEVERITY_ORDER[finding.severity] >= threshold
    ]
    if confirmed:
        return GateResult(
            rule="security",
            outcome=GateOutcome.FAIL,
            detail=f"{len(confirmed)} confirmed security finding(s)",
            confirmed=True,
        )
    opinions = [
        finding
        for finding in result.findings
        if finding.source is FindingSource.judge
        and finding.category.value == "security"
        and _SEVERITY_ORDER[finding.severity] >= threshold
    ]
    if opinions:
        # An unconfirmed security opinion is a warning a human should read; failing on it
        # would publish an AI guess as a vulnerability.
        return GateResult(
            rule="security",
            outcome=GateOutcome.PASS,
            detail=f"{len(opinions)} unconfirmed security finding(s) reported for review",
        )
    return GateResult(rule="security", outcome=GateOutcome.PASS, detail="no security findings")


def _gate_score(policy: GatePolicy, result: EvaluationResult) -> GateResult:
    if result.verdict is EvaluationVerdict.INCONCLUSIVE:
        # A score assembled without the judge's criteria is incomplete, not low: failing a
        # policy minimum on it would report a gap in the evidence as a bad patch.
        return GateResult(
            rule="score",
            outcome=GateOutcome.INCONCLUSIVE,
            detail="the evaluation reached no decision, so its score is incomplete",
        )
    if result.score.overall < policy.min_score:
        return GateResult(
            rule="score",
            outcome=GateOutcome.FAIL,
            detail=f"{result.score.overall:.2f} below the minimum {policy.min_score:.2f}",
        )
    return GateResult(
        rule="score",
        outcome=GateOutcome.PASS,
        detail=f"{result.score.overall:.2f} at or above {policy.min_score:.2f}",
    )


def _gate_verdict(result: EvaluationResult) -> GateResult:
    if result.verdict is EvaluationVerdict.INCONCLUSIVE:
        return GateResult(
            rule="verdict",
            outcome=GateOutcome.INCONCLUSIVE,
            detail="the evaluation reached no decision",
        )
    if result.verdict is EvaluationVerdict.REJECTED:
        return GateResult(
            rule="verdict",
            outcome=GateOutcome.FAIL,
            detail="the evaluation rejected the patch",
        )
    return GateResult(rule="verdict", outcome=GateOutcome.PASS, detail=result.verdict.value)


def _gate_size(
    policy: GatePolicy, request: EvaluationRequest, changed: Sequence[ChangedFile]
) -> GateResult:
    if policy.max_changed_files is None and policy.max_changed_lines is None:
        return GateResult(rule="size", outcome=GateOutcome.PASS, detail="no size limit")
    lines = sum(item.added + item.deleted for item in changed)
    if policy.max_changed_files is not None and len(changed) > policy.max_changed_files:
        return GateResult(
            rule="size",
            outcome=GateOutcome.FAIL,
            detail=f"{len(changed)} changed file(s) above {policy.max_changed_files}",
        )
    if policy.max_changed_lines is not None and lines > policy.max_changed_lines:
        return GateResult(
            rule="size",
            outcome=GateOutcome.FAIL,
            detail=f"{lines} changed line(s) above {policy.max_changed_lines}",
        )
    return GateResult(
        rule="size",
        outcome=GateOutcome.PASS,
        detail=f"{len(changed)} file(s), {lines} line(s)",
    )


def _is_forbidden(path: str, forbidden: str) -> bool:
    """An exact match, or a descendant of a forbidden directory.

    A plain prefix would also match `.gitlab-ci.yml.example` against `.gitlab-ci.yml`, and
    blocking a file that merely shares a name is a rule nobody asked for.
    """

    if path == forbidden:
        return True
    directory = forbidden if forbidden.endswith("/") else f"{forbidden}/"
    return path.startswith(directory)


def _gate_paths(policy: GatePolicy, changed: Sequence[ChangedFile]) -> GateResult:
    if not policy.forbidden_paths:
        return GateResult(rule="paths", outcome=GateOutcome.PASS, detail="no forbidden paths")
    touched = [
        path
        for item in changed
        # A rename away from a forbidden path still touched it: moving the file is how the
        # rule would be removed, so the source counts too.
        for path in (item.path, item.original_path)
        if path and any(_is_forbidden(path, forbidden) for forbidden in policy.forbidden_paths)
    ]
    if touched:
        return GateResult(
            rule="paths",
            outcome=GateOutcome.FAIL,
            detail=f"forbidden path(s) touched: {', '.join(sorted(touched))}",
        )
    return GateResult(rule="paths", outcome=GateOutcome.PASS, detail="inside the allowed paths")


def _gate_judge_mode(policy: GatePolicy, request: EvaluationRequest) -> GateResult:
    if not policy.allowed_judge_modes:
        return GateResult(rule="judge-mode", outcome=GateOutcome.PASS, detail="any mode allowed")
    mode = request.config.mode
    if mode in policy.allowed_judge_modes:
        return GateResult(rule="judge-mode", outcome=GateOutcome.PASS, detail=mode.value)
    allowed = ", ".join(item.value for item in policy.allowed_judge_modes)
    return GateResult(
        rule="judge-mode",
        outcome=GateOutcome.FAIL,
        detail=f"evaluated in {mode.value}, policy allows {allowed}",
    )


def _gate_budget(policy: GatePolicy, result: EvaluationResult) -> GateResult:
    if policy.max_judge_tokens is None:
        return GateResult(rule="budget", outcome=GateOutcome.PASS, detail="no token limit")
    if not result.assessments:
        return GateResult(
            rule="budget",
            outcome=GateOutcome.INCONCLUSIVE,
            detail="the evaluation records no judge usage, so its cost is unknown",
        )
    used = sum(item.tokens_in + item.tokens_out for item in result.assessments)
    if used > policy.max_judge_tokens:
        return GateResult(
            rule="budget",
            outcome=GateOutcome.FAIL,
            detail=f"{used} judge token(s) above {policy.max_judge_tokens}",
        )
    return GateResult(rule="budget", outcome=GateOutcome.PASS, detail=f"{used} judge token(s)")


def evidence_mismatch(
    result: EvaluationResult,
    checks: Sequence[CheckResult],
    *,
    recorded_sha256: str | None = None,
) -> str | None:
    """Say why the checks no longer match the evaluation, or ``None`` when they do.

    Re-verification rewrites ``verdicts.check_results`` in place, and the stored evaluation
    keeps the findings it derived from the old ones. Gating that pair would combine a verdict
    about one set of checks with the evidence of another — so the gate refuses and says which
    command fixes it.

    The exact answer is the digest the evaluation recorded. An evaluation written before that
    field existed has no digest and falls back to comparing the failures it recorded: that
    catches a check going from failing to passing, but not one going from passing to
    unmeasured.
    """

    if recorded_sha256 is not None:
        current_digest = digest([item.model_dump(mode="json") for item in checks])
        if current_digest == recorded_sha256:
            return None
        return (
            "the run's checks changed after this evaluation was made "
            f"(recorded {recorded_sha256[:12]}…, current {current_digest[:12]}…): "
            "re-run `fixjury judge evaluate` on this run before gating it"
        )

    recorded = {
        finding.evidence_ref
        for finding in result.findings
        if finding.source is FindingSource.automated
        and finding.confirmed
        and finding.evidence_ref.startswith("check:")
    }
    current = {f"check:{check.name}" for check in checks if check.status is CheckStatus.FAILED}
    if recorded == current:
        return None
    return (
        "the stored evaluation was made from different check evidence than the run carries "
        f"now (recorded: {sorted(recorded) or 'none'}, current: {sorted(current) or 'none'}): "
        "re-run `fixjury judge evaluate` on this run before gating it"
    )


def evaluate_gates(
    policy: GatePolicy,
    *,
    request: EvaluationRequest,
    result: EvaluationResult,
    evaluation_run_id: str,
) -> GateReport:
    """Apply every rule and combine the answers into one outcome."""

    changed = parse_changed_files(request.diff)
    results = [
        _gate_verdict(result),
        *_gate_checks(policy, request.checks, result),
        _gate_score(policy, result),
        _gate_security(policy, result),
        _gate_size(policy, request, changed),
        _gate_paths(policy, changed),
        _gate_judge_mode(policy, request),
        _gate_budget(policy, result),
    ]
    overall = max((item.outcome for item in results), key=lambda outcome: _SEVERITY[outcome])
    return GateReport(
        policy=policy,
        evaluation_run_id=evaluation_run_id,
        outcome=overall,
        results=tuple(results),
    )


__all__ = [
    "GateError",
    "GateOutcome",
    "GatePolicy",
    "GateReport",
    "GateResult",
    "evaluate_gates",
    "load_policy",
]
