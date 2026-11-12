from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from fixjury.config import settings
from fixjury.db import make_engine, session_scope
from fixjury.models import HumanVerdict, ReviewBatch, Run, RunStatus, Verdict
from fixjury.review.app import create_app
from fixjury.review.pack import pack_batches
from fixjury.run import ensure_task
from fixjury.tasks.loader import read_task

pytestmark = pytest.mark.integration

CORPUS = Path(__file__).resolve().parents[2] / "benchmarks"


def test_maker() -> async_sessionmaker[AsyncSession]:
    """A maker with no pool: the app serves requests on its own event loop."""

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    return async_sessionmaker(engine, expire_on_commit=False)


DOCUMENT = read_task(CORPUS / "tasks" / "T-001" / "task.yaml")
AGENTS = ("review-test-alpha", "review-test-beta")


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


async def seed(session: AsyncSession) -> None:
    await ensure_task(session, DOCUMENT)
    started = datetime.now(UTC)
    for index, agent in enumerate(AGENTS, start=1):
        run = Run(
            id=uuid.uuid4(),
            task_id=DOCUMENT.id,
            agent=agent,
            agent_version="0",
            model=f"model-{agent}",
            attempt=index,
            status=RunStatus.finished,
            declared_success=True,
            diff=(
                "diff --git a/loguru/_colorizer.py b/loguru/_colorizer.py\n"
                "--- a/loguru/_colorizer.py\n"
                "+++ b/loguru/_colorizer.py\n"
                f"@@ -1 +1 @@\n-x = {index}\n+x = {index + 1}\n"
            ),
            diff_sha256="a" * 64,
            tokens_in=10,
            tokens_out=5,
            sandbox_seconds=Decimal("1"),
            cost_list_usd=Decimal("0.1"),
            pricing_version="unpriced-2026-11",
            config_hash="c" * 64,
            started_at=started,
            finished_at=started,
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
            )
        )
    await session.flush()


async def clean(session: AsyncSession) -> None:
    batches = list((await session.scalars(select(ReviewBatch))).all())
    for batch in batches:
        if batch.task_id == DOCUMENT.id:
            await session.delete(batch)
    runs = list((await session.scalars(select(Run).where(Run.agent.in_(AGENTS)))).all())
    for run in runs:
        verdict = await session.get(Verdict, run.id)
        if verdict is not None:
            await session.delete(verdict)
        await session.flush()
        await session.delete(run)
    await session.flush()


def test_packing_creates_one_blind_batch_per_task() -> None:
    require_postgres()
    maker = test_maker()

    async def scenario() -> ReviewBatch:
        async with session_scope(maker) as session:
            await clean(session)
            await seed(session)
            batches = await pack_batches(session, reviewer="owner", tasks=(DOCUMENT.id,))
            assert len(batches) == 1
            return batches[0]

    batch = asyncio.run(scenario())

    assert batch.task_id == DOCUMENT.id
    assert len(batch.run_ids) == 2
    assert batch.pass_no == 1


def test_packing_twice_does_not_duplicate_a_batch() -> None:
    require_postgres()
    maker = test_maker()

    async def scenario() -> tuple[str, str]:
        async with session_scope(maker) as session:
            await clean(session)
            await seed(session)
            first = (await pack_batches(session, reviewer="owner", tasks=(DOCUMENT.id,)))[0]
            second = (await pack_batches(session, reviewer="owner", tasks=(DOCUMENT.id,)))[0]
            return first.id, second.id

    first_id, second_id = asyncio.run(scenario())

    assert first_id == second_id


def test_the_order_is_stored_not_regenerated() -> None:
    require_postgres()
    maker = test_maker()

    async def scenario() -> tuple[list[uuid.UUID], int]:
        async with session_scope(maker) as session:
            await clean(session)
            await seed(session)
            batch = (await pack_batches(session, reviewer="owner", tasks=(DOCUMENT.id,)))[0]
            return list(batch.run_ids), batch.order_seed

    order, order_seed = asyncio.run(scenario())

    assert order_seed > 0
    assert sorted(order) == sorted(set(order)), "a batch must not repeat a run"


