"""Policy checks on the agent's diff (PLAN §11, task 3.3).

Pure functions over the unified diff: no docker, no database, no file system.
That is deliberate — every rule here has a negative fixture in the unit tests, and
a rule that needs a container to evaluate could not be tested that way.

A policy failure is not a bad *verdict*, it is a rejection with a code, so the
report can say why an agent's patch never reached a human.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fixjury.models import RejectionCode

MAX_CHANGED_FILES = 10

PROTECTED_PREFIXES = (".github/", ".circleci/", ".gitlab/", "ci/")
PROTECTED_FILES = frozenset(
    {
        ".gitlab-ci.yml",
        ".travis.yml",
        "tox.ini",
        "noxfile.py",
        "setup.py",
        "setup.cfg",
        "pyproject.toml",
        "pytest.ini",
        "mypy.ini",
        ".flake8",
        "uv.lock",
        "poetry.lock",
        "Pipfile.lock",
        "pdm.lock",
        "requirements.txt",
        "requirements-dev.txt",
    }
)
TEST_PREFIXES = ("tests/", "test/")
TEST_SUFFIXES = ("_test.py",)
TEST_NAMES = ("conftest.py", "test_main.py")

_DIFF_HEADER = re.compile(r"^diff --git a/(?P<old>.+?) b/(?P<new>.+)$")
_RENAME_FROM = re.compile(r"^rename from (?P<path>.+)$")
_RENAME_TO = re.compile(r"^rename to (?P<path>.+)$")


@dataclass(frozen=True)
class ChangedFile:
    """One file the diff touches."""

    path: str
    added: int
    deleted: int
    is_new: bool = False
    is_deleted: bool = False
    is_renamed: bool = False
    original_path: str | None = None


@dataclass(frozen=True)
class Violation:
    """One rule the diff breaks."""

    code: RejectionCode
    note: str


@dataclass(frozen=True)
class PolicyResult:
    passed: bool
    violations: tuple[Violation, ...]
    files: tuple[ChangedFile, ...]

    @property
    def codes(self) -> tuple[RejectionCode, ...]:
        return tuple(violation.code for violation in self.violations)


def is_test_path(path: str) -> bool:
    """A path that holds tests, wherever the repository keeps them."""

    lowered = path.lower()
    name = lowered.rsplit("/", 1)[-1]
    if name.startswith("test_") or name.endswith(TEST_SUFFIXES):
        return True
    if name in TEST_NAMES:
        return True
    return any(f"/{prefix}" in f"/{lowered}" for prefix in TEST_PREFIXES)


def in_test_tree(path: str) -> bool:
    """Whether the path lives in a test directory, whatever the file is called."""

    parts = path.lower().split("/")[:-1]
    return any(part in {"tests", "test"} for part in parts)


def is_protected_path(path: str) -> bool:
    """CI, tooling configuration and lock files: never the agent's to edit."""

    if path in PROTECTED_FILES:
        return True
    return any(path.startswith(prefix) for prefix in PROTECTED_PREFIXES)


@dataclass
class _Builder:
    """Mutable accumulator for one file while its diff lines are read."""

    path: str
    added: int = 0
    deleted: int = 0
    is_new: bool = False
    is_deleted: bool = False
    is_renamed: bool = False
    original_path: str | None = None

    def build(self) -> ChangedFile:
        return ChangedFile(
            path=self.path,
            added=self.added,
            deleted=self.deleted,
            is_new=self.is_new,
            is_deleted=self.is_deleted,
            is_renamed=self.is_renamed,
            original_path=self.original_path,
        )


def parse_changed_files(diff: str) -> tuple[ChangedFile, ...]:
    """Read ``git diff`` output into one record per touched path."""

    files: list[ChangedFile] = []
    current: _Builder | None = None
    for line in diff.splitlines():
        header = _DIFF_HEADER.match(line)
        if header is not None:
            if current is not None:
                files.append(current.build())
            current = _Builder(path=header.group("new"))
            continue
        if current is None:
            continue
        if line.startswith("new file mode"):
            current.is_new = True
        elif line.startswith("deleted file mode"):
            current.is_deleted = True
        elif (renamed_from := _RENAME_FROM.match(line)) is not None:
            current.is_renamed = True
            current.original_path = renamed_from.group("path")
        elif (renamed_to := _RENAME_TO.match(line)) is not None:
            current.is_renamed = True
            current.path = renamed_to.group("path")
        elif line.startswith("+") and not line.startswith("+++"):
            current.added += 1
        elif line.startswith("-") and not line.startswith("---"):
            current.deleted += 1
    if current is not None:
        files.append(current.build())
    return tuple(files)


def _inside(path: str, allowed: tuple[str, ...]) -> bool:
    for entry in allowed:
        if entry in {"", ".", "./"}:
            return True
        prefix = entry.rstrip("/")
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def check_policy(
    diff: str,
    *,
    allowed_paths: tuple[str, ...],
    forbidden: tuple[str, ...] = (),
    max_files: int = MAX_CHANGED_FILES,
) -> PolicyResult:
    """Every rule from PLAN §11 task 3.3, in one pass over the diff."""

    files = parse_changed_files(diff)
    violations: list[Violation] = []

    if not files:
        violations.append(Violation(RejectionCode.INCOMPLETE_FIX, "the diff is empty"))

    if len(files) > max_files:
        violations.append(
            Violation(
                RejectionCode.EXTRA_FILES,
                f"{len(files)} files changed, the limit is {max_files}",
            )
        )

    for changed in files:
        if allowed_paths and not _inside(changed.path, allowed_paths):
            violations.append(
                Violation(
                    RejectionCode.EXTRA_FILES,
                    f"{changed.path} is outside allowed_paths",
                )
            )
        if is_protected_path(changed.path):
            violations.append(
                Violation(
                    RejectionCode.SPEC_VIOLATION,
                    f"{changed.path} is CI, configuration or a lock file",
                )
            )
        if _inside(changed.path, forbidden):
            violations.append(
                Violation(RejectionCode.SPEC_VIOLATION, f"{changed.path} is listed as forbidden")
            )
        if changed.is_deleted and is_test_path(changed.path):
            violations.append(
                Violation(RejectionCode.SPEC_VIOLATION, f"test file {changed.path} was deleted")
            )
        if (
            changed.is_renamed
            and changed.original_path is not None
            and in_test_tree(changed.original_path)
            and not in_test_tree(changed.path)
        ):
            violations.append(
                Violation(
                    RejectionCode.SPEC_VIOLATION,
                    f"test file {changed.original_path} was moved out of the test tree",
                )
            )

    return PolicyResult(passed=not violations, violations=tuple(violations), files=files)
