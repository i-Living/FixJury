"""The bench runner (PLAN §11, tasks 5.1 and 5.2; PLAN_V1.1 §3.4).

One command that turns a task list and an agent list into queued runs, drives the
worker until the queue is empty or the time budget is spent, and optionally
verifies every finished run. It stops on the budget instead of the clock: a
benchmark that overruns is a benchmark whose numbers nobody will trust.

From V1.1 the same loop can judge each verified run. A judge failure is counted and
stored — never turned into a verdict about the patch, and never into a failed run: the
agent finished, the checks ran, and only the opinion is missing.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fixjury import queue
from fixjury.db import SessionMaker, session_scope
from fixjury.judge.service import JUDGE_FAILURES, judge_configuration, record_failure
from fixjury.judge.service import evaluate_run as judge_run
from fixjury.models import Run, RunStatus
from fixjury.run import ensure_task
from fixjury.tasks.loader import read_index, read_task, task_path
from fixjury.verify.service import VerifyError, verify_run
from fixjury.worker import WorkerError, acknowledge, work_once

LOGGER = logging.getLogger(__name__)


class BenchError(RuntimeError):
    """The bench cannot start or cannot continue."""


@dataclass(frozen=True)
class BenchPlan:
    """What to run: every task against every agent."""

    tasks: tuple[str, ...]
    agents: tuple[str, ...]
    attempt: int = 1

    @property
    def pairs(self) -> int:
        return len(self.tasks) * len(self.agents)


@dataclass
class BenchSummary:
    """What actually happened, per agent."""

    plan: BenchPlan
    finished: int = 0
    failed: int = 0
    verified: int = 0
    judged: int = 0
    judge_failed: int = 0
    elapsed_s: float = 0.0
    stopped_on_budget: bool = False
    statuses: dict[str, int] = field(default_factory=dict)

    def record(self, run: Run) -> None:
        key = run.status.value
        self.statuses[key] = self.statuses.get(key, 0) + 1
        if run.status is RunStatus.finished:
            self.finished += 1
        else:
            self.failed += 1


def load_plan(
    *, corpus_root: Path, agents: tuple[str, ...], tasks: tuple[str, ...] = (), attempt: int = 1
) -> BenchPlan:
    """Task ids come from the corpus index unless the caller names them."""

    if tasks:
        chosen = tasks
    else:
        index = read_index(corpus_root)
        chosen = index.tasks
    if not chosen:
        raise BenchError("no tasks selected")
    if not agents:
        raise BenchError("no agents selected")
    for task_id in chosen:
        read_task(task_path(corpus_root, task_id))
    return BenchPlan(tasks=tuple(chosen), agents=tuple(agents), attempt=attempt)


async def enqueue_plan(
    session: AsyncSession, client: queue.RedisList, *, plan: BenchPlan, corpus_root: Path
) -> tuple[list[Run], list[str]]:
    """Queue every pair that has not run yet; report the ones that already have.

    Re-running ``fixjury bench`` after a crash is the resume path, so a pair that
    already finished is skipped rather than refused: refusing would mean a bench
    could never be continued, and duplicating it would add a second attempt to the
    same comparison.
    """

    queued: list[Run] = []
    skipped: list[str] = []
    for task_id in plan.tasks:
        await ensure_task(session, read_task(task_path(corpus_root, task_id)))
        for agent in plan.agents:
            try:
                queued.append(
                    await queue.enqueue(
                        session, client, task_id=task_id, agent=agent, attempt=plan.attempt
                    )
                )
            except queue.QueueError as exc:
                LOGGER.info("skipping %s/%s: %s", task_id, agent, exc)
                skipped.append(f"{task_id}/{agent}")
    return queued, skipped


async def drain(
    maker: SessionMaker,
    client: queue.RedisList,
    *,
    plan: BenchPlan,
    corpus_root: Path,
    image: str | None = None,
    max_seconds: float | None = None,
    verify: bool = False,
    judge: bool = False,
) -> BenchSummary:
    """Work the queue until it is empty, verifying and judging runs when asked."""

    started = time.monotonic()
    summary = BenchSummary(plan=plan)
    while True:
        if max_seconds is not None and time.monotonic() - started >= max_seconds:
            summary.stopped_on_budget = True
            LOGGER.warning("bench stopped on its %.0fs budget", max_seconds)
            break
        # One transaction per run: a crash must not roll back the runs before it.
        async with session_scope(maker) as session:
            try:
                row = await work_once(
                    client, session, corpus_root=corpus_root, image=image, wait_s=1.0
                )
            except WorkerError as exc:
                # One unusable delivery is not a reason to abandon the other pairs.
                LOGGER.warning("dropping a delivery: %s", exc)
                continue
            if row is None:
                break
            # The run has settled in the database: only now may its id leave the queue.
            await acknowledge(client, row.id)
            summary.record(row)
            if verify and row.status is RunStatus.finished:
                try:
                    await verify_run(session, row.id, corpus_root=corpus_root, image=image)
                    summary.verified += 1
                except VerifyError as exc:
                    LOGGER.warning("verification of %s failed: %s", row.id, exc)
                else:
                    if judge:
                        step = await judge_verified_run(session, run=row, corpus_root=corpus_root)
                        if step.judged:
                            summary.judged += 1
                        else:
                            summary.judge_failed += 1
    summary.elapsed_s = round(time.monotonic() - started, 1)
    return summary


@dataclass(frozen=True)
class JudgeStep:
    """What judging one verified run produced: a verdict, or the reason there is none."""

    judged: bool
    error: str | None = None


async def judge_verified_run(session: AsyncSession, *, run: Run, corpus_root: Path) -> JudgeStep:
    """Judge one verified run without letting a judge failure fail the run.

    The evaluation row is written in the same transaction as the run it judges, so a judge
    that fails leaves a `failed` evaluation carrying its reason: the gap is visible in the
    database and the report instead of living in a log line.
    """

    try:
        await judge_run(
            session, run_id=run.id, corpus_root=corpus_root, config=judge_configuration()
        )
        return JudgeStep(judged=True)
    except JUDGE_FAILURES as exc:
        LOGGER.warning("judging %s failed: %s", run.id, exc)
        try:
            await record_failure(session, run_id=run.id, error=str(exc))
        except JUDGE_FAILURES as store_exc:  # pragma: no cover - a missing run row
            LOGGER.error("could not record the judge failure of %s: %s", run.id, store_exc)
        return JudgeStep(judged=False, error=str(exc))


async def pending_runs(session: AsyncSession, *, plan: BenchPlan) -> list[Run]:
    """Every run the plan created, whatever state it is in."""

    rows = await session.scalars(
        select(Run).where(
            Run.task_id.in_(plan.tasks), Run.agent.in_(plan.agents), Run.attempt == plan.attempt
        )
    )
    return list(rows)
