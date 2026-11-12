from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fixjury.db import make_engine
from fixjury.metrics.service import compute_snapshot, latest_snapshots, total_cost
from fixjury.models import CostBasis, MetricSnapshot, Run, RunStatus, Verdict

pytestmark = pytest.mark.integration

CORPUS = Path(__file__).resolve().parents[2] / "benchmarks"


@asynccontextmanager
async def rolled_back_session() -> AsyncIterator[AsyncSession]:
    engine = make_engine()
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


async def add_run(
    session: AsyncSession,
    *,
    task_id: str,
    agent: str = "metrics-test",
    attempt: int = 1,
    status: RunStatus = RunStatus.finished,
    declared_success: bool | None = True,
    tokens: int = 100,
    cost: str = "0.5",
    human: str | None = None,
    automated: bool = True,
    heldout: bool | None = True,
) -> Run:
    from fixjury.models import HumanVerdict
    from fixjury.run import ensure_task
    from fixjury.tasks.loader import read_task

    document = read_task(CORPUS / "tasks" / task_id / "task.yaml")
    await ensure_task(session, document)
    started = datetime.now(UTC)
    run = Run(
        id=uuid.uuid4(),
        task_id=task_id,
        agent=agent,
        agent_version="0",
        model="stub",
        attempt=attempt,
        status=status,
        declared_success=declared_success,
        diff="diff --git a/x b/x\n" if status is RunStatus.finished else None,
        diff_sha256="a" * 64 if status is RunStatus.finished else None,
        tokens_in=tokens,
        tokens_out=0,
        sandbox_seconds=Decimal("10"),
        cost_list_usd=Decimal(cost),
        pricing_version="unpriced-2026-11",
        config_hash="c" * 64,
        started_at=started,
        finished_at=started + timedelta(seconds=30),
    )
    session.add(run)
    await session.flush()
    session.add(
        Verdict(
            run_id=run.id,
            tests_pass=True,
            lint_pass=True,
            type_pass=True,
            policy_pass=True,
            heldout_pass=heldout,
            automated_pass=automated,
            tool_versions={"pytest": "8.4.1"},
            human_verdict=HumanVerdict(human) if human else None,
        )
    )
    await session.flush()
    return run


def test_a_snapshot_is_computed_once_and_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    require_postgres()

    async def scenario() -> MetricSnapshot:
        async with rolled_back_session() as session:
            await add_run(session, task_id="T-001", human="accept")
            await add_run(session, task_id="T-002", attempt=1, human="reject")
            await add_run(
                session, task_id="T-003", attempt=1, status=RunStatus.infra_failed, cost="0"
            )
            snapshot = await compute_snapshot(session, agent="metrics-test")
            assert isinstance(snapshot.payload, dict)
            return snapshot

    snapshot = asyncio.run(scenario())

    payload = snapshot.payload
    assert payload["agent"] == "metrics-test"
    assert payload["cost_basis"] == "list_price"
    denominators = payload["denominators"]
    assert isinstance(denominators, dict)
    assert denominators["pairs"] == 2
    assert denominators["accepted"] == 1
    assert payload["exclusions"] == {"infra_failed": 1}
    metrics = payload["metrics"]
    assert isinstance(metrics, dict)
    assert metrics["tokens_per_accepted_fix"] == "200.00"


def test_the_snapshot_cost_total_matches_the_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """PLAN §11 task 4.4: the snapshot must agree with the rows it came from."""

    require_postgres()

    async def scenario() -> tuple[Decimal, dict[str, object]]:
        async with rolled_back_session() as session:
            await add_run(session, task_id="T-001", human="accept", cost="0.75")
            await add_run(session, task_id="T-002", attempt=1, human="reject", cost="1.25")
            snapshot = await compute_snapshot(session, agent="metrics-test")
            rows = list(
                (
                    await session.execute(
                        select(Run, Verdict)
                        .outerjoin(Verdict, Verdict.run_id == Run.id)
                        .where(Run.agent == "metrics-test")
                    )
                ).all()
            )
            return total_cost(rows), snapshot.payload

    total, payload = asyncio.run(scenario())

    metrics = payload["metrics"]
    assert isinstance(metrics, dict)
    assert Decimal(str(metrics["cost_total"])) == total == Decimal("2.00")


def test_a_second_computation_creates_a_second_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    require_postgres()

    async def scenario() -> tuple[int, int]:
        async with rolled_back_session() as session:
            await add_run(session, task_id="T-001", human="accept")
            first = await compute_snapshot(session, agent="metrics-test")
            second = await compute_snapshot(
                session, agent="metrics-test", cost_basis=CostBasis.amortized
            )
            stored = await session.scalar(
                select(func.count())
                .select_from(MetricSnapshot)
                .where(MetricSnapshot.agent == "metrics-test")
            )
            assert first.id != second.id
            return int(stored or 0), len(await latest_snapshots(session, agent="metrics-test"))

    stored, latest = asyncio.run(scenario())

    assert stored == 2
    assert latest == 1, "latest_snapshots returns one row per agent"


def test_an_agent_without_runs_is_refused() -> None:
    require_postgres()

    async def scenario() -> None:
        async with rolled_back_session() as session:
            with pytest.raises(Exception, match="no runs"):
                await compute_snapshot(session, agent="nobody")

    asyncio.run(scenario())
