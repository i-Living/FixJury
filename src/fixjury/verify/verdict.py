"""Verdict assembly (PLAN §11, task 3.4).

``automated_pass = tests_pass ∧ lint_pass ∧ type_pass ∧ policy_pass ∧ (heldout_pass ≠ false)``
from PLAN §3.2. Pure functions: the runner collects outcomes, this module decides
what they mean, and the platform writes the row.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
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


def stored_outcomes(outcomes: tuple[CheckOutcome, ...]) -> list[dict[str, object]]:
    """The JSONB shape of ``verdicts.check_results``.

    Serialising the outcome next to its type keeps the writer and the reader in one
    module: they have to agree on every key, and a split definition drifts silently.
    """

    return [
        {
            "command": outcome.command,
            "tool": outcome.tool.value,
            "exit_code": outcome.exit_code,
            "output": outcome.output,
            "heldout": outcome.heldout,
        }
        for outcome in outcomes
    ]


def _as_int(value: object) -> int | None:
    # ``bool`` is an ``int`` in Python; a stored ``True`` is not an exit code.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _as_text(value: object) -> str:
    return value if isinstance(value, str) else ""


def outcomes_from_stored(raw: Iterable[object]) -> tuple[CheckOutcome, ...]:
    """Rebuild outcomes from a stored verdict.

    A row this module cannot read is skipped rather than guessed at: a missing check is
    visible as missing evidence, while a fabricated one is invisible.
    """

    outcomes: list[CheckOutcome] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        command = _as_text(item.get("command"))
        exit_code = _as_int(item.get("exit_code"))
        try:
            tool = CheckTool(_as_text(item.get("tool")))
        except ValueError:
            continue
        if not command or exit_code is None:
            continue
        outcomes.append(
            CheckOutcome(
                command=command,
                tool=tool,
                exit_code=exit_code,
                output=_as_text(item.get("output")),
                heldout=item.get("heldout") is True,
            )
        )
    return tuple(outcomes)


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
