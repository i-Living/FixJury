"""Evaluation contracts (PLAN_V1.1 §3.1).

One shape for an evaluation, shared by the benchmark now and by GitHub Review when it
lands: the request built from persisted rows, the context a judge is allowed to see,
the finding and score vocabulary, and the result that is stored.

Everything here is pure data — no database, no network, no container. The rules that
make a verdict trustworthy live in :mod:`fixjury.evaluation.scoring`; the rules that
decide what a judge may see live in `EvaluationContext`.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fixjury.models import (
    Criterion,
    EvaluationSource,
    EvaluationVerdict,
    FindingSeverity,
    FindingSource,
)
from fixjury.tasks.schema import CheckTool

#: The name reserved for the held-out suite's check. The judge sees its outcome and never
#: its command or output: those name the tests, and the tests are not for the judge.
HELDOUT_NAME = "held-out"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


class JudgeMode(StrEnum):
    """SPEC §4.2. Default is adaptive; ``single`` has to be able to run alone.

    It lives here rather than in :mod:`fixjury.models` because it is never a column type:
    the mode travels inside the evaluation configuration, which is stored as JSON.
    """

    single = "single"
    adaptive = "adaptive"
    multi = "multi"


class CheckStatus(StrEnum):
    """SPEC §1.2. ``ERROR`` means the check could not run, not that the code failed."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    WARNING = "WARNING"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


