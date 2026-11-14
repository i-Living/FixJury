"""Publication export: raw runs, verdicts and rejections as JSONL (PLAN §5).

The published numbers must be reproducible from the data, so the data is what
gets published: one JSON object per row, the same rows the database holds, and a
manifest carrying the sha256 of each file. The manifest is what makes the export
checkable — a reader can hash the file and see it is the copy the database
produced, not one edited by hand afterwards.

Determinism is part of the contract: the rows are ordered, keys are sorted and
the encoding is fixed, so exporting the same database twice produces the same
bytes. A publication that changes between two exports of one database cannot be
used as evidence.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import uuid
from collections.abc import Iterable, Sequence
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fixjury.models import Rejection, Run, Verdict

RUNS_FILENAME = "runs.jsonl"
VERDICTS_FILENAME = "verdicts.jsonl"
REJECTIONS_FILENAME = "rejections.jsonl"
MANIFEST_FILENAME = "manifest.json"


class ExportError(RuntimeError):
    """The export is not the copy of the database it claims to be."""


def _json_default(value: object) -> str:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, (Decimal, uuid.UUID)):
        return str(value)
    raise TypeError(f"{type(value).__name__} is not serialisable in an export")


def record_line(record: dict[str, object]) -> str:
    """One JSON object, keys sorted, no whitespace, no ASCII escaping."""

    return json.dumps(
        record,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    )


def run_record(run: Run) -> dict[str, object]:
    """Every column of ``runs``, including the diff: it is the evidence."""

    return {
        "id": run.id,
        "task_id": run.task_id,
        "agent": run.agent,
        "agent_version": run.agent_version,
        "model": run.model,
        "attempt": run.attempt,
        "parent_run_id": run.parent_run_id,
        "status": run.status.value,
        "declared_success": run.declared_success,
        "unable_reason": run.unable_reason,
        "diff": run.diff,
        "diff_sha256": run.diff_sha256,
        "tokens_in": run.tokens_in,
        "tokens_out": run.tokens_out,
        "sandbox_seconds": run.sandbox_seconds,
        "cost_list_usd": run.cost_list_usd,
        "pricing_version": run.pricing_version,
        "config_hash": run.config_hash,
        "trace_id": run.trace_id,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


def verdict_record(verdict: Verdict) -> dict[str, object]:
    """Every column of ``verdicts``, human fields included."""

    return {
        "run_id": verdict.run_id,
        "tests_pass": verdict.tests_pass,
        "lint_pass": verdict.lint_pass,
        "type_pass": verdict.type_pass,
        "policy_pass": verdict.policy_pass,
        "heldout_pass": verdict.heldout_pass,
        "automated_pass": verdict.automated_pass,
        "tool_versions": verdict.tool_versions,
        "reviewer_verdict": (
            None if verdict.reviewer_verdict is None else verdict.reviewer_verdict.value
        ),
        "reviewer_reasons": list(verdict.reviewer_reasons),
        "reviewer_model": verdict.reviewer_model,
        "human_verdict": None if verdict.human_verdict is None else verdict.human_verdict.value,
        "human_reasons": list(verdict.human_reasons),
        "human_resolution": (
            None if verdict.human_resolution is None else verdict.human_resolution.value
        ),
        "human_reviewer": verdict.human_reviewer,
        "review_seconds": verdict.review_seconds,
        "blind": verdict.blind,
        "batch_id": verdict.batch_id,
        "reviewed_at": verdict.reviewed_at,
    }


def rejection_record(rejection: Rejection) -> dict[str, object]:
    """Every column of ``rejections``; ``code`` is the taxonomy code."""

    return {
        "id": rejection.id,
        "run_id": rejection.run_id,
        "source": rejection.source.value,
        "code": rejection.code.value,
        "note": rejection.note,
    }


def write_jsonl(path: Path, records: Iterable[dict[str, object]]) -> tuple[int, str]:
    """Write the records and return ``(rows, sha256 of the bytes written)``."""

    digest = hashlib.sha256()
    rows = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            line = record_line(record) + "\n"
            handle.write(line)
            digest.update(line.encode("utf-8"))
            rows += 1
    return rows, digest.hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_file(path: Path, expected_rows: int, expected_digest: str) -> None:
    with path.open("rb") as handle:
        counted = sum(1 for _ in handle)
    if counted != expected_rows:
        raise ExportError(
            f"{path.name} holds {counted} rows, the database returned {expected_rows}"
        )
    if file_sha256(path) != expected_digest:
        raise ExportError(f"{path.name} on disk is not the bytes the export wrote")


async def _rows(session: AsyncSession) -> tuple[list[Run], list[Verdict], list[Rejection]]:
    runs = list(
        (
            await session.scalars(select(Run).order_by(Run.task_id, Run.agent, Run.attempt, Run.id))
        ).all()
    )
    verdicts = list((await session.scalars(select(Verdict).order_by(Verdict.run_id))).all())
    rejections = list(
        (await session.scalars(select(Rejection).order_by(Rejection.run_id, Rejection.id))).all()
    )
    return runs, verdicts, rejections


async def export_rows(session: AsyncSession, out_dir: Path) -> dict[str, object]:
    """Write the three JSONL files and a manifest, and verify what landed.

    The verification is the point of the task this implements (PLAN §11, 5.3):
    the export must reproduce the database, so every file is checked against the
    row count of its table and hashed again from disk before the manifest says
    ``verified``.
    """

    runs, verdicts, rejections = await _rows(session)
    expected = {
        RUNS_FILENAME: await _count(session, Run),
        VERDICTS_FILENAME: await _count(session, Verdict),
        REJECTIONS_FILENAME: await _count(session, Rejection),
    }
    plans: Sequence[tuple[str, Iterable[dict[str, object]]]] = (
        (RUNS_FILENAME, (run_record(run) for run in runs)),
        (VERDICTS_FILENAME, (verdict_record(verdict) for verdict in verdicts)),
        (REJECTIONS_FILENAME, (rejection_record(rejection) for rejection in rejections)),
    )

    def write() -> dict[str, object]:
        out_dir.mkdir(parents=True, exist_ok=True)
        written: dict[str, dict[str, object]] = {}
        for filename, records in plans:
            path = out_dir / filename
            rows, digest = write_jsonl(path, records)
            if rows != expected[filename]:
                raise ExportError(
                    f"{filename} wrote {rows} rows, the database holds {expected[filename]}"
                )
            _verify_file(path, rows, digest)
            written[filename] = {"rows": rows, "sha256": digest}
        manifest: dict[str, object] = {
            "exported_at": dt.datetime.now(dt.UTC).isoformat(),
            "files": written,
            "verified": True,
        }
        (out_dir / MANIFEST_FILENAME).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return manifest

    return await asyncio.to_thread(write)


async def _count(session: AsyncSession, table: type[Run] | type[Verdict] | type[Rejection]) -> int:
    counted = await session.scalar(select(func.count()).select_from(table))
    return int(counted or 0)
