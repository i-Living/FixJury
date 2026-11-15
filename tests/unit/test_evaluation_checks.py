from __future__ import annotations

from fixjury.evaluation.checks import (
    coverage_percent,
    to_check_result,
)
from fixjury.evaluation.contracts import CheckStatus
from fixjury.tasks.schema import CheckTool
from fixjury.verify.verdict import CheckOutcome

COVERAGE_REPORT = """Name                 Stmts   Miss Branch BrPart  Cover
-------------------------------------------------------
app/__init__.py          4      0      0      0   100%
app/service.py          52      6     10      1  87.32%
-------------------------------------------------------
TOTAL                   56      6     10      1  87.32%
"""

COVERAGE_REPORT_WITHOUT_BRANCHES = """Name                 Stmts   Miss  Cover
-------------------------------------------
TOTAL                   56      6    89%
"""


def outcome(
    command: str = "pytest tests",
    tool: CheckTool = CheckTool.pytest,
    exit_code: int = 0,
    output: str = "",
    heldout: bool = False,
) -> CheckOutcome:
    return CheckOutcome(
        command=command, tool=tool, exit_code=exit_code, output=output, heldout=heldout
    )


def test_coverage_percent_reads_the_total_row() -> None:
    assert coverage_percent(COVERAGE_REPORT) == 87.32
    assert coverage_percent(COVERAGE_REPORT_WITHOUT_BRANCHES) == 89.0


def test_coverage_percent_is_none_when_no_total_was_printed() -> None:
    assert coverage_percent("Coverage.py warning: No data was collected.") is None


def test_a_passing_check_carries_its_output_as_evidence() -> None:
    result = to_check_result(outcome(output="2 passed"))

    assert result.status is CheckStatus.PASSED
    assert result.summary == "2 passed"
    assert result.exit_code == 0


def test_a_failing_check_is_failed_even_when_it_printed_a_summary() -> None:
    result = to_check_result(outcome(exit_code=1, output="1 failed, 3 passed"))

    assert result.status is CheckStatus.FAILED


def test_a_pytest_internal_error_is_not_a_test_failure() -> None:
    """Exit 3 is pytest admitting it could not run; the patch is unmeasured, not wrong."""

    result = to_check_result(outcome(exit_code=3, output="INTERNALERROR> Traceback …"))

    assert result.status is CheckStatus.ERROR


def test_a_coverage_report_reports_the_percentage_it_measured() -> None:
    result = to_check_result(
        outcome(command="coverage report", tool=CheckTool.coverage, output=COVERAGE_REPORT)
    )

    assert result.status is CheckStatus.PASSED
    assert result.summary == "coverage 87.3%"
    assert result.coverage_percent == 87.32


def test_a_coverage_run_that_measured_nothing_is_an_error_not_a_pass() -> None:
    result = to_check_result(
        outcome(command="coverage report", tool=CheckTool.coverage, output="No data to report.")
    )

    assert result.status is CheckStatus.ERROR
    assert result.coverage_percent is None
    assert "TOTAL" in result.summary


def test_an_empty_coverage_report_that_exits_nonzero_is_an_error_too() -> None:
    """`coverage report` exits 1 when there is no data: nothing was measured."""

    result = to_check_result(
        outcome(
            command="coverage report",
            tool=CheckTool.coverage,
            exit_code=1,
            output="No data to report.",
        )
    )

    assert result.status is CheckStatus.ERROR


def test_a_successful_coverage_run_is_the_measurement_not_the_reading() -> None:
    """`coverage run -m pytest` prints the test run, not a TOTAL table."""

    result = to_check_result(
        outcome(
            command="coverage run -m pytest tests",
            tool=CheckTool.coverage,
            output="4 passed in 1.20s",
        )
    )

    assert result.status is CheckStatus.PASSED
    assert result.coverage_percent is None
    assert result.summary == "4 passed in 1.20s"


def test_a_failed_coverage_run_is_a_failed_check() -> None:
    result = to_check_result(
        outcome(
            command="coverage run -m pytest tests",
            tool=CheckTool.coverage,
            exit_code=1,
            output="1 failed",
        )
    )

    assert result.status is CheckStatus.FAILED


def test_the_held_out_suite_is_named_for_what_it_is() -> None:
    result = to_check_result(outcome(command="pytest tests_heldout -q", exit_code=0, heldout=True))

    assert result.name == "held-out"
    assert result.tool is CheckTool.pytest


def test_a_long_command_is_trimmed_to_the_column_it_fits() -> None:
    result = to_check_result(outcome(command="pytest " + "tests/a_long_directory/ " * 40))

    assert len(result.name) == 128
