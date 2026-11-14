from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from fixjury.api.app import create_app
from fixjury.config import settings
from fixjury.db import make_engine, session_scope
from fixjury.metrics.service import compute_snapshot
from fixjury.models import (
    HumanVerdict,
    MetricSnapshot,
    Rejection,
    RejectionCode,
    RejectionSource,
    Run,
    RunStatus,
    Verdict,
)
from fixjury.run import ensure_task
from fixjury.tasks.loader import read_task

pytestmark = pytest.mark.integration

CORPUS = Path(__file__).resolve().parents[2] / "benchmarks"
DOCUMENT = read_task(CORPUS / "tasks" / "T-001" / "task.yaml")
AGENTS = ("api-test-alpha", "api-test-beta")


def api_test_maker() -> async_sessionmaker[AsyncSession]:
    """A maker with no pool: the app serves requests on its own event loop."""

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    return async_sessionmaker(engine, expire_on_commit=False)


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


async def clean(session: AsyncSession) -> None:
    runs = list((await session.scalars(select(Run).where(Run.agent.in_(AGENTS)))).all())
    for run in runs:
        for rejection in (
            await session.scalars(select(Rejection).where(Rejection.run_id == run.id))
        ).all():
            await session.delete(rejection)
        verdict = await session.get(Verdict, run.id)
        if verdict is not None:
            await session.delete(verdict)
        await session.flush()
        await session.delete(run)
    for snapshot in (
        await session.scalars(select(MetricSnapshot).where(MetricSnapshot.agent.in_(AGENTS)))
    ).all():
        await session.delete(snapshot)
    await session.flush()


async def seed(session: AsyncSession) -> dict[str, uuid.UUID]:
    await ensure_task(session, DOCUMENT)
    started = datetime.now(UTC)
    identifiers: dict[str, uuid.UUID] = {}
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
            diff=f"diff --git a/x b/x\n+value = {index}\n",
            diff_sha256="b" * 64,
            tokens_in=100,
            tokens_out=10,
            sandbox_seconds=Decimal("12.5"),
            cost_list_usd=Decimal("0.25"),
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
                human_verdict=HumanVerdict.accept if index == 1 else None,
                human_reviewer="owner" if index == 1 else None,
            )
        )
        identifiers[agent] = run.id
    await session.flush()
    session.add(
        Rejection(
            run_id=identifiers[AGENTS[1]],
            source=RejectionSource.automated,
            code=RejectionCode.NO_REGRESSION_TEST,
            note="no test pins the fix",
        )
    )
    await session.flush()
    return identifiers


def seed_data() -> dict[str, uuid.UUID]:
    maker = api_test_maker()

    async def scenario() -> dict[str, uuid.UUID]:
        async with session_scope(maker) as session:
            await clean(session)
            return await seed(session)

    return asyncio.run(scenario())


def clean_data() -> None:
    maker = api_test_maker()

    async def scenario() -> None:
        async with session_scope(maker) as session:
            await clean(session)

    asyncio.run(scenario())


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(maker=api_test_maker()))


def test_health_answers_when_the_database_is_reachable(client: TestClient) -> None:
    require_postgres()

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_the_api_has_no_write_route() -> None:
    """Nothing may record a run, a verdict or a snapshot through the API."""

    app = create_app(maker=api_test_maker())

    methods = {method for route in app.routes for method in getattr(route, "methods", set())}

    assert methods <= {"GET", "HEAD", "OPTIONS"}


def test_the_task_list_serves_the_frozen_corpus(client: TestClient) -> None:
    require_postgres()

    response = client.get("/api/v1/tasks")

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] >= 32
    identifiers = [task["id"] for task in payload["tasks"]]
    assert DOCUMENT.id in identifiers
    listed = next(task for task in payload["tasks"] if task["id"] == DOCUMENT.id)
    assert listed["title"] == DOCUMENT.title
    assert "body" not in listed, "the listing stays small; the body belongs to the detail"


