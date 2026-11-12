"""Task corpus documents: what a ``task.yaml`` may contain (PLAN §5, §8).

This module validates syntax and internal consistency. Corpus policy (cut-off
dates, held-out test presence, index agreement) lives in :mod:`fixjury.tasks.validate`
because those rules need the files around the document, not only the document.
"""

from __future__ import annotations

import datetime as dt
import re
import shlex
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fixjury.models import CutoffAssumption, Difficulty

#: No task in the corpus may predate this date (PLAN §5, contamination rule).
CUTOFF_DATE = dt.date(2025, 6, 1)

#: Target repositories must carry one of these SPDX licenses (PLAN §2).
ALLOWED_LICENSES: tuple[str, ...] = ("MIT", "Apache-2.0", "BSD-3-Clause")

_TASK_ID = re.compile(r"^T-\d{3}(-v[1-9]\d*)?$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_REPO = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_ISSUE_URL = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues/\d+$")

_SHELL_METACHARACTERS = frozenset(";|&<>$`\\\n\r\t\"'")


class CheckTool(StrEnum):
    """Commands the verification runner is allowed to execute (PLAN §8)."""

    pytest = "pytest"
    ruff = "ruff"
    mypy = "mypy"


class CheckCommand(BaseModel):
    """A parsed check command: a whitelisted tool plus relative paths."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: CheckTool
    args: tuple[str, ...]


def _check_relative_path(raw: str, *, what: str) -> str:
    if not raw or raw.startswith("-"):
        raise ValueError(f"{what} must not be empty or an option: {raw!r}")
    if raw.startswith("/") or raw.startswith("~") or "\\" in raw:
        raise ValueError(f"{what} must be a relative path: {raw!r}")
    parts = raw.split("/")
    if ".." in parts:
        raise ValueError(f"{what} must not escape the repository root: {raw!r}")
    return raw


def parse_check(raw: str) -> CheckCommand:
    """Parse a ``checks`` entry, rejecting anything outside the whitelist.

    Allowed shapes: ``pytest [path ...]``, ``ruff check <path ...>``,
    ``mypy <path ...>``. Flags, shell syntax and absolute paths are refused so
    a task file can never smuggle arbitrary commands into the runner.
    """

    if any(character in _SHELL_METACHARACTERS for character in raw):
        raise ValueError(f"check command contains shell syntax: {raw!r}")
    tokens = shlex.split(raw)
    if not tokens:
        raise ValueError("check command is empty")
    try:
        tool = CheckTool(tokens[0])
    except ValueError as exc:
        allowed = ", ".join(tool.value for tool in CheckTool)
        raise ValueError(f"check command must start with one of {allowed}: {raw!r}") from exc

    args = tokens[1:]
    if tool is CheckTool.ruff:
        if not args or args[0] != "check":
            raise ValueError(f"ruff checks must use the 'check' subcommand: {raw!r}")
        args = args[1:]
    if tool in (CheckTool.ruff, CheckTool.mypy) and not args:
        raise ValueError(f"{tool.value} check needs at least one path: {raw!r}")
    for argument in args:
        _check_relative_path(argument, what=f"{tool.value} path")
    return CheckCommand(tool=tool, args=tuple(args))


class TaskIssue(BaseModel):
    """Snapshot of the upstream issue, frozen with the task."""

    model_config = ConfigDict(extra="forbid")

    number: int = Field(gt=0)
    url: str
    created_at: dt.date | None = None
    fix_merged_at: dt.date | None = None
    body: str | None = None

    @field_validator("url")
    @classmethod
    def _valid_url(cls, value: str) -> str:
        if not _ISSUE_URL.match(value):
            raise ValueError(
                f"issue url must look like https://github.com/<owner>/<repo>/issues/<n>: {value!r}"
            )
        return value

    @model_validator(mode="after")
    def _url_matches_number(self) -> TaskIssue:
        if self.url.rsplit("/", 1)[-1] != str(self.number):
            raise ValueError(f"issue url {self.url!r} does not end with number {self.number}")
        return self


class TaskDocument(BaseModel):
    """A parsed ``task.yaml``.

    Facts about the upstream repository are optional at this layer so that an
    unfrozen draft can be parsed too. :mod:`fixjury.tasks.validate` requires
    them for a document that enters the corpus.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    repo: str
    license: str | None = None
    base_commit: str | None = None
    fix_commit: str | None = None
    issue: TaskIssue
    title: str
    acceptance: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    difficulty: Difficulty
    heldout_ref: str | None = None
    cutoff_assumption: CutoffAssumption = CutoffAssumption.clean
    frozen_at: dt.datetime | None = None
    frozen_by: str | None = None

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        if not _TASK_ID.match(value):
            raise ValueError(f"task id must look like T-001 or T-014-v2: {value!r}")
        return value

    @field_validator("repo")
    @classmethod
    def _valid_repo(cls, value: str) -> str:
        if not _REPO.match(value):
            raise ValueError(f"repo must be a GitHub https URL without a trailing slash: {value!r}")
        return value

    @field_validator("license")
    @classmethod
    def _allowed_license(cls, value: str | None) -> str | None:
        if value is not None and value not in ALLOWED_LICENSES:
            allowed = ", ".join(ALLOWED_LICENSES)
            raise ValueError(f"license must be one of {allowed}: {value!r}")
        return value

    @field_validator("base_commit", "fix_commit")
    @classmethod
    def _valid_commit(cls, value: str | None) -> str | None:
        if value is not None and not _COMMIT.match(value):
            raise ValueError(f"commit must be a full 40-character lowercase hex sha: {value!r}")
        return value

    @field_validator("title")
    @classmethod
    def _non_empty_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be empty")
        return value.strip()

    @field_validator("acceptance")
    @classmethod
    def _non_empty_acceptance(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not item.strip():
                raise ValueError("acceptance criteria must not be empty")
        return tuple(item.strip() for item in value)

    @field_validator("checks")
    @classmethod
    def _valid_checks(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            parse_check(item)
        return value

    @field_validator("allowed_paths")
    @classmethod
    def _valid_allowed_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _check_relative_path(item, what="allowed_paths entry")
        return value

    @field_validator("heldout_ref")
    @classmethod
    def _valid_heldout_ref(cls, value: str | None) -> str | None:
        if value is not None:
            _check_relative_path(value, what="heldout_ref")
        return value

    @model_validator(mode="after")
    def _freeze_fields_agree(self) -> TaskDocument:
        frozen = self.frozen_at is not None
        if frozen != (self.frozen_by is not None):
            raise ValueError("frozen_at and frozen_by must be set together")
        return self

    @property
    def frozen(self) -> bool:
        return self.frozen_at is not None
