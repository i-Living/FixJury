"""Judging during a benchmark run (PLAN_V1.1 §3.4).

A judge failure must never become a failed run or a verdict about the patch: the agent
finished, the checks ran, and only the opinion is missing. These tests drive the real
`record_failure` against PostgreSQL and check the row it leaves behind.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fixjury.bench import JudgeStep, judge_verified_run
from fixjury.config import settings
from fixjury.db import make_engine
from fixjury.evaluation.store import UNSCORED
from fixjury.models import (
    EvaluationResult,
    EvaluationRun,
    EvaluationStatus,
    Run,
    RunStatus,
    Task,
    Verdict,
)
from fixjury.run import ensure_task
from fixjury.tasks.loader import read_task

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


async def add_run(session: AsyncSession, *, task_id: str = "T-001", verified: bool = True) -> Run:
    document = read_task(CORPUS / "tasks" / task_id / "task.yaml")
    await ensure_task(session, document)
    started = datetime.now(UTC)
    run = Run(
        id=uuid.uuid4(),
        task_id=task_id,
        agent="bench-judge-test",
        agent_version="0",
        model="stub",
        attempt=1,
        status=RunStatus.finished,
        declared_success=True,
        diff="diff --git a/x b/x\n",
        diff_sha256="a" * 64,
        tokens_in=100,
        tokens_out=0,
        sandbox_seconds=Decimal("10"),
        cost_list_usd=Decimal("0.5"),
        pricing_version="unpriced-2026-11",
        config_hash="c" * 64,
        started_at=started,
        finished_at=started + timedelta(seconds=30),
    )
    session.add(run)
    await session.flush()
    if verified:
        session.add(
            Verdict(
                run_id=run.id,
                tests_pass=True,
                lint_pass=True,
                type_pass=True,
                policy_pass=True,
                heldout_pass=True,
                automated_pass=True,
                tool_versions={"pytest": "8.4.1"},
            )
        )
        await session.flush()
    return run


def test_a_run_without_a_verdict_leaves_a_failed_evaluation_not_a_failed_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The judge refuses an unverified run; the run itself keeps its status."""

    require_postgres()
    # A configured judge that never gets asked: the request cannot even be built.
    monkeypatch.setattr(settings, "judge_model", "judge-strong-model")
    monkeypatch.setattr(settings, "judge_base_url", "http://127.0.0.1:9/v1")

    async def scenario() -> tuple[JudgeStep, RunStatus, list[EvaluationRun]]:
        async with rolled_back_session() as session:
            run = await add_run(session, verified=False)
            step = await judge_verified_run(session, run=run, corpus_root=CORPUS)
            await session.flush()
            await session.refresh(run)
            evaluations = list(
                (await session.execute(select(EvaluationRun).where(EvaluationRun.run_id == run.id)))
                .scalars()
                .all()
            )
            return step, run.status, evaluations

    step, status, evaluations = asyncio.run(scenario())

    assert step.judged is False
    assert step.error is not None and "no verdict" in step.error
    assert status is RunStatus.finished, "the run is untouched: only the opinion is missing"
    assert len(evaluations) == 1
    failed = evaluations[0]
    assert failed.status is EvaluationStatus.failed
    assert failed.error is not None and "no verdict" in failed.error
    assert failed.scoring_version == UNSCORED
    assert failed.task_id == "T-001"
    assert failed.subject_ref == "T-001"


def test_a_judge_that_cannot_start_records_why(monkeypatch: pytest.MonkeyPatch) -> None:
    """No judge model configured is an evaluation failure with a message, not a crash."""

    require_postgres()
    monkeypatch.setattr(settings, "judge_model", "")

    async def scenario() -> tuple[JudgeStep, EvaluationRun]:
        async with rolled_back_session() as session:
            run = await add_run(session)
            step = await judge_verified_run(session, run=run, corpus_root=CORPUS)
            await session.flush()
            evaluation = (
                await session.execute(select(EvaluationRun).where(EvaluationRun.run_id == run.id))
            ).scalar_one()
            return step, evaluation

    step, evaluation = asyncio.run(scenario())

    assert step.judged is False
    assert step.error is not None and "judge model" in step.error
    assert evaluation.status is EvaluationStatus.failed
    assert evaluation.error == step.error
    # Nothing was scored, so no result row claims a verdict.
    assert evaluation.id is not None


def test_a_judged_run_writes_an_evaluation_without_a_failure_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The counting path: one verdict, no failure row, the run still finished."""

    require_postgres()

    async def fake_judge(session: AsyncSession, **kwargs: object) -> None:
        return None

    monkeypatch.setattr("fixjury.bench.judge_run", fake_judge)

    async def scenario() -> tuple[JudgeStep, int]:
        async with rolled_back_session() as session:
            run = await add_run(session)
            step = await judge_verified_run(session, run=run, corpus_root=CORPUS)
            await session.flush()
            rows = list(
                (await session.execute(select(EvaluationRun).where(EvaluationRun.run_id == run.id)))
                .scalars()
                .all()
            )
            return step, len(rows)

    step, rows = asyncio.run(scenario())

    assert step.judged is True
    assert step.error is None
    assert rows == 0, "a successful judgement is written by the judge, not by the bench"


def test_a_failed_evaluation_is_not_counted_as_a_verdict() -> None:
    """A failed row has no result, so nothing can read it as a decision about a patch."""

    require_postgres()

    async def scenario() -> int:
        async with rolled_back_session() as session:
            run = await add_run(session, verified=False)
            await judge_verified_run(session, run=run, corpus_root=CORPUS)
            await session.flush()
            evaluation = (
                await session.execute(select(EvaluationRun).where(EvaluationRun.run_id == run.id))
            ).scalar_one()
            results = list(
                (
                    await session.execute(
                        select(EvaluationResult).where(
                            EvaluationResult.evaluation_run_id == evaluation.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            return len(results)

    assert asyncio.run(scenario()) == 0


def test_the_failed_row_keeps_the_configuration_it_ran_under() -> None:
    require_postgres()

    async def scenario() -> dict[str, object]:
        async with rolled_back_session() as session:
            run = await add_run(session, verified=False)
            await judge_verified_run(session, run=run, corpus_root=CORPUS)
            await session.flush()
            evaluation = (
                await session.execute(select(EvaluationRun).where(EvaluationRun.run_id == run.id))
            ).scalar_one()
            return dict(evaluation.config)

    config = asyncio.run(scenario())

    assert config["profile"] == "default"
    assert config["mode"] in {"single", "adaptive", "multi"}
    assert len(str(config["schema_version"])) > 0


def test_the_task_row_survives_a_failed_evaluation() -> None:
    """A failed evaluation points at a real task; nothing cascades into the corpus."""

    require_postgres()

    async def scenario() -> str:
        async with rolled_back_session() as session:
            run = await add_run(session, verified=False)
            await judge_verified_run(session, run=run, corpus_root=CORPUS)
            await session.flush()
            task = await session.get(Task, "T-001")
            assert task is not None
            return task.id

    assert asyncio.run(scenario()) == "T-001"
