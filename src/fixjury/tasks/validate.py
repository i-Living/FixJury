"""Corpus policy validation (PLAN §5, §8).

``schema.py`` answers "is this document well formed"; this module answers "may
it enter the frozen corpus": cut-off dates, held-out presence, index agreement
and the rule that no check or allowed path may point into ``benchmarks/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fixjury.tasks.heldout import HELDOUT_PATCH, REFERENCE_PATCH, validate_heldout
from fixjury.tasks.loader import (
    TaskFileError,
    discover_task_paths,
    heldout_path,
    read_index,
    read_task,
)
from fixjury.tasks.schema import CUTOFF_DATE, TaskDocument, parse_check


@dataclass(frozen=True)
class Finding:
    """One corpus problem, located by task id or by file."""

    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.where}: {self.message}"


def _points_into_benchmarks(path: str) -> bool:
    return path == "benchmarks" or path.startswith("benchmarks/")


def validate_task(document: TaskDocument, *, corpus_root: Path) -> list[Finding]:
    """Check one task against every corpus rule that needs its files."""

    where = f"tasks/{document.id}"
    findings: list[Finding] = []

    if not document.frozen:
        findings.append(Finding(where, "task is not frozen: frozen_at and frozen_by must be set"))
    if document.license is None:
        findings.append(Finding(where, "license is missing"))
    if document.base_commit is None:
        findings.append(Finding(where, "base_commit is missing"))
    if document.issue.created_at is None:
        findings.append(Finding(where, "issue.created_at is missing"))
    elif document.issue.created_at < CUTOFF_DATE:
        findings.append(
            Finding(
                where,
                f"issue was created on {document.issue.created_at}, before the "
                f"{CUTOFF_DATE.isoformat()} contamination cut-off",
            )
        )
    if document.issue.body is None:
        findings.append(Finding(where, "issue body snapshot is missing"))
    if document.cutoff_assumption.value != "clean":
        findings.append(Finding(where, "cutoff_assumption must be 'clean' for a corpus task"))

    if document.fix_commit is None:
        if document.issue.fix_merged_at is not None:
            findings.append(Finding(where, "fix_merged_at is set but fix_commit is not"))
        if not (heldout_path(corpus_root, document.id) / REFERENCE_PATCH).is_file():
            findings.append(
                Finding(
                    where, f"task has no fix_commit and no {REFERENCE_PATCH} in its held-out bundle"
                )
            )
    else:
        if document.issue.fix_merged_at is None:
            findings.append(Finding(where, "fix_merged_at is missing while fix_commit is set"))
        elif document.issue.fix_merged_at < CUTOFF_DATE:
            findings.append(
                Finding(
                    where,
                    f"fix was merged on {document.issue.fix_merged_at}, before the "
                    f"{CUTOFF_DATE.isoformat()} contamination cut-off",
                )
            )

    if not document.acceptance:
        findings.append(Finding(where, "acceptance criteria are empty"))

    if not document.checks:
        findings.append(Finding(where, "checks are empty"))
    for raw in document.checks:
        for argument in parse_check(raw).args:
            if _points_into_benchmarks(argument):
                findings.append(Finding(where, f"check {raw!r} points into benchmarks/"))

    if not document.allowed_paths:
        findings.append(Finding(where, "allowed_paths is empty"))
    for path in document.allowed_paths:
        if _points_into_benchmarks(path):
            findings.append(Finding(where, f"allowed path {path!r} points into benchmarks/"))

    expected_ref = f"benchmarks/heldout/{document.id}"
    if document.heldout_ref is None:
        findings.append(Finding(where, "heldout_ref is missing"))
    elif document.heldout_ref != expected_ref:
        findings.append(
            Finding(where, f"heldout_ref must be {expected_ref!r}, found {document.heldout_ref!r}")
        )

    bundle = heldout_path(corpus_root, document.id)
    if not (bundle / HELDOUT_PATCH).is_file():
        findings.append(Finding(where, f"held-out bundle has no {HELDOUT_PATCH}"))
    for problem in validate_heldout(bundle):
        findings.append(Finding(where, problem))

    return findings


def validate_corpus(corpus_root: Path, *, min_tasks: int = 0) -> list[Finding]:
    """Validate every task plus the corpus index; returns all findings."""

    findings: list[Finding] = []

    try:
        index = read_index(corpus_root)
    except TaskFileError as exc:
        findings.append(Finding("index.json", str(exc)))
        index = None
    if index is not None and not index.corpus_version.strip():
        findings.append(Finding("index.json", "corpus_version must not be empty"))

    paths = discover_task_paths(corpus_root)
    if not paths:
        findings.append(Finding("tasks/", "no task.yaml files found"))
    if len(paths) < min_tasks:
        findings.append(
            Finding("tasks/", f"corpus has {len(paths)} tasks, expected at least {min_tasks}")
        )

    seen: dict[str, Path] = {}
    frozen_dates = []
    for path in paths:
        try:
            document = read_task(path)
        except TaskFileError as exc:
            findings.append(Finding(str(path.relative_to(corpus_root)), str(exc)))
            continue

        if path.parent.name != document.id:
            findings.append(
                Finding(
                    str(path.relative_to(corpus_root)),
                    f"directory name {path.parent.name!r} does not match task id {document.id!r}",
                )
            )
        if document.id in seen:
            findings.append(
                Finding(
                    f"tasks/{document.id}",
                    f"duplicate task id, also defined in {seen[document.id]}",
                )
            )
        else:
            seen[document.id] = path

        if document.frozen_at is not None:
            frozen_dates.append(document.frozen_at.date())
        findings.extend(validate_task(document, corpus_root=corpus_root))

    if index is not None:
        indexed = set(index.tasks)
        present = set(seen)
        missing = sorted(indexed - present)
        extra = sorted(present - indexed)
        if missing:
            findings.append(
                Finding("index.json", f"lists tasks without a file: {', '.join(missing)}")
            )
        if extra:
            findings.append(
                Finding("index.json", f"does not list tasks on disk: {', '.join(extra)}")
            )
        if list(index.tasks) != sorted(index.tasks):
            findings.append(Finding("index.json", "tasks must be sorted by id"))
        if frozen_dates and index.frozen_at < max(frozen_dates):
            findings.append(
                Finding(
                    "index.json",
                    f"frozen_at {index.frozen_at.isoformat()} predates the newest task "
                    f"freeze {max(frozen_dates).isoformat()}",
                )
            )

    return findings
