"""The gate command's exit codes (PLAN_V1.1 §5.1), against a real stored evaluation.

The contract in `docs/USAGE.md` is PASS 0, FAIL 1, INCONCLUSIVE 2, and a pipeline that gates
on it needs all three to be reachable. Each case here commits a real run, verdict and
evaluation, then drives `fixjury gates check` the way a script would.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from typer.testing import CliRunner, Result

from fixjury.cli import app
from fixjury.db import make_engine, make_session_maker, session_scope
from fixjury.evaluation.checks import stored_outcomes
from fixjury.evaluation.contracts import AssessmentSummary
from fixjury.evaluation.scoring import assemble_result, load_profile
from fixjury.evaluation.service import build_request
from fixjury.evaluation.store import store_evaluation
from fixjury.models import Criterion, EvaluationVerdict, Run, RunStatus, Verdict
from fixjury.run import ensure_task
from fixjury.tasks.loader import read_task
from fixjury.tasks.schema import CheckTool
from fixjury.verify.verdict import CheckOutcome

pytestmark = pytest.mark.integration

CORPUS = Path(__file__).resolve().parents[2] / "benchmarks"
PROFILE_PATH = Path(__file__).resolve().parents[2] / "config" / "evaluation.yaml"
DIFF = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1,2 @@\n-broken\n+fixed\n"


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


async def seed(failing: bool, *, judged: bool = True) -> uuid.UUID:
    """Commit a run, its verdict and an evaluation; return the run id."""

    engine = make_engine()
    maker = make_session_maker(engine)
    try:
        async with session_scope(maker) as session:
            document = read_task(CORPUS / "tasks" / "T-001" / "task.yaml")
            await ensure_task(session, document)
            started = datetime.now(UTC)
            run = Run(
                id=uuid.uuid4(),
                task_id="T-001",
                agent="gates-cli-test",
                agent_version="0",
                model="stub",
                attempt=1,
                status=RunStatus.finished,
                declared_success=True,
                diff=DIFF,
                diff_sha256=hashlib.sha256(DIFF.encode("utf-8")).hexdigest(),
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
            session.add(
                Verdict(
                    run_id=run.id,
                    tests_pass=not failing,
                    lint_pass=True,
                    type_pass=True,
                    policy_pass=True,
                    heldout_pass=not failing,
                    automated_pass=not failing,
                    tool_versions={"pytest": "8.4.1"},
                    check_results=stored_outcomes(
                        (
                            CheckOutcome(
                                command="pytest tests",
                                tool=CheckTool.pytest,
                                exit_code=1 if failing else 0,
                                output="1 failed" if failing else "4 passed",
                            ),
                            CheckOutcome(
                                command="pytest tests_heldout",
                                tool=CheckTool.pytest,
                                exit_code=1 if failing else 0,
                                output="1 failed" if failing else "3 passed",
                                heldout=True,
                            ),
                        )
                    ),
                )
            )
            await session.flush()
            request = await build_request(session, run_id=run.id, corpus_root=CORPUS)
            assessment = AssessmentSummary(
                judge_id="judge-1",
                provider="fake",
                model="strong-model",
                prompt_version="1",
                verdict=EvaluationVerdict.ACCEPTED if not failing else EvaluationVerdict.REJECTED,
                overall=88.0,
                confidence=0.9,
                tokens_in=80,
                tokens_out=20,
                cost_list_usd=Decimal("0.0100"),
                latency_ms=1000,
            )
            result = assemble_result(
                request,
                profile=load_profile(PROFILE_PATH),
                judge_verdict=assessment.verdict if judged else None,
                # What the judge scores: every criterion at 88, as a real assessment would.
                judge_scores={criterion: 88.0 for criterion in Criterion} if judged else {},
                findings=(),
                assessments=(assessment,) if judged else (),
                confidence=0.9 if judged else 0.0,
            )
            await store_evaluation(session, request=request, result=result)
            return run.id
    finally:
        await engine.dispose()


async def remove(run_id: uuid.UUID) -> None:
    engine = make_engine()
    maker = make_session_maker(engine)
    try:
        async with session_scope(maker) as session:
            await session.execute(delete(Run).where(Run.id == run_id))
    finally:
        await engine.dispose()


def gate(run_id: uuid.UUID) -> Result:
    return CliRunner().invoke(app, ["gates", "check", "--run", str(run_id)])


def test_a_passing_gate_exits_zero() -> None:
    require_postgres()
    run_id = asyncio.run(seed(failing=False))
    try:
        result = gate(run_id)
    finally:
        asyncio.run(remove(run_id))

    assert result.exit_code == 0, result.output
    assert "Quality gates — PASS" in result.output


def test_a_failing_gate_exits_one() -> None:
    require_postgres()
    run_id = asyncio.run(seed(failing=True))
    try:
        result = gate(run_id)
    finally:
        asyncio.run(remove(run_id))

    assert result.exit_code == 1, result.output
    assert "Quality gates — FAIL" in result.output


def test_an_inconclusive_gate_exits_two() -> None:
    """No judge verdict means no decision, and no decision is not a pass."""

    require_postgres()
    run_id = asyncio.run(seed(failing=False, judged=False))
    try:
        result = gate(run_id)
    finally:
        asyncio.run(remove(run_id))

    assert result.exit_code == 2, result.output
    assert "Quality gates — INCONCLUSIVE" in result.output


def test_a_run_without_an_evaluation_cannot_be_gated() -> None:
    require_postgres()

    result = CliRunner().invoke(app, ["gates", "check", "--run", str(uuid.uuid4())])

    assert result.exit_code == 2
    assert "no evaluation" in result.output


def test_a_stored_evaluation_is_not_gated_against_newer_evidence() -> None:
    """Re-verification rewrites the checks; the old verdict must not be gated silently."""

    require_postgres()
    run_id = asyncio.run(seed(failing=False))
    try:

        async def reverify() -> None:
            engine = make_engine()
            maker = make_session_maker(engine)
            try:
                async with session_scope(maker) as session:
                    verdict = (
                        await session.execute(select(Verdict).where(Verdict.run_id == run_id))
                    ).scalar_one()
                    verdict.check_results = stored_outcomes(
                        (
                            CheckOutcome(
                                command="pytest tests",
                                tool=CheckTool.pytest,
                                exit_code=1,
                                output="1 failed after re-verification",
                            ),
                        )
                    )
            finally:
                await engine.dispose()

        asyncio.run(reverify())
        result = gate(run_id)
    finally:
        asyncio.run(remove(run_id))

    assert result.exit_code == 2
    assert "different check evidence" in result.output
    assert "judge evaluate" in result.output
