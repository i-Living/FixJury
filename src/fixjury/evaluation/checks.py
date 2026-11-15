"""Structured check evidence (PLAN_V1.1 §1.2).

Verification already knows what each check printed; the verdict row kept only five
booleans. This module turns an outcome into the evidence a judge may cite. The stored
shape lives next to ``CheckOutcome`` in :mod:`fixjury.verify.verdict` and is re-exported
here, so callers of the evaluation package do not have to know both modules.

The status is the point of this module. An exit code alone cannot tell "the tests failed"
from "pytest could not run", and treating the two the same turns a broken sandbox into a
rejected patch. Each tool has its own codes, and ``coverage`` even within one tool:
``coverage report`` exits 1 when it has no data, while ``coverage run -m pytest`` inherits
pytest's codes.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from fixjury.evaluation.contracts import HELDOUT_NAME, CheckResult, CheckStatus
from fixjury.tasks.schema import CheckTool
from fixjury.verify.verdict import CheckOutcome, outcomes_from_stored, stored_outcomes

#: `coverage report` ends with a TOTAL row; the percentage is its last column, decimals
#: included — a lazy prefix plus a digits-only group would read "87.32%" as "32".
_COVERAGE_TOTAL = re.compile(r"^TOTAL\b.*?(\d{1,3}(?:\.\d+)?)%\s*$", re.MULTILINE)

#: How much of a check's output travels into evidence. The container log keeps the rest.
SUMMARY_LIMIT = 600

_NAME_LIMIT = 128

#: The exit code that means "the check ran and found a problem". pytest: 1 is a test
#: failure (2 interrupted, 3 internal error, 4 usage, 5 collected nothing). ruff and mypy:
#: 1 is a finding, 2 is the tool refusing to run.
_VIOLATION_EXIT = 1


def coverage_percent(output: str) -> float | None:
    """The total coverage a ``coverage report`` printed, or ``None`` when it printed none."""

    match = _COVERAGE_TOTAL.search(output)
    if match is None:
        return None
    return float(match.group(1))


def _summary(output: str) -> str:
    text = output.strip()
    return text[-SUMMARY_LIMIT:] if len(text) > SUMMARY_LIMIT else text


def _is_coverage_report(command: str) -> bool:
    return command.split()[1:2] == ["report"]


def _status(outcome: CheckOutcome) -> CheckStatus:
    """What the exit code means for this tool, and for this coverage subcommand."""

    if outcome.exit_code == 0:
        return CheckStatus.PASSED
    if outcome.tool is CheckTool.coverage and _is_coverage_report(outcome.command):
        # `coverage report` exits 1 when there is no data to report: it measured nothing,
        # so the patch is unmeasured rather than failing a check it never ran.
        return CheckStatus.ERROR
    if outcome.exit_code == _VIOLATION_EXIT:
        return CheckStatus.FAILED
    return CheckStatus.ERROR


def to_check_result(outcome: CheckOutcome) -> CheckResult:
    """One outcome as structured evidence, with the status the exit code cannot give."""

    name = HELDOUT_NAME if outcome.heldout else outcome.command[:_NAME_LIMIT]
    status = _status(outcome)
    summary = _summary(outcome.output)
    percent: float | None = None

    if outcome.tool is CheckTool.coverage:
        if _is_coverage_report(outcome.command):
            percent = coverage_percent(outcome.output)
            if status is CheckStatus.PASSED and percent is None:
                # Exit 0 with no TOTAL line means nothing was measured. Calling that
                # PASSED would hand the judge a number that does not exist.
                return CheckResult(
                    name=name,
                    tool=outcome.tool,
                    status=CheckStatus.ERROR,
                    exit_code=outcome.exit_code,
                    summary="coverage report has no TOTAL line",
                )
            if percent is not None:
                summary = f"coverage {percent:.1f}%"
        elif status is CheckStatus.PASSED:
            # `coverage run -m pytest` prints the test run, not a table: the data went to
            # the coverage file, and this check is the measurement, not the reading.
            summary = summary or "coverage data recorded"

    return CheckResult(
        name=name,
        tool=outcome.tool,
        status=status,
        exit_code=outcome.exit_code,
        summary=summary,
        coverage_percent=percent,
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
