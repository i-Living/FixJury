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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fixjury.db import make_engine
from fixjury.evaluation.checks import stored_outcomes
from fixjury.evaluation.contracts import (
    AssessmentSummary,
    EvaluationRequest,
    EvaluationResult,
)
from fixjury.evaluation.scoring import assemble_result, load_profile
from fixjury.evaluation.service import EvaluationError, build_request
from fixjury.evaluation.store import evaluations_for_run, load_evaluation, store_evaluation
from fixjury.models import (
    Criterion,
    EvaluationFinding,
    EvaluationRun,
    EvaluationStatus,
    EvaluationVerdict,
    Run,
    RunStatus,
    Verdict,
)
from fixjury.models import (
    EvaluationResult as EvaluationResultRow,
)
from fixjury.run import ensure_task
from fixjury.tasks.loader import read_task
from fixjury.tasks.schema import CheckTool
from fixjury.verify.verdict import CheckOutcome

pytestmark = pytest.mark.integration

CORPUS = Path(__file__).resolve().parents[2] / "benchmarks"
PROFILE_PATH = Path(__file__).resolve().parents[2] / "config" / "evaluation.yaml"
DIFF = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-broken\n+fixed\n"

OUTCOMES = (
    CheckOutcome(command="pytest tests", tool=CheckTool.pytest, exit_code=0, output="4 passed"),
    CheckOutcome(
        command="ruff check src", tool=CheckTool.ruff, exit_code=0, output="All checks passed"
    ),
    CheckOutcome(command="mypy src", tool=CheckTool.mypy, exit_code=0, output="Success"),
    CheckOutcome(
        command="pytest tests_heldout",
        tool=CheckTool.pytest,
        exit_code=0,
        output="3 passed",
        heldout=True,
    ),
)


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


async def add_verified_run(
    session: AsyncSession,
    *,
    task_id: str = "T-001",
    with_verdict: bool = True,
) -> Run:
    document = read_task(CORPUS / "tasks" / task_id / "task.yaml")
    await ensure_task(session, document)
    started = datetime.now(UTC)
    run = Run(
        id=uuid.uuid4(),
        task_id=task_id,
        agent="evaluation-test",
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
    if with_verdict:
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
                check_results=stored_outcomes(OUTCOMES),
            )
        )
        await session.flush()
    return run


def judge_assessment() -> AssessmentSummary:
    return AssessmentSummary(
        judge_id="primary",
        provider="opencode-go",
        model="a-stronger-model",
        prompt_version="j1",
        verdict=EvaluationVerdict.ACCEPTED,
        overall=88.0,
        confidence=0.85,
        tokens_in=1200,
        tokens_out=300,
        cost_list_usd=Decimal("0.0042"),
        latency_ms=1500,
    )


def test_a_request_is_built_from_the_rows_verification_left_behind() -> None:
    require_postgres()

    async def scenario() -> EvaluationRequest:
        async with rolled_back_session() as session:
            run = await add_verified_run(session)
            return await build_request(session, run_id=run.id, corpus_root=CORPUS)

    request = asyncio.run(scenario())

    assert request.subject_ref == "T-001"
    assert request.policy_pass is True
    assert request.policy_violations == ()
    assert [check.name for check in request.checks] == [
        "pytest tests",
        "ruff check src",
        "mypy src",
        "held-out",
    ]
    assert request.checks[0].summary == "4 passed"


def test_a_run_without_a_verdict_is_not_evaluated() -> None:
    require_postgres()

    async def scenario() -> None:
        async with rolled_back_session() as session:
            run = await add_verified_run(session, with_verdict=False)
            with pytest.raises(EvaluationError, match="has no verdict"):
                await build_request(session, run_id=run.id, corpus_root=CORPUS)

    asyncio.run(scenario())


def test_a_verdict_without_stored_evidence_is_refused() -> None:
    """A V1 verdict carries five booleans and nothing a judge could cite."""

    require_postgres()

    async def scenario() -> None:
        async with rolled_back_session() as session:
            run = await add_verified_run(session)
            verdict = await session.get(Verdict, run.id)
            assert verdict is not None
            verdict.check_results = []
            await session.flush()
            with pytest.raises(EvaluationError, match="no stored check evidence"):
                await build_request(session, run_id=run.id, corpus_root=CORPUS)

    asyncio.run(scenario())


