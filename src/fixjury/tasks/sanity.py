"""Sanity evidence for a corpus task (PLAN §5).

The rule is mechanical: the task's own checks pass on ``base_commit`` (a no-op
baseline must not flake), the held-out suite fails there, and both pass once
the reference fix is applied. ``scripts/corpus_sanity.py`` produces the
evidence, this module defines it and decides whether it is acceptable.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

SANITY_FILENAME = "sanity.json"


class SanityError(ValueError):
    """A sanity record could not be read."""


class StageResult(BaseModel):
    """One command run at one commit."""

    model_config = ConfigDict(extra="forbid")

    exit_code: int
    summary: str


class SanityResult(BaseModel):
    """What the sanity runner observed, kept next to the held-out bundle."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    checked_at: dt.datetime
    python_version: str
    tool_versions: dict[str, str]
    repo: str
    base_commit: str
    fix_commit: str | None
    checks: tuple[str, ...]
    heldout_command: str
    base_checks: StageResult
    base_heldout: StageResult
    fix_checks: StageResult
    fix_heldout: StageResult

    @property
    def ok(self) -> bool:
        """True when the task behaves exactly as PLAN §5 requires."""

        return (
            self.base_checks.exit_code == 0
            and self.base_heldout.exit_code != 0
            and self.fix_checks.exit_code == 0
            and self.fix_heldout.exit_code == 0
        )

    def problems(self) -> list[str]:
        """Human-readable failures, empty when :attr:`ok` is true."""

        issues: list[str] = []
        if self.base_checks.exit_code != 0:
            issues.append(f"checks fail on base_commit: {self.base_checks.summary}")
        if self.base_heldout.exit_code == 0:
            issues.append("held-out suite passes on base_commit, so it does not cover the defect")
        if self.fix_checks.exit_code != 0:
            issues.append(f"checks fail with the reference fix: {self.fix_checks.summary}")
        if self.fix_heldout.exit_code != 0:
            issues.append(
                f"held-out suite fails with the reference fix: {self.fix_heldout.summary}"
            )
        return issues


def read_sanity(path: Path) -> SanityResult:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SanityError(f"{path}: cannot read file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SanityError(f"{path}: invalid JSON: {exc}") from exc
    try:
        return SanityResult.model_validate(raw)
    except ValidationError as exc:
        parts = [
            f"{'.'.join(str(i) for i in error['loc'])}: {error['msg']}" for error in exc.errors()
        ]
        raise SanityError(f"{path}: {'; '.join(parts)}") from exc


def write_sanity(path: Path, result: SanityResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
