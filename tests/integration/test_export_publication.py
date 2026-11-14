from __future__ import annotations

import asyncio
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
from fixjury.export import (
    MANIFEST_FILENAME,
    REJECTIONS_FILENAME,
    RUNS_FILENAME,
    VERDICTS_FILENAME,
    export_rows,
    file_sha256,
)
from fixjury.models import (
    HumanVerdict,
    Rejection,
    RejectionCode,
    RejectionSource,
    Run,
    RunStatus,
    Verdict,
)

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
    agent: str = "export-test",
    human: HumanVerdict | None = None,
) -> Run:
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
        attempt=1,
        status=RunStatus.finished,
        declared_success=True,
        diff="diff --git a/x b/x\n+patched\n",
        diff_sha256="a" * 64,
        tokens_in=100,
        tokens_out=10,
        sandbox_seconds=Decimal("12.50"),
        cost_list_usd=Decimal("0.2500"),
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
            human_verdict=human,
        )
    )
    await session.flush()
    return run


async def table_count(session: AsyncSession, table: type[Run] | type[Verdict]) -> int:
    counted = await session.scalar(select(func.count()).select_from(table))
    return int(counted or 0)


def test_the_export_reproduces_the_database_and_hashes_its_files(tmp_path: Path) -> None:
    require_postgres()

    async def scenario() -> tuple[dict[str, object], dict[str, int], uuid.UUID, uuid.UUID]:
        async with rolled_back_session() as session:
            accepted = await add_run(session, task_id="T-001", human=HumanVerdict.accept)
            rejected = await add_run(session, task_id="T-002")
            session.add(
                Rejection(
                    run_id=rejected.id,
                    source=RejectionSource.human,
                    code=RejectionCode.WRONG_ROOT_CAUSE,
                    note="symptom only",
                )
            )
            await session.flush()
            manifest = await export_rows(session, tmp_path)
            counts = {
                RUNS_FILENAME: await table_count(session, Run),
                VERDICTS_FILENAME: await table_count(session, Verdict),
            }
            return manifest, counts, accepted.id, rejected.id

    manifest, counts, accepted_id, rejected_id = asyncio.run(scenario())

    files = manifest["files"]
    assert isinstance(files, dict)
    assert manifest["verified"] is True
    for filename, count in counts.items():
        entry = files[filename]
        assert isinstance(entry, dict)
        assert entry["rows"] == count
        assert entry["sha256"] == file_sha256(tmp_path / filename)

    rejections = [
        json.loads(line)
        for line in (tmp_path / REJECTIONS_FILENAME).read_text(encoding="utf-8").splitlines()
    ]
    ours = [row for row in rejections if row["run_id"] == str(rejected_id)]
    assert [row["code"] for row in ours] == ["WRONG_ROOT_CAUSE"]

    runs = [json.loads(line) for line in (tmp_path / RUNS_FILENAME).read_text().splitlines()]
    exported = [row for row in runs if row["agent"] == "export-test"]
    assert len(exported) == 2
    assert {row["task_id"] for row in exported} == {"T-001", "T-002"}

    verdicts = [
        json.loads(line) for line in (tmp_path / VERDICTS_FILENAME).read_text().splitlines()
    ]
    accepted = [row for row in verdicts if row["run_id"] == str(accepted_id)]
    assert [row["human_verdict"] for row in accepted] == ["accept"]


def test_two_exports_of_one_database_are_byte_identical(tmp_path: Path) -> None:
    require_postgres()
    first = tmp_path / "first"
    second = tmp_path / "second"

    async def scenario() -> None:
        async with rolled_back_session() as session:
            await add_run(session, task_id="T-003")
            await export_rows(session, first)
            await export_rows(session, second)

    asyncio.run(scenario())

    assert (first / RUNS_FILENAME).read_bytes() == (second / RUNS_FILENAME).read_bytes()
    assert (first / VERDICTS_FILENAME).read_bytes() == (second / VERDICTS_FILENAME).read_bytes()
    assert (first / MANIFEST_FILENAME).is_file()