def test_an_evaluation_round_trips_through_the_database() -> None:
    require_postgres()

    async def scenario() -> None:
        profile = load_profile(PROFILE_PATH)
        async with rolled_back_session() as session:
            run = await add_verified_run(session)
            request = await build_request(session, run_id=run.id, corpus_root=CORPUS)
            result = assemble_result(request, profile=profile)
            stored = await store_evaluation(session, request=request, result=result)
            assert stored.status is EvaluationStatus.finished
            assert stored.run_id == run.id
            assert stored.config_hash == request.config.config_hash()

            loaded = await load_evaluation(session, stored.id)
            assert loaded is not None
            assert loaded.result is not None
            assert loaded.result.verdict is EvaluationVerdict.INCONCLUSIVE
            assert loaded.result.judge_verdict is None
            assert len(loaded.result.score.criteria) == len(Criterion)
            assert loaded.result.score.profile == "default"
            assert any("no judge assessed" in item for item in loaded.result.limitations)

    asyncio.run(scenario())


def test_a_judged_evaluation_stores_its_verdict_findings_and_usage() -> None:
    require_postgres()

    async def scenario() -> tuple[EvaluationResult, EvaluationRun, int]:
        profile = load_profile(PROFILE_PATH)
        async with rolled_back_session() as session:
            run = await add_verified_run(session)
            request = await build_request(session, run_id=run.id, corpus_root=CORPUS)
            result = assemble_result(
                request,
                profile=profile,
                judge_verdict=EvaluationVerdict.ACCEPTED,
                judge_scores={criterion: 90.0 for criterion in Criterion},
                assessments=(judge_assessment(),),
                confidence=0.85,
            )
            stored = await store_evaluation(session, request=request, result=result)
            loaded = await load_evaluation(session, stored.id)
            assert loaded is not None and loaded.result is not None
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(EvaluationResultRow)
                    .where(EvaluationResultRow.evaluation_run_id == stored.id)
                )
            ).scalar_one()
            return loaded.result, stored, int(count)

    result, stored, result_rows = asyncio.run(scenario())

    assert result.verdict is EvaluationVerdict.ACCEPTED
    assert result.judge_verdict is EvaluationVerdict.ACCEPTED
    assert result.score.overall == 90.0
    assert stored.tokens_in == 1200
    assert stored.tokens_out == 300
    assert stored.cost_list_usd == Decimal("0.0042")
    assert result_rows == 1


def test_a_failed_check_is_stored_as_a_confirmed_finding() -> None:
    require_postgres()

    async def scenario() -> tuple[EvaluationResult, list[EvaluationRun]]:
        profile = load_profile(PROFILE_PATH)
        async with rolled_back_session() as session:
            run = await add_verified_run(session)
            verdict = await session.get(Verdict, run.id)
            assert verdict is not None
            verdict.check_results = stored_outcomes(
                (
                    CheckOutcome(
                        command="pytest tests",
                        tool=CheckTool.pytest,
                        exit_code=1,
                        output="1 failed",
                    ),
                    OUTCOMES[3],
                )
            )
            await session.flush()
            request = await build_request(session, run_id=run.id, corpus_root=CORPUS)
            result = assemble_result(
                request,
                profile=profile,
                judge_verdict=EvaluationVerdict.ACCEPTED,
                judge_scores={criterion: 95.0 for criterion in Criterion},
                confidence=0.9,
            )
            stored = await store_evaluation(session, request=request, result=result)
            findings = list(
                (
                    await session.execute(
                        select(EvaluationFinding).where(
                            EvaluationFinding.evaluation_run_id == stored.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(findings) == 1
            loaded = await load_evaluation(session, stored.id)
            assert loaded is not None and loaded.result is not None
            return loaded.result, await evaluations_for_run(session, run.id)

    result, evaluations = asyncio.run(scenario())

    assert result.verdict is EvaluationVerdict.REJECTED
    assert result.judge_verdict is EvaluationVerdict.ACCEPTED
    assert result.findings[0].confirmed is True
    assert result.findings[0].evidence_ref == "check:pytest tests"
    assert len(evaluations) == 1


def test_re_evaluating_the_same_run_adds_a_new_row_instead_of_overwriting() -> None:
    require_postgres()

    async def scenario() -> list[EvaluationRun]:
        profile = load_profile(PROFILE_PATH)
        async with rolled_back_session() as session:
            run = await add_verified_run(session)
            request = await build_request(session, run_id=run.id, corpus_root=CORPUS)
            first = assemble_result(request, profile=profile)
            await store_evaluation(session, request=request, result=first)
            second = assemble_result(
                request,
                profile=profile,
                judge_verdict=EvaluationVerdict.INCONCLUSIVE,
                confidence=0.1,
            )
            await store_evaluation(session, request=request, result=second)
            return await evaluations_for_run(session, run.id)

    evaluations = asyncio.run(scenario())

    assert len(evaluations) == 2