def test_a_task_detail_carries_the_body_and_the_spec(client: TestClient) -> None:
    require_postgres()

    response = client.get(f"/api/v1/tasks/{DOCUMENT.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == DOCUMENT.title
    assert payload["body"] == (DOCUMENT.issue.body or "")
    assert isinstance(payload["spec"], dict) and payload["spec"]


def test_an_unknown_task_is_not_found(client: TestClient) -> None:
    require_postgres()

    assert client.get("/api/v1/tasks/T-999").status_code == 404


def test_runs_can_be_filtered_and_carry_no_diff_in_the_listing(client: TestClient) -> None:
    require_postgres()
    identifiers = seed_data()
    try:
        response = client.get("/api/v1/runs", params={"agent": AGENTS[0]})

        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 1
        assert payload["count"] == 1
        run = payload["runs"][0]
        assert run["id"] == str(identifiers[AGENTS[0]])
        assert run["status"] == "finished"
        assert "diff" not in run
    finally:
        clean_data()


def test_runs_can_be_filtered_by_status_and_paginated(client: TestClient) -> None:
    require_postgres()
    seed_data()
    try:
        finished = client.get("/api/v1/runs", params={"status": "finished", "agent": AGENTS[0]})
        queued = client.get("/api/v1/runs", params={"status": "queued", "agent": AGENTS[0]})
        page = client.get("/api/v1/runs", params={"agent": AGENTS[1], "limit": 1, "offset": 0})

        assert finished.json()["total"] == 1
        assert queued.json()["total"] == 0
        assert page.json()["count"] == 1
    finally:
        clean_data()


def test_an_invalid_status_is_refused(client: TestClient) -> None:
    require_postgres()

    response = client.get("/api/v1/runs", params={"status": "nonsense"})

    assert response.status_code == 422


def test_a_run_detail_carries_the_diff_the_verdict_and_the_rejections(
    client: TestClient,
) -> None:
    require_postgres()
    identifiers = seed_data()
    try:
        accepted = client.get(f"/api/v1/runs/{identifiers[AGENTS[0]]}")
        rejected = client.get(f"/api/v1/runs/{identifiers[AGENTS[1]]}")

        assert accepted.status_code == 200
        payload = accepted.json()
        assert payload["diff"].startswith("diff --git")
        assert payload["verdict"]["automated_pass"] is True
        assert payload["verdict"]["human_verdict"] == "accept"
        assert payload["rejections"] == []

        assert rejected.status_code == 200
        codes = [row["code"] for row in rejected.json()["rejections"]]
        assert codes == ["NO_REGRESSION_TEST"]
    finally:
        clean_data()


def test_an_unknown_run_is_not_found(client: TestClient) -> None:
    require_postgres()

    assert client.get(f"/api/v1/runs/{uuid.uuid4()}").status_code == 404


def test_a_snapshot_is_served_exactly_as_it_was_stored(client: TestClient) -> None:
    """Invariant 5: the API reads the payload, it does not recompute a metric."""

    require_postgres()
    seed_data()
    try:
        maker = api_test_maker()

        async def store() -> tuple[uuid.UUID, dict[str, object]]:
            async with session_scope(maker) as session:
                snapshot = await compute_snapshot(session, agent=AGENTS[0])
                return snapshot.id, dict(snapshot.payload)

        identifier, stored = asyncio.run(store())

        listing = client.get("/api/v1/snapshots")
        assert listing.status_code == 200
        served = next(row for row in listing.json()["snapshots"] if row["agent"] == AGENTS[0])
        assert served["id"] == str(identifier)
        assert served["payload"] == stored

        one = client.get(f"/api/v1/snapshots/{identifier}")
        assert one.status_code == 200
        assert one.json()["payload"] == stored
        assert one.json()["cost_basis"] == "list_price"
    finally:
        clean_data()


def test_an_unknown_snapshot_is_not_found(client: TestClient) -> None:
    require_postgres()

    assert client.get(f"/api/v1/snapshots/{uuid.uuid4()}").status_code == 404


def test_the_router_serves_every_list_as_an_envelope(client: TestClient) -> None:
    require_postgres()

    for path, key in (
        ("/api/v1/tasks", "tasks"),
        ("/api/v1/runs", "runs"),
        ("/api/v1/snapshots", "snapshots"),
    ):
        response = client.get(path)
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body, dict)
        assert body["count"] == len(body[key])