def test_the_page_shows_the_patch_and_never_the_agent() -> None:
    require_postgres()
    maker = test_maker()

    async def prepare() -> str:
        async with session_scope(maker) as session:
            await clean(session)
            await seed(session)
            batch = (await pack_batches(session, reviewer="owner", tasks=(DOCUMENT.id,)))[0]
            return batch.id

    batch_id = asyncio.run(prepare())
    client = TestClient(create_app(maker=maker, corpus_root=CORPUS))

    index = client.get("/")
    page = client.get(f"/batch/{batch_id}")

    assert index.status_code == 200
    assert batch_id in index.text
    assert page.status_code == 200
    assert "loguru/_colorizer.py" in page.text
    for agent in AGENTS:
        assert agent not in page.text
    assert "model-review-test" not in page.text
    assert "0.1" not in page.text, "cost must not leak into the review page"

    asyncio.run(clean_all(maker))


async def clean_all(maker: async_sessionmaker[AsyncSession]) -> None:
    async with session_scope(maker) as session:
        await clean(session)


def test_a_verdict_is_recorded_and_cannot_be_repeated() -> None:
    require_postgres()
    maker = test_maker()

    async def prepare() -> tuple[str, uuid.UUID]:
        async with session_scope(maker) as session:
            await clean(session)
            await seed(session)
            batch = (await pack_batches(session, reviewer="owner", tasks=(DOCUMENT.id,)))[0]
            return batch.id, batch.run_ids[0]

    batch_id, run_id = asyncio.run(prepare())
    client = TestClient(create_app(maker=maker, corpus_root=CORPUS))

    first = client.post(
        f"/batch/{batch_id}/verdict",
        data={"run_id": str(run_id), "verdict": "accept", "seconds": 42},
        follow_redirects=False,
    )
    second = client.post(
        f"/batch/{batch_id}/verdict",
        data={"run_id": str(run_id), "verdict": "accept"},
        follow_redirects=False,
    )

    assert first.status_code == 303
    assert second.status_code == 409

    async def read() -> Verdict:
        async with session_scope(maker) as session:
            verdict = await session.get(Verdict, run_id)
            assert verdict is not None
            return verdict

    verdict = asyncio.run(read())

    assert verdict.human_verdict is HumanVerdict.accept
    assert verdict.review_seconds == 42
    assert verdict.blind is True
    assert verdict.batch_id == batch_id

    asyncio.run(clean_all(maker))


def test_a_rejection_needs_a_reason() -> None:
    require_postgres()
    maker = test_maker()

    async def prepare() -> tuple[str, uuid.UUID]:
        async with session_scope(maker) as session:
            await clean(session)
            await seed(session)
            batch = (await pack_batches(session, reviewer="owner", tasks=(DOCUMENT.id,)))[0]
            return batch.id, batch.run_ids[0]

    batch_id, run_id = asyncio.run(prepare())
    client = TestClient(create_app(maker=maker, corpus_root=CORPUS))

    response = client.post(
        f"/batch/{batch_id}/verdict",
        data={"run_id": str(run_id), "verdict": "reject"},
        follow_redirects=False,
    )

    assert response.status_code == 400

    async def cleanup() -> None:
        async with session_scope(maker) as session:
            await session.execute(delete(Verdict).where(Verdict.run_id == run_id))
            await clean(session)

    asyncio.run(cleanup())


def test_a_run_outside_the_batch_is_refused() -> None:
    require_postgres()
    maker = test_maker()

    async def prepare() -> tuple[str, uuid.UUID]:
        async with session_scope(maker) as session:
            await clean(session)
            await seed(session)
            batch = (await pack_batches(session, reviewer="owner", tasks=(DOCUMENT.id,)))[0]
            return batch.id, uuid.uuid4()

    batch_id, stranger = asyncio.run(prepare())
    client = TestClient(create_app(maker=maker, corpus_root=CORPUS))

    response = client.post(
        f"/batch/{batch_id}/verdict",
        data={"run_id": str(stranger), "verdict": "accept"},
        follow_redirects=False,
    )

    assert response.status_code == 404
    asyncio.run(clean_all(maker))
