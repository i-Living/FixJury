from __future__ import annotations

import asyncio
import hashlib
import json
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
from fixjury.evaluation.contracts import EvaluationConfiguration, EvaluationResult, JudgeMode
from fixjury.evaluation.scoring import load_profile
from fixjury.evaluation.store import load_evaluation
from fixjury.judge.modes import JudgeSetup
from fixjury.judge.provider import ProviderResponse
from fixjury.judge.service import INDEPENDENCE_NOTE, evaluate_run
from fixjury.models import (
    Criterion,
    EvaluationRun,
    EvaluationVerdict,
    JudgeAssessment,
    JudgeConfiguration,
    JudgeEscalation,
    Run,
    RunStatus,
    Verdict,
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
        command="pytest tests_heldout",
        tool=CheckTool.pytest,
        exit_code=0,
        output="3 passed",
        heldout=True,
    ),
)


class FakeProvider:
    name = "fake-provider"

    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.models: list[str] = []

    async def evaluate(
        self, *, model: str, system: str, user: str, timeout_s: float
    ) -> ProviderResponse:
        self.models.append(model)
        return ProviderResponse(
            text=self.replies.pop(0), model=model, tokens_in=800, tokens_out=200, latency_ms=1500
        )


def reply(*, verdict: str = "ACCEPTED", confidence: float = 0.9) -> str:
    return json.dumps(
        {
            "verdict": verdict,
            "confidence": confidence,
            "criteria": [
                {"criterion": criterion.value, "score": 88, "rationale": "checked"}
                for criterion in Criterion
            ],
            "findings": [],
            "reasoning_summary": "the diff replaces the broken call and keeps the tests green",
            "limitations": [],
            "escalation_required": False,
        }
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


async def add_verified_run(session: AsyncSession, *, task_id: str = "T-001") -> Run:
    document = read_task(CORPUS / "tasks" / task_id / "task.yaml")
    await ensure_task(session, document)
    started = datetime.now(UTC)
    run = Run(
        id=uuid.uuid4(),
        task_id=task_id,
        agent="judge-test",
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


def setup(provider: FakeProvider, *, escalation_model: str = "") -> JudgeSetup:
    return JudgeSetup(
        provider=provider, primary_model="strong-model", escalation_model=escalation_model
    )


def test_a_single_judge_evaluation_is_stored_with_its_assessment() -> None:
    require_postgres()

    async def scenario() -> tuple[EvaluationResult, EvaluationRun, int, int, int]:
        async with rolled_back_session() as session:
            run = await add_verified_run(session)
            judged = await evaluate_run(
                session,
                run_id=run.id,
                corpus_root=CORPUS,
                config=EvaluationConfiguration(mode=JudgeMode.single),
                setup=setup(FakeProvider([reply()])),
                profile=load_profile(PROFILE_PATH),
            )
            assessments = (
                await session.execute(
                    select(func.count())
                    .select_from(JudgeAssessment)
                    .where(JudgeAssessment.evaluation_run_id == judged.evaluation.id)
                )
            ).scalar_one()
            escalations = (
                await session.execute(
                    select(func.count())
                    .select_from(JudgeEscalation)
                    .where(JudgeEscalation.evaluation_run_id == judged.evaluation.id)
                )
            ).scalar_one()
            configurations = (
                await session.execute(select(func.count()).select_from(JudgeConfiguration))
            ).scalar_one()
            loaded = await load_evaluation(session, judged.evaluation.id)
            assert loaded is not None and loaded.result is not None
            return (
                judged.result,
                judged.evaluation,
                int(assessments),
                int(escalations),
                int(configurations),
            )

    result, evaluation, assessments, escalations, configurations = asyncio.run(scenario())

    assert result.verdict is EvaluationVerdict.ACCEPTED
    assert result.judge_verdict is EvaluationVerdict.ACCEPTED
    assert result.score.overall == 88.0
    assert INDEPENDENCE_NOTE in result.limitations
    assert evaluation.judge_config_hash is not None
    assert assessments == 1
    assert escalations == 0
    assert configurations == 1


def test_a_low_confidence_answer_escalates_to_a_second_judge() -> None:
    require_postgres()

    async def scenario() -> tuple[int, int]:
        async with rolled_back_session() as session:
            run = await add_verified_run(session)
            judged = await evaluate_run(
                session,
                run_id=run.id,
                corpus_root=CORPUS,
                config=EvaluationConfiguration(mode=JudgeMode.adaptive),
                setup=setup(
                    FakeProvider([reply(confidence=0.2), reply(confidence=0.95)]),
                    escalation_model="second-model",
                ),
                profile=load_profile(PROFILE_PATH),
            )
            assessments = (
                await session.execute(
                    select(func.count())
                    .select_from(JudgeAssessment)
                    .where(JudgeAssessment.evaluation_run_id == judged.evaluation.id)
                )
            ).scalar_one()
            escalated = list(
                (
                    await session.execute(
                        select(JudgeEscalation).where(
                            JudgeEscalation.evaluation_run_id == judged.evaluation.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(escalated) == 2
            # Every round is recorded, including the one that decided to stop.
            assert [row.escalated for row in escalated] == [True, False]
            assert escalated[0].triggers
            return int(assessments), len(judged.assessments)

    assessments, judges = asyncio.run(scenario())

    assert assessments == 2
    assert judges == 2


def test_a_second_judge_rejection_rejects_the_patch() -> None:
    require_postgres()

    async def scenario() -> EvaluationResult:
        async with rolled_back_session() as session:
            run = await add_verified_run(session)
            judged = await evaluate_run(
                session,
                run_id=run.id,
                corpus_root=CORPUS,
                config=EvaluationConfiguration(mode=JudgeMode.adaptive),
                setup=setup(
                    FakeProvider([reply(confidence=0.3), reply(verdict="REJECTED", confidence=0.9)])
                ),
                profile=load_profile(PROFILE_PATH),
            )
            return judged.result

    result = asyncio.run(scenario())

    assert result.verdict is EvaluationVerdict.REJECTED
    assert result.judge_verdict is EvaluationVerdict.REJECTED
    assert result.escalated is True


def test_a_failed_check_rejects_even_a_unanimous_acceptance() -> None:
    require_postgres()

    async def scenario() -> EvaluationResult:
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
                    OUTCOMES[1],
                )
            )
            await session.flush()
            judged = await evaluate_run(
                session,
                run_id=run.id,
                corpus_root=CORPUS,
                config=EvaluationConfiguration(mode=JudgeMode.single),
                setup=setup(FakeProvider([reply()])),
                profile=load_profile(PROFILE_PATH),
            )
            return judged.result

    result = asyncio.run(scenario())

    assert result.verdict is EvaluationVerdict.REJECTED
    assert result.judge_verdict is EvaluationVerdict.ACCEPTED
    assert any(finding.confirmed for finding in result.findings)