def canonical_json(payload: Any) -> str:
    """Stable serialisation: the same document always digests to the same hash."""

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def digest(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class EvaluationConfiguration(BaseModel):
    """What the evaluation was configured with; hashed and stored with every result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    mode: JudgeMode = JudgeMode.adaptive
    profile: str = Field(default="default", min_length=1, max_length=64)
    confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    max_judges: int = Field(default=3, ge=1, le=10)
    max_escalation_rounds: int = Field(default=2, ge=0, le=5)
    token_budget: int = Field(default=200_000, ge=0)
    timeout_s: int = Field(default=300, ge=1)
    #: Digest of the check evidence this evaluation was made from. Re-verification rewrites
    #: `verdicts.check_results` in place, so without it a gate could combine a stored verdict
    #: with a different set of checks. ``None`` means the evaluation predates the field, and a
    #: gate falls back to comparing the findings it recorded.
    evidence_sha256: str | None = Field(default=None)

    @field_validator("evidence_sha256")
    @classmethod
    def _valid_evidence_digest(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256.match(value):
            raise ValueError(f"evidence digest must be a sha256 hex string: {value!r}")
        return value

    def config_hash(self) -> str:
        return digest(self.model_dump(mode="json"))


class CheckResult(BaseModel):
    """One automated check, with the evidence a judge may cite (SPEC §1.2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    tool: CheckTool
    status: CheckStatus
    exit_code: int
    summary: str = Field(default="", max_length=2000)
    artifact_ref: str | None = Field(default=None, max_length=512)
    duration_ms: int | None = Field(default=None, ge=0)
    # Measured coverage, kept as a number so a gate or a report never has to parse the
    # summary text. ``None`` means the check measured nothing, which is not zero.
    coverage_percent: float | None = Field(default=None, ge=0.0, le=100.0)

    @model_validator(mode="after")
    def _status_agrees_with_the_exit_code(self) -> CheckResult:
        if self.status is CheckStatus.PASSED and self.exit_code != 0:
            raise ValueError(f"check {self.name!r} is PASSED with exit code {self.exit_code}")
        if self.status is CheckStatus.FAILED and self.exit_code == 0:
            raise ValueError(f"check {self.name!r} is FAILED with exit code 0")
        return self


class EvaluationFinding(BaseModel):
    """A finding with the evidence that supports it (SPEC §4.5).

    ``confirmed`` is reserved for automated checks: a judge opinion is never a confirmed
    failure, and the validator refuses the combination instead of trusting the caller.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    severity: FindingSeverity
    category: Criterion
    description: str = Field(min_length=1, max_length=2000)
    evidence_ref: str = Field(min_length=1, max_length=256)
    impact: str = Field(min_length=1, max_length=2000)
    source: FindingSource
    confirmed: bool = False
    path: str | None = Field(default=None, max_length=512)
    line: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _confirmation_belongs_to_checks(self) -> EvaluationFinding:
        if self.source is FindingSource.judge and self.confirmed:
            raise ValueError("a judge finding is never confirmed; confirmation comes from a check")
        return self


class CriterionScore(BaseModel):
    """One criterion's score with the evidence it rests on."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    criterion: Criterion
    score: float = Field(ge=0.0, le=100.0)
    weight: float = Field(ge=0.0, le=100.0)
    rationale: str = Field(default="", max_length=2000)
    evidence_refs: tuple[str, ...] = ()


class EvaluationScore(BaseModel):
    """The weighted score: criteria, weights and the number they produce (SPEC §5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    overall: float = Field(ge=0.0, le=100.0)
    criteria: tuple[CriterionScore, ...]
    profile: str = Field(min_length=1, max_length=64)
    profile_version: str = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def _is_a_consistent_percentage(self) -> EvaluationScore:
        criteria = [item.criterion for item in self.criteria]
        if len(set(criteria)) != len(criteria):
            raise ValueError("a criterion appears twice in one score")
        total = sum(item.weight for item in self.criteria)
        if abs(total - 100.0) > 1e-6:
            raise ValueError(f"criteria weights must add up to 100, got {total}")
        expected = sum(item.score * item.weight for item in self.criteria) / 100.0
        if abs(expected - self.overall) > 0.01:
            raise ValueError(
                f"overall {self.overall} does not match the weighted criteria ({expected:.2f})"
            )
        return self


class AssessmentSummary(BaseModel):
    """One judge's assessment, as the stored result refers to it (SPEC §2.6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    judge_id: str = Field(min_length=1, max_length=64)
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    prompt_version: str = Field(min_length=1, max_length=32)
    verdict: EvaluationVerdict
    overall: float = Field(ge=0.0, le=100.0)
    confidence: float = Field(ge=0.0, le=1.0)
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    cost_list_usd: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    latency_ms: int = Field(default=0, ge=0)
    escalated: bool = False


class EvaluationRequest(BaseModel):
    """Everything an evaluation needs, rebuilt from persisted rows.

    The diff digest is checked against the diff itself so a stored request cannot claim
    evidence it does not carry.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    source: EvaluationSource = EvaluationSource.benchmark
    subject_ref: str = Field(min_length=1, max_length=256)
    run_id: uuid.UUID
    base_commit: str
    diff: str
    diff_sha256: str
    spec_text: str = Field(min_length=1)
    checks: tuple[CheckResult, ...] = ()
    policy_pass: bool = True
    policy_violations: tuple[str, ...] = ()
    config: EvaluationConfiguration = EvaluationConfiguration()

    @field_validator("base_commit")
    @classmethod
    def _valid_commit(cls, value: str) -> str:
        if not _COMMIT.match(value):
            raise ValueError(
                f"base commit must be a full 40-character lowercase hex sha: {value!r}"
            )
        return value

    @field_validator("diff_sha256")
    @classmethod
    def _valid_digest(cls, value: str) -> str:
        if not _SHA256.match(value):
            raise ValueError(f"diff digest must be a sha256 hex string: {value!r}")
        return value

    @model_validator(mode="after")
    def _digest_matches_the_diff(self) -> EvaluationRequest:
        actual = hashlib.sha256(self.diff.encode("utf-8")).hexdigest()
        if actual != self.diff_sha256:
            raise ValueError("diff_sha256 does not match the diff it travels with")
        return self


class EvaluationContext(BaseModel):
    """Exactly what a judge may see (SPEC §3.2).

    Agent and model identity are not fields here on purpose: a context cannot leak what
    it does not carry. The held-out suite is reduced to its pass/fail outcome for the same
    reason — its command names the tests and its output quotes them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    subject_ref: str = Field(min_length=1, max_length=256)
    spec_text: str = Field(min_length=1)
    base_commit: str
    diff: str
    diff_sha256: str
    checks: tuple[CheckResult, ...] = ()
    changed_paths: tuple[str, ...] = ()
    heldout_passed: bool | None = None
    policy_pass: bool = True
    limitations: tuple[str, ...] = ()

    @classmethod
    def from_request(
        cls,
        request: EvaluationRequest,
        *,
        changed_paths: tuple[str, ...] = (),
        heldout_passed: bool | None = None,
        limitations: tuple[str, ...] = (),
    ) -> EvaluationContext:
        heldout = next(
            (check for check in request.checks if check.name == HELDOUT_NAME),
            None,
        )
        if heldout_passed is None and heldout is not None:
            # Only a definitive outcome is an outcome: a suite that could not run or was
            # skipped leaves the answer unknown rather than reporting a failure.
            if heldout.status is CheckStatus.PASSED:
                heldout_passed = True
            elif heldout.status is CheckStatus.FAILED:
                heldout_passed = False
        return cls(
            subject_ref=request.subject_ref,
            spec_text=request.spec_text,
            base_commit=request.base_commit,
            diff=request.diff,
            diff_sha256=request.diff_sha256,
            checks=tuple(check for check in request.checks if check.name != HELDOUT_NAME),
            changed_paths=changed_paths,
            heldout_passed=heldout_passed,
            policy_pass=request.policy_pass,
            limitations=limitations,
        )


class EvaluationResult(BaseModel):
    """The stored outcome of one evaluation (SPEC §4.5, §5).

    ``verdict`` is the final one; ``judge_verdict`` is what the judge said before the
    critical-failure rules, so an override is auditable rather than invisible.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    verdict: EvaluationVerdict
    judge_verdict: EvaluationVerdict | None = None
    score: EvaluationScore
    confidence: float = Field(ge=0.0, le=1.0)
    findings: tuple[EvaluationFinding, ...] = ()
    assessments: tuple[AssessmentSummary, ...] = ()
    limitations: tuple[str, ...] = ()
    escalated: bool = False
    reasoning_summary: str = Field(default="", max_length=8000)
    config_hash: str
    judge_config_hash: str | None = None

    @model_validator(mode="after")
    def _acceptance_cannot_override_a_confirmed_failure(self) -> EvaluationResult:
        if self.verdict is not EvaluationVerdict.ACCEPTED:
            return self
        blocking = [
            finding
            for finding in self.findings
            if finding.confirmed and finding.severity is FindingSeverity.critical
        ]
        if blocking:
            refs = ", ".join(finding.evidence_ref for finding in blocking)
            raise ValueError(f"ACCEPTED with confirmed critical findings: {refs}")
        return self
