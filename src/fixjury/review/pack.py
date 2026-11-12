"""Blind review packaging (PLAN §11, task 6.1).

A batch is the runs of **one task** by several agents, in a stored random order.
The reviewer sees the task, the patch and the check results; the agent name, the
model, the cost and the timing are removed before the page is rendered, and the
order is stored in ``review_batches.order_seed`` so blindness is auditable.

Only a patch with ``automated_pass = true`` reaches a human (PLAN §3.1), so a
batch is a comparison of plausible fixes, not of everything that ran.
"""

from __future__ import annotations

import hashlib
import random
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fixjury.models import ReviewBatch, Run, Verdict
from fixjury.tasks.schema import TaskDocument

MIN_RUNS_PER_BATCH = 2

_INDEX_LINE = re.compile(r"^index [0-9a-f]+\.\.[0-9a-f]+.*$", re.MULTILINE)
_MAIL_HEADER = re.compile(r"^(From[: ]|Author:|Date:|Subject:|Commit:).*$", re.MULTILINE)


class PackError(RuntimeError):
    """Batches cannot be packed as asked."""


@dataclass(frozen=True)
class BlindItem:
    """What a reviewer is allowed to see for one run."""

    run_id: uuid.UUID
    position: int
    patch: str
    checks: dict[str, bool | None]


def seed_for(task_id: str, pass_no: int, base_seed: int) -> int:
    """A per-task seed, so one task's order does not depend on another's."""

    digest = hashlib.sha256(f"{task_id}|{pass_no}|{base_seed}".encode()).hexdigest()
    return int(digest[:12], 16)


def normalise_diff(diff: str) -> str:
    """Strip what identifies the author or the object ids, keep the patch."""

    cleaned = _MAIL_HEADER.sub("", diff)
    cleaned = _INDEX_LINE.sub("", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip() + "\n"


def blind_item(run: Run, verdict: Verdict | None, position: int) -> BlindItem:
    """Build the reviewer's view: no agent, no model, no cost, no timing."""

    return BlindItem(
        run_id=run.id,
        position=position,
        patch=normalise_diff(run.diff or ""),
        checks={
            "tests": None if verdict is None else verdict.tests_pass,
            "lint": None if verdict is None else verdict.lint_pass,
            "types": None if verdict is None else verdict.type_pass,
            "policy": None if verdict is None else verdict.policy_pass,
            "held-out": None if verdict is None else verdict.heldout_pass,
        },
    )


def batch_id_for(task_id: str, pass_no: int, seed: int) -> str:
    return f"{task_id}-p{pass_no}-{seed:012x}"


async def pack_batches(
    session: AsyncSession,
    *,
    reviewer: str,
    pass_no: int = 1,
    seed: int = 0,
    tasks: Sequence[str] | None = None,
    min_runs: int = MIN_RUNS_PER_BATCH,
) -> list[ReviewBatch]:
    """Create one batch per task that has enough acceptable patches."""

    statement = (
        select(Run, Verdict)
        .join(Verdict, Verdict.run_id == Run.id)
        .where(Verdict.automated_pass.is_(True))
        .where(Verdict.human_verdict.is_(None))
    )
    if tasks:
        statement = statement.where(Run.task_id.in_(tasks))
    rows = list((await session.execute(statement)).all())

    by_task: dict[str, list[uuid.UUID]] = {}
    for run, _verdict in rows:
        by_task.setdefault(run.task_id, []).append(run.id)

    created: list[ReviewBatch] = []
    for task_id, run_ids in sorted(by_task.items()):
        if len(run_ids) < min_runs:
            continue
        order_seed = seed_for(task_id, pass_no, seed)
        shuffled = list(run_ids)
        # Deterministic by design: the order is stored, not regenerated.
        random.Random(order_seed).shuffle(shuffled)  # noqa: S311
        identifier = batch_id_for(task_id, pass_no, order_seed)
        existing = await session.get(ReviewBatch, identifier)
        if existing is not None:
            created.append(existing)
            continue
        batch = ReviewBatch(
            id=identifier,
            task_id=task_id,
            run_ids=shuffled,
            order_seed=order_seed,
            pass_no=pass_no,
            reviewer=reviewer,
        )
        session.add(batch)
        await session.flush()
        created.append(batch)
    return created


def ordered_items(
    batch: ReviewBatch,
    runs: dict[uuid.UUID, Run],
    verdicts: dict[uuid.UUID, Verdict],
) -> list[BlindItem]:
    """The batch in its stored order, blinded."""

    items: list[BlindItem] = []
    for position, run_id in enumerate(batch.run_ids):
        run = runs.get(run_id)
        if run is None:
            raise PackError(f"batch {batch.id} references a missing run {run_id}")
        items.append(blind_item(run, verdicts.get(run_id), position))
    return items


def rubric(document: TaskDocument) -> dict[str, tuple[str, ...]]:
    """What the reviewer is asked to judge, taken from the task itself."""

    return {
        "accept when": (
            "the patch addresses the reported behaviour, not a symptom of it",
            "the change stays inside the paths the task allows",
            "the tests it adds or keeps would fail on the base commit",
        ),
        "reject when": (
            "the behaviour in the issue still reproduces",
            "the fix is unrelated to the reported defect",
            "the patch weakens or removes an existing test",
        ),
        "acceptance criteria": document.acceptance,
    }
