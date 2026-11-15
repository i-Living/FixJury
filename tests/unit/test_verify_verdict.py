from __future__ import annotations

from fixjury.models import RejectionCode
from fixjury.tasks.schema import CheckTool
from fixjury.verify.policy import PolicyResult, Violation
from fixjury.verify.verdict import (
    CheckOutcome,
    assemble,
    outcomes_from_stored,
    stored_outcomes,
)

PASSING_POLICY = PolicyResult(passed=True, violations=(), files=())


def outcome(tool: CheckTool, code: int = 0, command: str | None = None) -> CheckOutcome:
    return CheckOutcome(
        command=command or f"{tool.value} tests/",
        tool=tool,
        exit_code=code,
        output="",
    )


def test_everything_passing_is_an_automated_pass() -> None:
    result = assemble(
        (outcome(CheckTool.pytest), outcome(CheckTool.ruff), outcome(CheckTool.mypy)),
        policy=PASSING_POLICY,
        heldout_pass=True,
        tool_versions={"pytest": "8.3.0"},
    )

    assert result.automated_pass is True
    assert result.rejections == ()
    assert result.tool_versions == {"pytest": "8.3.0"}


def test_a_failing_test_check_is_a_build_break() -> None:
    result = assemble(
        (outcome(CheckTool.pytest, 1),),
        policy=PASSING_POLICY,
        heldout_pass=True,
        tool_versions={},
    )

    assert result.tests_pass is False
    assert result.automated_pass is False
    assert RejectionCode.BUILD_BREAK in {violation.code for violation in result.rejections}


def test_a_task_without_a_lint_check_is_not_a_lint_failure() -> None:
    result = assemble(
        (outcome(CheckTool.pytest),),
        policy=PASSING_POLICY,
        heldout_pass=True,
        tool_versions={},
    )

    assert result.lint_pass is True
    assert result.type_pass is True
    assert result.automated_pass is True


def test_a_task_without_a_test_check_has_no_evidence() -> None:
    result = assemble(
        (outcome(CheckTool.ruff),),
        policy=PASSING_POLICY,
        heldout_pass=True,
        tool_versions={},
    )

    assert result.tests_pass is False
    assert result.automated_pass is False


def test_a_missing_heldout_bundle_is_null_not_false() -> None:
    result = assemble(
        (outcome(CheckTool.pytest),),
        policy=PASSING_POLICY,
        heldout_pass=None,
        tool_versions={},
    )

    assert result.heldout_pass is None
    assert result.automated_pass is True
    assert result.rejections == ()


def test_a_failing_heldout_suite_blocks_the_pass() -> None:
    result = assemble(
        (outcome(CheckTool.pytest),),
        policy=PASSING_POLICY,
        heldout_pass=False,
        tool_versions={},
    )

    assert result.automated_pass is False
    assert RejectionCode.INCOMPLETE_FIX in {violation.code for violation in result.rejections}


def test_a_policy_failure_blocks_the_pass() -> None:
    policy = PolicyResult(
        passed=False,
        violations=(),
        files=(),
    )
    result = assemble(
        (outcome(CheckTool.pytest),),
        policy=policy,
        heldout_pass=True,
        tool_versions={},
    )

    assert result.policy_pass is False
    assert result.automated_pass is False


def test_extra_violations_reach_the_verdict() -> None:
    result = assemble(
        (),
        policy=PASSING_POLICY,
        heldout_pass=None,
        tool_versions={},
        extra_violations=(Violation(RejectionCode.BUILD_BREAK, "the diff does not apply"),),
        note="the diff does not apply to the base commit",
    )

    assert result.automated_pass is False
    assert result.note == "the diff does not apply to the base commit"
    assert result.rejections[0].note == "the diff does not apply"


def test_the_verdict_carries_the_raw_outcomes() -> None:
    outcomes = (outcome(CheckTool.pytest, command="pytest tests/test_api.py"),)

    result = assemble(outcomes, policy=PASSING_POLICY, heldout_pass=True, tool_versions={})

    assert result.outcomes == outcomes


def test_stored_outcomes_round_trip() -> None:
    outcomes = (
        CheckOutcome(command="pytest tests", tool=CheckTool.pytest, exit_code=0, output="4 passed"),
        CheckOutcome(
            command="ruff check src", tool=CheckTool.ruff, exit_code=1, output="E501 line too long"
        ),
        CheckOutcome(
            command="pytest tests_heldout",
            tool=CheckTool.pytest,
            exit_code=1,
            output="",
            heldout=True,
        ),
    )

    assert outcomes_from_stored(stored_outcomes(outcomes)) == outcomes


def test_a_stored_row_this_module_cannot_read_is_skipped() -> None:
    """A missing check is visible as missing evidence; a fabricated one is invisible."""

    restored = outcomes_from_stored(
        [
            {"command": "pytest tests", "tool": "pytest", "exit_code": 0},
            {"command": "pytest tests", "tool": "not-a-tool", "exit_code": 0},
            {"command": "", "tool": "pytest", "exit_code": 0},
            {"command": "pytest tests", "tool": "pytest", "exit_code": "zero"},
            {"command": "mypy src", "tool": "mypy", "exit_code": True},
            "not a row",
        ]
    )

    assert len(restored) == 1
    assert restored[0].command == "pytest tests"
