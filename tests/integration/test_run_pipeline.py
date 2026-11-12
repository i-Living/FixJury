from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from fixjury import run as run_module
from fixjury.agents.base import AgentResult, RunRequest
from fixjury.db import make_engine
from fixjury.models import Run, RunStatus, Task
from fixjury.run import RunError, run_task
from fixjury.tasks.loader import read_task

pytestmark = pytest.mark.integration

CORPUS = Path(__file__).resolve().parents[2] / "benchmarks"
DOCUMENT = read_task(CORPUS / "tasks" / "T-001" / "task.yaml")


class FakeAdapter:
    """Stands in for a real agent: the platform must not care which one ran."""

    name = "fake"
    version = "0"

    def __init__(self, *, crash: bool = False) -> None:
        self.crash = crash
        self.seen: list[RunRequest] = []

    async def run(self, request: RunRequest) -> AgentResult:
        self.seen.append(request)
        if self.crash:
            raise RuntimeError("the adapter exploded")
        return AgentResult(
            diff="--- a/module.py\n+++ b/module.py\n",
            diff_sha256="b" * 64,
            declared_success=True,
            unable_reason=None,
            tokens_in=11,
            tokens_out=7,
            steps=3,
            sandbox_seconds=Decimal("1.50"),
            summary="ok",
        )


@asynccontextmanager
async def rolled_back_session() -> AsyncIterator[AsyncSession]:
    """A session whose work is rolled back, so the developer database stays clean."""

    engine: AsyncEngine = make_engine()
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(bind=connection)
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


def require_postgres() -> None:
    async def probe() -> None:
        engine = make_engine()
        try:
            async with engine.connect():
                return
        finally:
            await engine.dispose()

    try:
        asyncio.run(probe())
    except Exception as exc:
        pytest.skip(f"postgres is not reachable: {exc}")


def test_a_finished_run_is_persisted_with_its_diff(monkeypatch: pytest.MonkeyPatch) -> None:
    require_postgres()
    adapter = FakeAdapter()
    monkeypatch.setattr(run_module, "checkout_base", lambda *args: None)
    monkeypatch.setattr(run_module, "build_adapter", lambda name: adapter)

    async def scenario() -> Run:
        async with rolled_back_session() as session:
            row = await run_task(DOCUMENT, session, agent="fake")
            stored = await session.get(Run, row.id)
            assert stored is not None
            assert stored.status is RunStatus.finished
            assert stored.diff_sha256 == "b" * 64
            assert stored.declared_success is True
            assert stored.tokens_in == 11
            assert stored.tokens_out == 7
            assert stored.sandbox_seconds == Decimal("1.50")
            assert stored.pricing_version.startswith("unpriced")
            assert stored.cost_list_usd == Decimal("0")
            assert len(stored.config_hash) == 64
            assert stored.started_at is not None
            assert stored.finished_at is not None
            task = await session.get(Task, DOCUMENT.id)
            assert task is not None
            assert task.repo_url == DOCUMENT.repo
            return stored

    stored = asyncio.run(scenario())

    assert adapter.seen[0].spec.startswith(f"# Task {DOCUMENT.id}")
    assert adapter.seen[0].base_commit == DOCUMENT.base_commit
    assert stored.agent == "fake"
    assert stored.attempt == 1


def test_a_crashed_adapter_becomes_an_infra_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    require_postgres()
    monkeypatch.setattr(run_module, "checkout_base", lambda *args: None)
    monkeypatch.setattr(run_module, "build_adapter", lambda name: FakeAdapter(crash=True))

    async def scenario() -> Run:
        async with rolled_back_session() as session:
            row = await run_task(DOCUMENT, session, agent="fake", attempt=2)
            return row

    row = asyncio.run(scenario())

    assert row.status is RunStatus.infra_failed
    assert row.diff is None
    assert row.diff_sha256 is None
    assert row.declared_success is None


def test_a_frozen_task_is_never_rewritten(monkeypatch: pytest.MonkeyPatch) -> None:
    require_postgres()
    monkeypatch.setattr(run_module, "checkout_base", lambda *args: None)
    monkeypatch.setattr(run_module, "build_adapter", lambda name: FakeAdapter())
    drifted = DOCUMENT.model_copy(update={"base_commit": "f" * 40})

    async def scenario() -> None:
        async with rolled_back_session() as session:
            await run_task(DOCUMENT, session, agent="fake", attempt=3)
            with pytest.raises(RunError, match="frozen at"):
                await run_task(drifted, session, agent="fake", attempt=4)

    asyncio.run(scenario())


def test_only_one_row_per_task_agent_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    require_postgres()
    monkeypatch.setattr(run_module, "checkout_base", lambda *args: None)
    monkeypatch.setattr(run_module, "build_adapter", lambda name: FakeAdapter())

    async def scenario() -> None:
        async with rolled_back_session() as session:
            await run_task(DOCUMENT, session, agent="fake", attempt=5)
            rows = await session.scalars(
                select(Run).where(Run.task_id == DOCUMENT.id, Run.attempt == 5)
            )
            assert len(list(rows)) == 1

    asyncio.run(scenario())
