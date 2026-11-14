from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fixjury import queue
from fixjury.db import make_engine
from fixjury.models import Run, RunStatus
from fixjury.run import ensure_task
from fixjury.tasks.loader import read_task
from fixjury.tasks.schema import TaskDocument
from fixjury.worker import acknowledge, process_run, redis_client, work_once


class FakeRedis:
    """The list commands the queue uses, in memory."""

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}

    async def rpush(self, key: str, *values: str) -> int:
        self.lists.setdefault(key, []).extend(values)
        return len(self.lists[key])

    async def lrem(self, key: str, count: int, value: str) -> int:
        items = self.lists.get(key, [])
        if value not in items:
            return 0
        items.remove(value)
        return 1

    async def llen(self, key: str) -> int:
        return len(self.lists.get(key, []))

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        return list(self.lists.get(key, []))

    async def blmove(
        self,
        source: str,
        destination: str,
        wait_s: float,
        src: str = "LEFT",
        dest: str = "RIGHT",
    ) -> str | None:
        items = self.lists.get(source, [])
        if not items:
            return None
        value = items.pop(0)
        self.lists.setdefault(destination, []).append(value)
        return value


pytestmark = pytest.mark.integration

CORPUS = Path(__file__).resolve().parents[2] / "benchmarks"
DOCUMENT = read_task(CORPUS / "tasks" / "T-001" / "task.yaml")


@pytest.fixture(autouse=True)
def empty_queue() -> None:
    """Every test starts with an empty queue: one failed run must not poison the next."""

    async def wipe() -> None:
        client = redis_client()
        try:
            # redis-py's client can delete; the narrowed queue protocol only needs lists.
            remover: Any = client
            await remover.delete(queue.QUEUED_KEY, queue.PROCESSING_KEY)
        finally:
            await client.aclose()  # type: ignore[attr-defined]

    try:
        asyncio.run(wipe())
    except Exception as exc:  # the test itself skips when Redis is down
        pytest.skip(f"redis is not reachable: {exc}")


def require_services() -> None:
    async def probe_database() -> None:
        engine = make_engine()
        try:
            async with engine.connect():
                return
        finally:
            await engine.dispose()

    async def probe_redis() -> None:
        client = redis_client()
        try:
            await client.llen(queue.QUEUED_KEY)
        finally:
            await client.aclose()  # type: ignore[attr-defined]

    try:
        asyncio.run(probe_database())
    except Exception as exc:
        pytest.skip(f"postgres is not reachable: {exc}")
    try:
        asyncio.run(probe_redis())
    except Exception as exc:
        pytest.skip(f"redis is not reachable: {exc}")


def test_a_queued_run_is_processed_and_leaves_the_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    require_services()

    async def fake_run_task(
        document: TaskDocument,
        session: AsyncSession,
        *,
        agent: str,
        attempt: int,
        image: str | None = None,
        row: Run | None = None,
        **kwargs: object,
    ) -> Run:
        assert row is not None
        row.status = RunStatus.finished
        row.diff = "diff --git a/x b/x\n"
        row.diff_sha256 = "e" * 64
        await session.flush()
        return row

    monkeypatch.setattr("fixjury.worker.run_task", fake_run_task)

    async def scenario() -> tuple[Run | None, dict[str, int], dict[str, int]]:
        engine = make_engine()
        client = redis_client()
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                session = AsyncSession(bind=connection)
                try:
                    await session.get(Run, uuid.uuid4())
                    from fixjury.run import ensure_task

                    await ensure_task(session, DOCUMENT)
                    queued = await queue.enqueue(
                        session, client, task_id=DOCUMENT.id, agent="baseline", attempt=41
                    )
                    assert queued.status is RunStatus.queued
                    row = await work_once(client, session, corpus_root=CORPUS, wait_s=0.1)
                    before = await queue.depth(client)
                    assert row is not None
                    # The caller acknowledges once the transaction is durable, not the worker.
                    await acknowledge(client, row.id)
                    return row, before, await queue.depth(client)
                finally:
                    await session.close()
                    await transaction.rollback()
        finally:
            await client.aclose()  # type: ignore[attr-defined]
            await engine.dispose()

    row, before, after = asyncio.run(scenario())

    assert row is not None
    assert row.status is RunStatus.finished
    assert before == {"queued": 0, "processing": 1}
    assert after == {"queued": 0, "processing": 0}


