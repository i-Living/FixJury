"""Reading and writing ``benchmarks/`` files: tasks, held-out specs, index."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from fixjury.spec.render import render_task
from fixjury.tasks.schema import TaskDocument

TASKS_DIRNAME = "tasks"
HELDOUT_DIRNAME = "heldout"
INDEX_FILENAME = "index.json"
TASK_FILENAME = "task.yaml"


class TaskFileError(ValueError):
    """A task or index file could not be read."""


class CorpusIndex(BaseModel):
    """``benchmarks/index.json`` — the corpus manifest (PLAN §8)."""

    model_config = ConfigDict(extra="forbid")

    corpus_version: str
    frozen_at: dt.date
    tasks: tuple[str, ...]

    @field_validator("tasks")
    @classmethod
    def _no_duplicates(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            duplicates = sorted({task for task in value if value.count(task) > 1})
            raise ValueError(f"duplicate task ids: {', '.join(duplicates)}")
        return value


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TaskFileError(f"{path}: cannot read file: {exc}") from exc
    except yaml.YAMLError as exc:
        raise TaskFileError(f"{path}: invalid YAML: {exc}") from exc


def read_task(path: Path) -> TaskDocument:
    """Parse and syntax-check one ``task.yaml``."""

    raw = _read_yaml(path)
    if not isinstance(raw, dict):
        raise TaskFileError(f"{path}: task file must contain a mapping")
    try:
        return TaskDocument.model_validate(raw)
    except ValidationError as exc:
        raise TaskFileError(f"{path}: {_format_validation_error(exc)}") from exc


def write_task(path: Path, document: TaskDocument) -> None:
    """Write a task document in canonical form, creating parent directories."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_task(document), encoding="utf-8")


def task_path(corpus_root: Path, task_id: str) -> Path:
    return corpus_root / TASKS_DIRNAME / task_id / TASK_FILENAME


def heldout_path(corpus_root: Path, task_id: str) -> Path:
    return corpus_root / HELDOUT_DIRNAME / task_id


def discover_task_paths(corpus_root: Path) -> list[Path]:
    """Every ``tasks/*/task.yaml`` under a corpus root, sorted by task id."""

    tasks_root = corpus_root / TASKS_DIRNAME
    if not tasks_root.is_dir():
        return []
    return sorted(tasks_root.glob(f"*/{TASK_FILENAME}"))


def read_index(corpus_root: Path) -> CorpusIndex:
    path = corpus_root / INDEX_FILENAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TaskFileError(f"{path}: cannot read file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise TaskFileError(f"{path}: invalid JSON: {exc}") from exc
    try:
        return CorpusIndex.model_validate(raw)
    except ValidationError as exc:
        raise TaskFileError(f"{path}: {_format_validation_error(exc)}") from exc


def write_index(corpus_root: Path, index: CorpusIndex) -> None:
    corpus_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "corpus_version": index.corpus_version,
        "frozen_at": index.frozen_at.isoformat(),
        "tasks": list(index.tasks),
    }
    (corpus_root / INDEX_FILENAME).write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error["loc"])
        message = error["msg"]
        parts.append(f"{location}: {message}" if location else message)
    return "; ".join(parts)
