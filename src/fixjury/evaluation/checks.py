"""Structured check evidence (PLAN_V1.1 §1.2).

Verification already knows what each check printed; the verdict row kept only five
booleans. This module turns an outcome into the evidence a judge may cite. The stored
shape lives next to ``CheckOutcome`` in :mod:`fixjury.verify.verdict` and is re-exported
here, so callers of the evaluation package do not have to know both modules.

``ERROR`` is the important status here: a check that could not run is not a failed check,
and treating the two the same would turn a broken sandbox into a rejected patch.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from fixjury.evaluation.contracts import HELDOUT_NAME, CheckResult, CheckStatus
from fixjury.tasks.schema import CheckTool
from fixjury.verify.verdict import CheckOutcome, outcomes_from_stored, stored_outcomes

#: `coverage report` ends with a TOTAL row; the percentage is its last column.
_COVERAGE_TOTAL = re.compile(r"^TOTAL\b.*?(\d{1,3})%\s*$", re.MULTILINE)

#: How much of a check's output travels into evidence. The container log keeps the rest.
SUMMARY_LIMIT = 600

_NAME_LIMIT = 128


def coverage_percent(output: str) -> float | None:
    """The total coverage a ``coverage report`` printed, or ``None`` when it printed none."""

    match = _COVERAGE_TOTAL.search(output)
    if match is None:
        return None
    return float(match.group(1))


def _summary(output: str) -> str:
    text = output.strip()
    return text[-SUMMARY_LIMIT:] if len(text) > SUMMARY_LIMIT else text


def to_check_result(outcome: CheckOutcome) -> CheckResult:
    """One outcome as structured evidence, with the status the exit code alone cannot give."""

    name = HELDOUT_NAME if outcome.heldout else outcome.command[:_NAME_LIMIT]
    if outcome.exit_code != 0:
        return CheckResult(
            name=name,
            tool=outcome.tool,
            status=CheckStatus.FAILED,
            exit_code=outcome.exit_code,
            summary=_summary(outcome.output),
        )
    if outcome.tool is CheckTool.coverage:
        percent = coverage_percent(outcome.output)
        if percent is None:
            # Exit 0 with no TOTAL line means nothing was measured. Calling that PASSED
            # would hand the judge a number that does not exist.
            return CheckResult(
                name=name,
                tool=outcome.tool,
                status=CheckStatus.ERROR,
                exit_code=outcome.exit_code,
                summary="coverage report has no TOTAL line",
            )
        return CheckResult(
            name=name,
            tool=outcome.tool,
            status=CheckStatus.PASSED,
            exit_code=outcome.exit_code,
            summary=f"coverage {percent:.1f}%",
            coverage_percent=percent,
        )
    return CheckResult(
        name=name,
        tool=outcome.tool,
        status=CheckStatus.PASSED,
        exit_code=outcome.exit_code,
        summary=_summary(outcome.output),
    )


def to_check_results(outcomes: Sequence[CheckOutcome]) -> tuple[CheckResult, ...]:
    return tuple(to_check_result(outcome) for outcome in outcomes)


__all__ = [
    "HELDOUT_NAME",
    "SUMMARY_LIMIT",
    "coverage_percent",
    "outcomes_from_stored",
    "stored_outcomes",
    "to_check_result",
    "to_check_results",
]
