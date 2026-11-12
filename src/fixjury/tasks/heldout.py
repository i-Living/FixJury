"""Held-out test bundles (PLAN §5, task 1.5).

A bundle is a directory ``benchmarks/heldout/<task-id>/`` with:

* ``heldout.patch`` — a test-only diff that *adds* the regression tests;
* ``heldout.yaml`` — the pytest command that runs them plus the list of files
  the patch creates;
* ``reference.patch`` — the reference fix, required only for tasks without an
  upstream ``fix_commit``.

The patch is applied to the verification checkout only. The bundle never
reaches an agent container, and the corpus validator refuses any check or
allowed path that points into ``benchmarks/``.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from fixjury.tasks.loader import TaskFileError
from fixjury.tasks.schema import CheckTool, parse_check

HELDOUT_FILENAME = "heldout.yaml"
HELDOUT_PATCH = "heldout.patch"
REFERENCE_PATCH = "reference.patch"

_DIFF_HEADER = re.compile(r"^diff --git a/(?P<old>.+) b/(?P<new>.+)$")


class HeldoutSpec(BaseModel):
    """``heldout.yaml`` — how to run the held-out suite."""

    model_config = ConfigDict(extra="forbid")

    command: str
    files: tuple[str, ...]
    note: str | None = None

    @field_validator("command")
    @classmethod
    def _pytest_only(cls, value: str) -> str:
        parsed = parse_check(value)
        if parsed.tool is not CheckTool.pytest:
            raise ValueError("held-out suite must run with pytest")
        return value

    @field_validator("files")
    @classmethod
    def _relative_python_files(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("heldout.yaml must list the files the patch creates")
        for item in value:
            if item.startswith("/") or ".." in item.split("/") or "\\" in item:
                raise ValueError(f"held-out file must be a relative path: {item!r}")
            if not item.endswith(".py"):
                raise ValueError(f"held-out file must be a Python file: {item!r}")
        return value


def read_heldout(directory: Path) -> HeldoutSpec:
    path = directory / HELDOUT_FILENAME
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TaskFileError(f"{path}: cannot read file: {exc}") from exc
    except yaml.YAMLError as exc:
        raise TaskFileError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise TaskFileError(f"{path}: held-out file must contain a mapping")
    try:
        return HeldoutSpec.model_validate(raw)
    except ValidationError as exc:
        parts = [
            f"{'.'.join(str(i) for i in error['loc'])}: {error['msg']}" for error in exc.errors()
        ]
        raise TaskFileError(f"{path}: {'; '.join(parts)}") from exc


def patch_created_files(patch: str) -> tuple[str, ...]:
    """Return the repository-relative paths a unified diff creates."""

    created: list[str] = []
    current: str | None = None
    is_new = False
    for line in patch.splitlines():
        header = _DIFF_HEADER.match(line)
        if header is not None:
            if current is not None and is_new:
                created.append(current)
            current = header.group("new")
            is_new = False
            continue
        if current is None:
            continue
        if line.startswith("new file mode") or line.startswith("--- /dev/null"):
            is_new = True
    if current is not None and is_new:
        created.append(current)
    return tuple(created)


def validate_heldout(directory: Path) -> list[str]:
    """Check one held-out bundle; returns human-readable problems."""

    problems: list[str] = []
    if not directory.is_dir():
        return [f"held-out directory {directory} does not exist"]

    patch_path = directory / HELDOUT_PATCH
    try:
        patch = patch_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{patch_path}: cannot read file: {exc}"]

    if "GIT binary patch" in patch or "Binary files" in patch:
        problems.append(f"{patch_path}: held-out patch must be textual")
    created = patch_created_files(patch)
    if not created:
        problems.append(f"{patch_path}: patch does not add any file")

    try:
        spec = read_heldout(directory)
    except TaskFileError as exc:
        problems.append(str(exc))
        return problems

    if set(spec.files) != set(created):
        missing = sorted(set(created) - set(spec.files))
        extra = sorted(set(spec.files) - set(created))
        if missing:
            problems.append(f"heldout.yaml misses files created by the patch: {', '.join(missing)}")
        if extra:
            problems.append(
                f"heldout.yaml lists files the patch does not create: {', '.join(extra)}"
            )

    command = parse_check(spec.command)
    unknown = sorted(set(command.args) - set(spec.files))
    if unknown:
        problems.append(
            f"held-out command runs files the patch does not create: {', '.join(unknown)}"
        )
    return problems
