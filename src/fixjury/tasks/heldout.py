"""Held-out test bundles (PLAN §5, task 1.5).

A bundle is a directory ``benchmarks/heldout/<task-id>/`` with:

* ``files/<repo-relative path>`` — the post-fix copy of every test file the
  upstream fix touched. Verification copies these over the checkout, so an
  agent patching the same test file cannot change what the held-out suite does;
* ``heldout.yaml`` — the pytest command that runs them plus the list of files;
* ``reference.patch`` — the reference fix, required only for tasks without an
  upstream ``fix_commit``.

A bundle is never mounted into an agent container: it belongs to verification
only, and the corpus validator refuses any check or allowed path that points
into ``benchmarks/``.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from fixjury.tasks.loader import TaskFileError
from fixjury.tasks.schema import CheckTool, parse_check

HELDOUT_FILENAME = "heldout.yaml"
HELDOUT_FILES_DIRNAME = "files"
REFERENCE_PATCH = "reference.patch"


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
            raise ValueError("heldout.yaml must list the test files the bundle provides")
        for item in value:
            if item.startswith("/") or ".." in item.split("/") or "\\" in item:
                raise ValueError(f"held-out file must be a relative path: {item!r}")
            if item.startswith("benchmarks/"):
                raise ValueError(f"held-out file must be a repository path: {item!r}")
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


def heldout_files(directory: Path, spec: HeldoutSpec) -> tuple[Path, ...]:
    """Overlay files of a bundle, in the order ``heldout.yaml`` lists them."""

    return tuple(directory / HELDOUT_FILES_DIRNAME / item for item in spec.files)


def validate_heldout(directory: Path) -> list[str]:
    """Check one held-out bundle; returns human-readable problems."""

    if not directory.is_dir():
        return [f"held-out directory {directory} does not exist"]

    try:
        spec = read_heldout(directory)
    except TaskFileError as exc:
        return [str(exc)]

    problems: list[str] = []
    for item, path in zip(spec.files, heldout_files(directory, spec), strict=True):
        if not path.is_file():
            problems.append(f"held-out bundle has no files/{item}")
        elif not path.read_text(encoding="utf-8").strip():
            problems.append(f"held-out file files/{item} is empty")

    command = parse_check(spec.command)
    unknown = sorted(set(command.args) - set(spec.files))
    if unknown:
        problems.append(
            f"held-out command runs files the bundle does not provide: {', '.join(unknown)}"
        )
    return problems
