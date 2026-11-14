"""The worker: claim a queued run, execute it, retry once if it was infra (PLAN §11).

The retry rule is the one that matters for the benchmark: a run that failed
because of infrastructure is retried as a *new attempt row*, never by rewriting
the failed one, so the failed attempt stays visible in the data.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

import redis.asyncio as redis
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from fixjury import queue
from fixjury.config import settings
from fixjury.models import Run, RunStatus
from fixjury.run import RunError, run_task
from fixjury.tasks.loader import read_task, task_path

LOGGER = logging.getLogger(__name__)
# PostgreSQL: "could not obtain lock on row in relation" — raised by FOR UPDATE NOWAIT.
LOCK_NOT_AVAILABLE = "55P03"


class WorkerError(RuntimeError):
    """A queued item cannot be processed."""


def _is_lock_conflict(exc: DBAPIError) -> bool:
    """Postgres refuses a ``NOWAIT`` lock with SQLSTATE 55P03, and nothing else means "taken".

    The driver's exception class is generic (``AsyncAdapt_asyncpg_dbapi.Error``), so the
    state code is the only reliable discriminator.
    """

    orig = getattr(exc, "orig", None)
    state = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return state == LOCK_NOT_AVAILABLE


def redis_client() -> queue.RedisList:
    """A Redis client that speaks the small list protocol the queue needs."""

    return redis.from_url(settings.redis_url, decode_responses=True)  # type: ignore[return-value]


async def process_run(
    client: queue.RedisList,
    session: AsyncSession,
    run_id: uuid.UUID,
    *,
    corpus_root: Path,
    image: str | None = None,
) -> Run:
    """Execute one claimed run and settle the queue around it.

    The row is locked ``NOWAIT`` for the whole attempt: two deliveries of the same id
    — a stale duplicate left in Redis, or a second worker — cannot both execute it,
    and the loser backs off without blocking on a run that takes minutes. It leaves the
    id where it found it, so a worker that dies still leaves a recoverable orphan.
    """

    try:
        run = await session.get(Run, run_id, with_for_update={"nowait": True})
    except DBAPIError as exc:
        if not _is_lock_conflict(exc):
            raise
        # The id stays in ``processing``: the worker that holds the lock will remove it
        # when it settles, and if that worker dies instead, ``resume`` must still find
        # the run. Dropping it here would leave an orphan nothing would ever requeue.
        LOGGER.info("run %s is being worked elsewhere, leaving it to that worker", run_id)
        raise WorkerError(f"run {run_id} is already claimed") from exc
    if run is None:
        await queue.complete(client, str(run_id))
        raise WorkerError(f"run {run_id} is queued but missing from the database")
    if run.finished_at is not None:
        # A stale queue id for a run that already settled: working it again would
        # spend tokens on a pair the report has already counted.
        LOGGER.info("run %s for %s/%s already settled, dropping it", run_id, run.task_id, run.agent)
        await queue.complete(client, str(run_id))
        return run
    document = read_task(task_path(corpus_root, run.task_id))
    run.status = RunStatus.running
    run.started_at = datetime.now(UTC)
    await session.flush()

    try:
        await run_task(
            document,
            session,
            agent=run.agent,
            attempt=run.attempt,
            image=image,
            row=run,
        )
    except RunError as exc:
        LOGGER.warning("run %s cannot be prepared: %s", run_id, exc)
        run.status = RunStatus.infra_failed
        run.finished_at = datetime.now(UTC)
        await session.flush()
    finally:
        await queue.complete(client, str(run_id))

    if run.status is RunStatus.infra_failed:
        retry = queue.next_attempt(run.attempt)
        if retry is not None:
            try:
                await queue.enqueue(
                    session, client, task_id=run.task_id, agent=run.agent, attempt=retry
                )
            except queue.QueueError as exc:
                # A retry that already exists in another state is not a reason to
                # kill the worker: the row is the record, the queue is transport.
                LOGGER.warning("no retry queued for %s: %s", run.id, exc)
    return run


async def work_once(
    client: queue.RedisList,
    session: AsyncSession,
    *,
    corpus_root: Path,
    image: str | None = None,
    wait_s: float = 5.0,
) -> Run | None:
    """Claim one run and process it; ``None`` when the queue stayed empty."""

    raw = await queue.claim(client, wait_s=wait_s)
    if raw is None:
        return None
    return await process_run(
        client, session, queue.parse_run_id(raw), corpus_root=corpus_root, image=image
    )
