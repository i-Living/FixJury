from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from pydantic import ValidationError

from fixjury.tasks.schema import (
    CheckCommand,
    CheckTool,
    TaskDocument,
    parse_check,
)


def document_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "T-001",
        "repo": "https://github.com/pallets/click",
        "license": "BSD-3-Clause",
        "base_commit": "0" * 39 + "1",
        "fix_commit": "0" * 39 + "2",
        "issue": {
            "number": 2712,
            "url": "https://github.com/pallets/click/issues/2712",
            "created_at": "2025-09-14",
            "fix_merged_at": "2025-10-02",
            "body": "Completion output for --foo-bar is wrong.",
        },
        "title": "Option name with a dash breaks shell completion",
        "acceptance": ["completion output for --foo-bar matches the option name"],
        "checks": ["pytest tests/test_shell_completion.py", "ruff check src", "mypy src"],
        "forbidden": ["edits to files under .github/"],
        "allowed_paths": ["src/click/shell_completion.py", "tests/"],
        "difficulty": "medium",
        "heldout_ref": "benchmarks/heldout/T-001",
        "cutoff_assumption": "clean",
        "frozen_at": "2026-11-12T10:00:00+00:00",
        "frozen_by": "owner",
    }
    payload.update(overrides)
    return payload


def test_document_accepts_a_complete_task() -> None:
    document = TaskDocument.model_validate(document_payload())

    assert document.id == "T-001"
    assert document.frozen is True
    assert document.issue.created_at == dt.date(2025, 9, 14)
    assert document.difficulty.value == "medium"


def test_document_versioned_id_is_allowed() -> None:
    document = TaskDocument.model_validate(document_payload(id="T-014-v2"))

    assert document.id == "T-014-v2"


@pytest.mark.parametrize("task_id", ["T-1", "t-001", "T-0001", "T-001-v0", "X-001"])
def test_document_rejects_bad_ids(task_id: str) -> None:
    with pytest.raises(ValidationError):
        TaskDocument.model_validate(document_payload(id=task_id))


def test_document_requires_a_github_repo_url() -> None:
    with pytest.raises(ValidationError):
        TaskDocument.model_validate(document_payload(repo="https://gitlab.com/pallets/click"))


def test_document_rejects_short_commit() -> None:
    with pytest.raises(ValidationError):
        TaskDocument.model_validate(document_payload(base_commit="8e6b0f0"))


def test_document_rejects_unknown_license() -> None:
    with pytest.raises(ValidationError):
        TaskDocument.model_validate(document_payload(license="GPL-3.0"))


def test_document_rejects_issue_url_number_mismatch() -> None:
    with pytest.raises(ValidationError):
        TaskDocument.model_validate(
            document_payload(
                issue={
                    "number": 2712,
                    "url": "https://github.com/pallets/click/issues/2713",
                    "created_at": "2025-09-14",
                }
            )
        )


def test_document_requires_frozen_fields_together() -> None:
    with pytest.raises(ValidationError):
        TaskDocument.model_validate(document_payload(frozen_by=None))


def test_document_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        TaskDocument.model_validate(document_payload(job="nope"))


@pytest.mark.parametrize(
    "raw",
    [
        "pytest",
        "pytest tests/test_option.py",
        "pytest tests/unit tests/integration/test_api.py",
        "ruff check src tests",
        "mypy src",
    ],
)
def test_parse_check_accepts_whitelisted_commands(raw: str) -> None:
    command = parse_check(raw)

    assert isinstance(command, CheckCommand)
    assert command.tool in CheckTool.__members__.values()


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "bash -c 'rm -rf /'",
        "pytest tests; rm -rf /",
        "pytest tests && echo ok",
        "pytest $(whoami)",
        "pytest /etc/passwd",
        "pytest ../outside",
        "pytest -k secret",
        "ruff src",
        "ruff check",
        "mypy",
        "uv run pytest",
    ],
)
def test_parse_check_rejects_everything_else(raw: str) -> None:
    with pytest.raises(ValueError, match=r"check|ruff|mypy|pytest"):
        parse_check(raw)


def test_parse_check_splits_tool_and_paths() -> None:
    command = parse_check("ruff check src tests")

    assert command.tool is CheckTool.ruff
    assert command.args == ("src", "tests")
