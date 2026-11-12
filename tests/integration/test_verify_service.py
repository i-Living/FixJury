from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fixjury.db import make_engine
from fixjury.models import Rejection, RejectionCode, RejectionSource, Run, RunStatus, Verdict
from fixjury.tasks.loader import read_task
from fixjury.tasks.schema import TaskDocument
from fixjury.verify import service
from fixjury.verify.policy import PolicyResult, Violation
from fixjury.verify.verdict import VerificationResult, assemble

CORPUS = Path(__file__).resolve().parents[2] / "benchmarks"
DOCUMENT = read_task(CORPUS / "tasks" / "T-001" / "task.yaml")

pytestmark = pytest.mark.integration


@asynccontextmanager
async def rolled_back_session() -> AsyncIterator[AsyncSession]:
    """A session whose work is rolled back, so the developer database stays clean."""

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


RESULT = assemble(
    (),
    policy=PolicyResult(
        passed=False,
        violations=(Violation(RejectionCode.EXTRA_FILES, "outside allowed_paths"),),
        files=(),
    ),
    heldout_pass=False,
    tool_versions={"pytest": "8.3.0"},
)


async def store_run(session: AsyncSession, attempt: int, diff: str | None) -> Run:
    run = Run(
        id=uuid.uuid4(),
        task_id=DOCUMENT.id,
        agent="fake",
        agent_version="0",
        model="stub",
        attempt=attempt,
        status=RunStatus.finished,
        declared_success=True,
        diff=diff,
        diff_sha256="a" * 64 if diff else None,
        tokens_in=1,
        tokens_out=1,
        sandbox_seconds=Decimal("0.10"),
        cost_list_usd=Decimal("0"),
        pricing_version="unpriced",
        config_hash="c" * 64,
    )
    session.add(run)
    await session.flush()
    return run


def test_verifying_twice_keeps_one_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    require_postgres()

    async def fake_verify(
        document: TaskDocument,
        diff: str,
        *,
        workspace: Path,
        image: str,
        heldout_bundle: Path | None = None,
    ) -> VerificationResult:
        return RESULT

    monkeypatch.setattr(service, "verify_workspace", fake_verify)

    async def scenario() -> tuple[Verdict, int, int]:
        async with rolled_back_session() as session:
            run = await store_run(session, attempt=11, diff="diff --git a/x b/x\n")
            first = await service.verify_run(session, run.id, corpus_root=CORPUS)
            second = await service.verify_run(session, run.id, corpus_root=CORPUS)
            verdicts = await session.scalar(
                select(func.count()).select_from(Verdict).where(Verdict.run_id == run.id)
            )
            rejections = await session.scalar(
                select(func.count()).select_from(Rejection).where(Rejection.run_id == run.id)
            )
            assert first.run_id == second.run_id == run.id
            assert first.automated_pass is False
            assert first.heldout_pass is False
            assert first.tool_versions == {"pytest": "8.3.0"}
            return first, int(verdicts or 0), int(rejections or 0)

    verdict, verdicts, rejections = asyncio.run(scenario())

    assert verdict.automated_pass is False
    assert verdicts == 1, "a second verification must replace the verdict, not add one"
    assert rejections == 2, "one rejection per violation, not one per verification"


def test_a_run_without_a_diff_is_not_verified_in_a_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    require_postgres()
    called = {"count": 0}

    async def fake_verify(
        document: TaskDocument,
        diff: str,
        *,
        workspace: Path,
        image: str,
        heldout_bundle: Path | None = None,
    ) -> VerificationResult:
        called["count"] += 1
        return RESULT

    monkeypatch.setattr(service, "verify_workspace", fake_verify)

    async def scenario() -> Verdict:
        async with rolled_back_session() as session:
            run = await store_run(session, attempt=12, diff=None)
            return await service.verify_run(session, run.id, corpus_root=CORPUS)

    verdict = asyncio.run(scenario())

    assert called["count"] == 0
    assert verdict.tests_pass is False
    assert verdict.automated_pass is False


def test_an_unknown_run_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    require_postgres()

    async def scenario() -> None:
        async with rolled_back_session() as session:
            with pytest.raises(service.VerifyError, match="does not exist"):
                await service.verify_run(session, uuid.uuid4(), corpus_root=CORPUS)

    asyncio.run(scenario())


def test_rejections_are_stored_as_automated(monkeypatch: pytest.MonkeyPatch) -> None:
    require_postgres()

    async def fake_verify(
        document: TaskDocument,
        diff: str,
        *,
        workspace: Path,
        image: str,
        heldout_bundle: Path | None = None,
    ) -> VerificationResult:
        return RESULT

    monkeypatch.setattr(service, "verify_workspace", fake_verify)

    async def scenario() -> list[Rejection]:
        async with rolled_back_session() as session:
            run = await store_run(session, attempt=13, diff="diff --git a/x b/x\n")
            await service.verify_run(session, run.id, corpus_root=CORPUS)
            rows = await session.scalars(select(Rejection).where(Rejection.run_id == run.id))
            return list(rows)

    rows = asyncio.run(scenario())

    assert {row.code for row in rows} == {RejectionCode.EXTRA_FILES, RejectionCode.INCOMPLETE_FIX}
    assert {row.source for row in rows} == {RejectionSource.automated}


def test_the_result_helper_is_wired_to_the_same_assemble() -> None:
    assert isinstance(RESULT, VerificationResult)
