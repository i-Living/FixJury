"""Capturing the agent's work as a unified diff (PLAN §7, invariant 1).

The diff is the only transport between an agent and verification. Two rules make
it trustworthy: untracked files must be staged intent-to-add first, otherwise
``git diff`` silently omits a new module the agent wrote; and binary content must
be refused explicitly instead of disappearing into an unreadable patch.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

BINARY_MARKER = "GIT binary patch"


class DiffError(RuntimeError):
    """The workspace cannot be turned into a diff."""


class UnreadableDiff(DiffError):
    """The diff contains content verification cannot apply, such as a binary patch."""


@dataclass(frozen=True)
class CapturedDiff:
    """A diff plus the digest recorded on the run."""

    diff: str
    sha256: str
    files: tuple[str, ...]

    @property
    def is_empty(self) -> bool:
        return not self.diff.strip()


def _git() -> str:
    found = shutil.which("git")
    if found is None:
        raise DiffError("git is not installed")
    return found


def _run(workdir: Path, argv: list[str]) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [_git(), *argv],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[:3]
        raise DiffError(f"git {' '.join(argv)} failed: {' '.join(detail)}")
    return completed.stdout


def changed_files(workdir: Path) -> tuple[str, ...]:
    """Return the workspace paths that differ from ``HEAD``, including new ones."""

    output = _run(workdir, ["status", "--porcelain", "--untracked-files=all"])
    paths: list[str] = []
    for line in output.splitlines():
        entry = line[3:].strip()
        if not entry:
            continue
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        paths.append(entry.strip('"'))
    return tuple(sorted(paths))


def capture_diff(workdir: Path, *, allow_binary: bool = False) -> CapturedDiff:
    """Capture the workspace as a diff against ``HEAD``.

    ``allow_binary`` exists for callers that only need the digest; the repair
    pipeline never sets it, because a binary patch cannot be applied and
    reviewed reliably.
    """

    if not workdir.is_dir():
        raise DiffError(f"workspace {workdir} is not a directory")

    _run(workdir, ["add", "--intent-to-add", "--all"])
    diff = _run(workdir, ["diff", "--binary", "--no-color", "--no-ext-diff"])

    if BINARY_MARKER in diff and not allow_binary:
        raise UnreadableDiff("diff contains a binary patch; the run is rejected as UNREADABLE_DIFF")

    digest = hashlib.sha256(diff.encode("utf-8")).hexdigest()
    return CapturedDiff(diff=diff, sha256=digest, files=changed_files(workdir))
