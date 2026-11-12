"""Verdict assembly (PLAN §11, task 3.4).

``automated_pass = tests_pass ∧ lint_pass ∧ type_pass ∧ policy_pass ∧ (heldout_pass ≠ false)``
from PLAN §3.2. Pure functions: the runner collects outcomes, this module decides
what they mean, and the platform writes the row.
"""

from __future__ import annotations

from dataclasses import dataclass

from fixjury.models import RejectionCode
from fixjury.tasks.schema import CheckTool
from fixjury.verify.policy import PolicyResult, Violation


@dataclass(frozen=True)
class CheckOutcome:
    """One command the verification container executed."""

    command: str
    tool: CheckTool
    exit_code: int
    output: str
    # The held-out suite is a pytest run, but it answers a different question
    # than the task's own checks, so it must not feed ``tests_pass``.
    heldout: bool = False

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


@dataclass(frozen=True)
class VerificationResult:
    """Everything a verdict row needs, and nothing that depends on an agent name."""

    tests_pass: bool
    lint_pass: bool
    type_pass: bool
    policy_pass: bool
    heldout_pass: bool | None
    tool_versions: dict[str, str]
    rejections: tuple[Violation, ...]
    outcomes: tuple[CheckOutcome, ...] = ()
    note: str | None = None

    @property
    def automated_pass(self) -> bool:
        return (
            self.tests_pass
            and self.lint_pass
            and self.type_pass
            and self.policy_pass
            and self.heldout_pass is not False
        )


def _failed(outcomes: tuple[CheckOutcome, ...], tool: CheckTool) -> list[CheckOutcome]:
    return [outcome for outcome in outcomes if outcome.tool is tool and not outcome.passed]


def assemble(
    outcomes: tuple[CheckOutcome, ...],
    *,
    policy: PolicyResult,
    heldout_pass: bool | None,
    tool_versions: dict[str, str],
    extra_violations: tuple[Violation, ...] = (),
    note: str | None = None,
) -> VerificationResult:
    """Turn raw check outcomes into the booleans the metrics read."""

    tests = tuple(
        outcome for outcome in outcomes if outcome.tool is CheckTool.pytest and not outcome.heldout
    )
    lint = tuple(outcome for outcome in outcomes if outcome.tool is CheckTool.ruff)
    types = tuple(outcome for outcome in outcomes if outcome.tool is CheckTool.mypy)

    violations: list[Violation] = list(extra_violations)
    for outcome in _failed(outcomes, CheckTool.pytest):
        if not outcome.heldout:
            violations.append(Violation(RejectionCode.BUILD_BREAK, f"{outcome.command} failed"))
    for outcome in _failed(outcomes, CheckTool.ruff):
        violations.append(Violation(RejectionCode.BUILD_BREAK, f"{outcome.command} failed"))
    for outcome in _failed(outcomes, CheckTool.mypy):
        violations.append(Violation(RejectionCode.BUILD_BREAK, f"{outcome.command} failed"))
    if heldout_pass is False:
        violations.append(Violation(RejectionCode.INCOMPLETE_FIX, "the held-out suite still fails"))
    violations.extend(policy.violations)

    return VerificationResult(
        tests_pass=bool(tests) and all(outcome.passed for outcome in tests),
        # A task without a lint or type check is not a task that failed one.
        lint_pass=all(outcome.passed for outcome in lint),
        type_pass=all(outcome.passed for outcome in types),
        policy_pass=policy.passed,
        heldout_pass=heldout_pass,
        tool_versions=dict(tool_versions),
        rejections=tuple(violations),
        outcomes=outcomes,
        note=note,
    )
