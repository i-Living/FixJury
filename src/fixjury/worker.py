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
from sqlalchemy.ext.asyncio import AsyncSession

from fixjury import queue
from fixjury.config import settings
from fixjury.models import Run, RunStatus
from fixjury.run import RunError, run_task
from fixjury.tasks.loader import read_task, task_path

LOGGER = logging.getLogger(__name__)


class WorkerError(RuntimeError):
    """A queued item cannot be processed."""


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
    """Execute one claimed run and settle the queue around it."""

    run = await session.get(Run, run_id)
    if run is None:
        await queue.complete(client, str(run_id))
        raise WorkerError(f"run {run_id} is queued but missing from the database")
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