def test_a_cancelled_worker_leaves_the_run_for_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    """A killed process must leave a delivery ``resume`` can recover, not a lost run."""

    require_services()
    started = asyncio.Event()

    async def hanging_run_task(*args: object, **kwargs: object) -> Run:
        started.set()
        await asyncio.sleep(3600)
        raise AssertionError("the run must be cancelled before it finishes")

    monkeypatch.setattr("fixjury.worker.run_task", hanging_run_task)

    async def scenario() -> tuple[dict[str, int], int, dict[str, int]]:
        engine = make_engine()
        client = redis_client()
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                session = AsyncSession(bind=connection)
                try:
                    await ensure_task(session, DOCUMENT)
                    await queue.enqueue(
                        session, client, task_id=DOCUMENT.id, agent="baseline", attempt=42
                    )
                    working = asyncio.ensure_future(
                        work_once(client, session, corpus_root=CORPUS, wait_s=0.1)
                    )
                    await asyncio.wait_for(started.wait(), timeout=5)
                    working.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await working
                    before = await queue.depth(client)
                    recovered = await queue.resume(client)
                    return before, recovered, await queue.depth(client)
                finally:
                    await session.close()
                    await transaction.rollback()
        finally:
            await client.aclose()  # type: ignore[attr-defined]
            await engine.dispose()

    before, recovered, after = asyncio.run(scenario())

    assert before == {"queued": 0, "processing": 1}
    assert recovered == 1
    assert after == {"queued": 1, "processing": 0}


def test_a_run_that_never_commits_is_still_in_the_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The id leaves ``processing`` only after the result is durable."""

    require_services()

    async def fake_run_task(
        document: TaskDocument, session: AsyncSession, *, row: Run | None = None, **kwargs: object
    ) -> Run:
        assert row is not None
        row.status = RunStatus.finished
        row.diff = "diff --git a/x b/x\n"
        row.diff_sha256 = "f" * 64
        await session.flush()
        return row

    monkeypatch.setattr("fixjury.worker.run_task", fake_run_task)

    async def scenario() -> dict[str, int]:
        engine = make_engine()
        client = redis_client()
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                session = AsyncSession(bind=connection)
                try:
                    await ensure_task(session, DOCUMENT)
                    await queue.enqueue(
                        session, client, task_id=DOCUMENT.id, agent="baseline", attempt=43
                    )
                    await work_once(client, session, corpus_root=CORPUS, wait_s=0.1)
                    # The worker dies before the caller commits and acknowledges.
                    return await queue.depth(client)
                finally:
                    await session.close()
                    await transaction.rollback()
        finally:
            await client.aclose()  # type: ignore[attr-defined]
            await engine.dispose()

    assert asyncio.run(scenario()) == {"queued": 0, "processing": 1}


def test_a_crash_leaves_the_run_in_processing_and_resume_recovers_it() -> None:
    require_services()
    client = FakeRedis()

    async def scenario() -> tuple[str | None, int, dict[str, int]]:
        await client.rpush(queue.QUEUED_KEY, "run-1")
        claimed = await queue.claim(client)
        recovered = await queue.resume(client)
        return claimed, recovered, await queue.depth(client)

    claimed, recovered, counts = asyncio.run(scenario())

    assert claimed == "run-1"
    assert recovered == 1
    assert counts == {"queued": 1, "processing": 0}


def test_an_infra_failure_queues_the_next_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    require_services()

    async def failing_run_task(
        document: TaskDocument,
        session: AsyncSession,
        *,
        agent: str,
        attempt: int,
        image: str | None = None,
        row: Run | None = None,
        **kwargs: object,
    ) -> Run:
        assert row is not None
        row.status = RunStatus.infra_failed
        await session.flush()
        return row

    monkeypatch.setattr("fixjury.worker.run_task", failing_run_task)
    client = FakeRedis()

    async def scenario() -> tuple[Run, list[int]]:
        engine = make_engine()
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                session = AsyncSession(bind=connection)
                try:
                    from fixjury.run import ensure_task

                    await ensure_task(session, DOCUMENT)
                    queued = await queue.enqueue(
                        session, client, task_id=DOCUMENT.id, agent="baseline", attempt=1
                    )
                    await client.rpush(queue.QUEUED_KEY, str(queued.id))
                    row = await process_run(client, session, queued.id, corpus_root=CORPUS)
                    attempts = [
                        candidate.attempt
                        for candidate in (
                            await session.scalars(
                                select(Run).where(
                                    Run.task_id == DOCUMENT.id, Run.agent == "baseline"
                                )
                            )
                        ).all()
                    ]
                    return row, sorted(attempts)
                finally:
                    await session.close()
                    await transaction.rollback()
        finally:
            await engine.dispose()

    row, attempts = asyncio.run(scenario())

    assert row.status is RunStatus.infra_failed
    assert 2 in attempts, "a failed attempt must queue the next one"


def test_an_unknown_queued_run_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    require_services()
    client = FakeRedis()

    async def scenario() -> None:
        engine = make_engine()
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                session = AsyncSession(bind=connection)
                try:
                    await process_run(client, session, uuid.uuid4(), corpus_root=CORPUS)
                finally:
                    await session.close()
                    await transaction.rollback()
        finally:
            await engine.dispose()

    from fixjury.worker import WorkerError

    with pytest.raises(WorkerError, match="missing from the database"):
        asyncio.run(scenario())
