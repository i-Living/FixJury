"""The run queue (PLAN §11, task 4.3).

Redis holds two lists: ``queued`` and ``processing``. A worker moves an item
from one to the other atomically with ``BLMOVE``, so a crash leaves the run in
``processing`` instead of losing it, and ``resume`` puts it back.

Duplicates are prevented by the database, not by the queue: ``runs`` has a
unique constraint on ``(task_id, agent, attempt)``, so enqueueing the same triple
twice inserts one row and pushes one id.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fixjury.metrics.cost import pricing
from fixjury.models import Run, RunStatus

QUEUED_KEY = "fixjury:runs:queued"
PROCESSING_KEY = "fixjury:runs:processing"
MAX_ATTEMPTS = 2


class QueueError(RuntimeError):
    """The queue cannot be used as asked."""


class RedisList(Protocol):
    """The handful of commands the queue needs, so tests can fake them."""

    async def rpush(self, key: str, *values: str) -> int: ...

    async def lrem(self, key: str, count: int, value: str) -> int: ...

    async def llen(self, key: str) -> int: ...

    async def lrange(self, key: str, start: int, end: int) -> list[str]: ...

    # redis-py's Pipeline: a context manager whose commands are queued, not awaited.
    # Typed loosely because the protocol only needs the two writes below.
    def pipeline(self, *, transaction: bool = True) -> Any: ...

    async def blmove(
        self,
        source: str,
        destination: str,
        wait_s: float,
        src: str = "LEFT",
        dest: str = "RIGHT",
    ) -> str | None: ...


async def replace_pending(client: RedisList, run_id: str) -> None:
    """Queue ``run_id`` once, dropping any id already pending for it.

    Re-running a bench pushes the same row again, and a duplicated id means the pair is
    worked, and paid for, twice. Both writes go in one transaction: a crash between
    them would leave the row queued in the database and absent from Redis.
    """

    async with client.pipeline(transaction=True) as pipe:
        pipe.lrem(QUEUED_KEY, 0, run_id)
        pipe.rpush(QUEUED_KEY, run_id)


def next_attempt(current: int) -> int | None:
    """The attempt to retry, or ``None`` when the budget is spent."""

    return current + 1 if current < MAX_ATTEMPTS else None


async def enqueue(
    session: AsyncSession, client: RedisList, *, task_id: str, agent: str, attempt: int = 1
) -> Run:
    """Create (or reuse) the run row for one triple and queue it."""

    existing = await session.scalar(
        select(Run).where(Run.task_id == task_id, Run.agent == agent, Run.attempt == attempt)
    )
    if existing is not None:
        if existing.status is not RunStatus.queued:
            state = existing.status.value
            raise QueueError(
                f"run {existing.id} for {task_id}/{agent}/{attempt} is already {state}"
            )
        await replace_pending(client, str(existing.id))
        return existing

    row = Run(
        id=uuid.uuid4(),
        task_id=task_id,
        agent=agent,
        agent_version="pending",
        model="pending",
        attempt=attempt,
        status=RunStatus.queued,
        tokens_in=0,
        tokens_out=0,
        # The version the run will be priced under is already decided, so record it
        # now: a placeholder here leaks into the snapshot's label while the run is
        # still queued.
        pricing_version=pricing().version,
        config_hash="0" * 64,
    )
    session.add(row)
    await session.flush()
    await client.rpush(QUEUED_KEY, str(row.id))
    return row


async def claim(client: RedisList, *, wait_s: float = 5.0) -> str | None:
    """Atomically take one run id from ``queued`` and park it in ``processing``."""

    run_id = await client.blmove(QUEUED_KEY, PROCESSING_KEY, wait_s)
    return run_id or None


async def complete(client: RedisList, run_id: str) -> None:
    """Drop a finished run from ``processing``."""

    await client.lrem(PROCESSING_KEY, 1, run_id)


async def resume(client: RedisList) -> int:
    """Put every run left in ``processing`` back into ``queued``.

    This moves ids, not runs: a run whose worker is still alive is protected from a
    second delivery by the row lock the worker holds for the whole attempt, so resume
    can stay a blind requeue and an orphan is never left behind.
    """

    orphans = await client.lrange(PROCESSING_KEY, 0, -1)
    for run_id in orphans:
        await client.lrem(PROCESSING_KEY, 1, run_id)
        await client.rpush(QUEUED_KEY, run_id)
    return len(orphans)


async def depth(client: RedisList) -> dict[str, int]:
    return {
        "queued": await client.llen(QUEUED_KEY),
        "processing": await client.llen(PROCESSING_KEY),
    }


def parse_run_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise QueueError(f"queue holds something that is not a run id: {raw!r}") from exc
