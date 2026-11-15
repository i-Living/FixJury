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
from fixjury.evaluation.gates import evaluate_gates, load_policy
from fixjury.evaluation.scoring import load_profile
from fixjury.evaluation.service import build_request
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
GATES_PATH = Path(__file__).resolve().parents[2] / "config" / "quality.yaml"
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
    assert any(note.startswith(INDEPENDENCE_NOTE) for note in result.limitations)
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


def test_a_scoring_failure_keeps_the_usage_of_the_calls_that_ran() -> None:
    """A failed judgement still cost tokens, and the failure row has to say so."""

    require_postgres()

    async def scenario() -> tuple[str, int, int, Decimal, list[dict[str, object]]]:
        async with rolled_back_session() as session:
            run = await add_verified_run(session)
            with pytest.raises(Exception) as failure:
                await evaluate_run(
                    session,
                    run_id=run.id,
                    corpus_root=CORPUS,
                    # A profile that does not exist fails after the judge has already answered.
                    config=EvaluationConfiguration(profile="no-such-profile"),
                    setup=setup(FakeProvider([reply()])),
                )
            row = (
                await session.execute(select(EvaluationRun).where(EvaluationRun.run_id == run.id))
            ).scalar_one()
            return (
                str(failure.value),
                row.tokens_in,
                row.tokens_out,
                row.cost_list_usd,
                list(row.assessments),
            )

    message, tokens_in, tokens_out, cost, assessments = asyncio.run(scenario())

    assert "profile" in message
    assert (tokens_in, tokens_out) == (800, 200)
    assert len(assessments) == 1, "the assessment that ran is kept, not dropped"
    # The row's money is the sum of the assessments it kept. `strong-model` has no list
    # price in the pinned table, so the number can be zero — but it is the same zero the
    # assessment reported, not a total invented at the store.
    assert Decimal(str(assessments[0]["cost_list_usd"])) == cost


def test_a_stored_evaluation_passes_the_shipped_quality_gates() -> None:
    """The gates read what the judge and the checks recorded, end to end."""

    require_postgres()

    async def scenario() -> tuple[str, str, list[str]]:
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
            request = await build_request(
                session,
                run_id=run.id,
                corpus_root=CORPUS,
                config=EvaluationConfiguration.model_validate(judged.evaluation.config),
            )
            report = evaluate_gates(
                load_policy(GATES_PATH),
                request=request,
                result=judged.result,
                evaluation_run_id=str(judged.evaluation.id),
            )
            return (
                report.outcome.value,
                judged.result.verdict.value,
                [f"{item.rule}={item.outcome.value}" for item in report.results],
            )

    outcome, verdict, rules = asyncio.run(scenario())

    assert verdict == "ACCEPTED"
    assert outcome == "PASS", rules
    assert "held-out=PASS" in rules
    assert not [rule for rule in rules if rule.endswith("FAIL")]
