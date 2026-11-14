"""JSON routes over the rows the platform wrote (PLAN §2, §9).

Two rules shape this module. It never computes a metric: a snapshot is served as
it was stored, payload included, because invariant 5 says the numbers are
computed once. And it never writes: every route is a GET, so no browser, agent
or adapter can record a verdict, a snapshot or a run through the API.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select, text

from fixjury.db import SessionMaker, session_scope
from fixjury.metrics.service import latest_snapshots, snapshot_by_id
from fixjury.models import MetricSnapshot, Rejection, Run, RunStatus, Task, Verdict


class TaskSummary(BaseModel):
    """A corpus task without the issue text, for listings."""

    id: str
    repo_url: str
    license: str
    base_commit: str
    fix_commit: str | None
    issue_number: int
    issue_url: str
    issue_created_at: date
    title: str
    difficulty: str
    heldout_ref: str
    frozen_at: datetime


class TaskDetail(TaskSummary):
    body: str
    # The frozen RepairSpec document as stored in JSONB: the shape belongs to
    # ``tasks/schema.py`` and changes with it, so the API serves it unchanged.
    spec: dict[str, Any]
    cutoff_assumption: str
    fix_merged_at: date | None


class VerdictOut(BaseModel):
    tests_pass: bool
    lint_pass: bool
    type_pass: bool
    policy_pass: bool
    heldout_pass: bool | None
    automated_pass: bool
    tool_versions: dict[str, str]
    reviewer_verdict: str | None
    reviewer_reasons: list[str]
    reviewer_model: str | None
    human_verdict: str | None
    human_reasons: list[str]
    human_resolution: str | None
    human_reviewer: str | None
    review_seconds: int | None
    blind: bool
    batch_id: str | None
    reviewed_at: datetime | None


class RejectionOut(BaseModel):
    source: str
    code: str
    note: str | None


class RunSummary(BaseModel):
    """A run without its diff: the listing stays readable."""

    id: uuid.UUID
    task_id: str
    agent: str
    agent_version: str
    model: str
    attempt: int
    parent_run_id: uuid.UUID | None
    status: str
    declared_success: bool | None
    unable_reason: str | None
    diff_sha256: str | None
    tokens_in: int
    tokens_out: int
    sandbox_seconds: Decimal
    cost_list_usd: Decimal
    pricing_version: str
    config_hash: str
    trace_id: str | None
    started_at: datetime | None
    finished_at: datetime | None


class RunDetail(RunSummary):
    diff: str | None
    verdict: VerdictOut | None
    rejections: list[RejectionOut]


class SnapshotOut(BaseModel):
    id: uuid.UUID
    agent: str
    cost_basis: str
    config_hash: str
    computed_at: datetime
    # Stored payload, served as computed. The API never recomputes a metric.
    payload: dict[str, Any]


class TaskList(BaseModel):
    tasks: list[TaskSummary]
    count: int


class RunList(BaseModel):
    runs: list[RunSummary]
    count: int
    total: int


class SnapshotList(BaseModel):
    snapshots: list[SnapshotOut]
    count: int


def task_summary(task: Task) -> TaskSummary:
    return TaskSummary(
        id=task.id,
        repo_url=task.repo_url,
        license=task.license,
        base_commit=task.base_commit,
        fix_commit=task.fix_commit,
        issue_number=task.issue_number,
        issue_url=task.issue_url,
        issue_created_at=task.issue_created_at,
        title=task.title,
        difficulty=task.difficulty.value,
        heldout_ref=task.heldout_ref,
        frozen_at=task.frozen_at,
    )


def task_detail(task: Task) -> TaskDetail:
    summary = task_summary(task)
    return TaskDetail(
        **summary.model_dump(),
        body=task.body,
        spec=task.spec,
        cutoff_assumption=task.cutoff_assumption.value,
        fix_merged_at=task.fix_merged_at,
    )


def run_summary(run: Run) -> RunSummary:
    return RunSummary(
        id=run.id,
        task_id=run.task_id,
        agent=run.agent,
        agent_version=run.agent_version,
        model=run.model,
        attempt=run.attempt,
        parent_run_id=run.parent_run_id,
        status=run.status.value,
        declared_success=run.declared_success,
        unable_reason=run.unable_reason,
        diff_sha256=run.diff_sha256,
        tokens_in=run.tokens_in,
        tokens_out=run.tokens_out,
        sandbox_seconds=run.sandbox_seconds,
        cost_list_usd=run.cost_list_usd,
        pricing_version=run.pricing_version,
        config_hash=run.config_hash,
        trace_id=run.trace_id,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def run_detail(run: Run, verdict: Verdict | None, rejections: list[Rejection]) -> RunDetail:
    return RunDetail(
        **run_summary(run).model_dump(),
        diff=run.diff,
        verdict=None if verdict is None else verdict_out(verdict),
        rejections=[
            RejectionOut(source=row.source.value, code=row.code.value, note=row.note)
            for row in rejections
        ],
    )


def verdict_out(verdict: Verdict) -> VerdictOut:
    return VerdictOut(
        tests_pass=verdict.tests_pass,
        lint_pass=verdict.lint_pass,
        type_pass=verdict.type_pass,
        policy_pass=verdict.policy_pass,
        heldout_pass=verdict.heldout_pass,
        automated_pass=verdict.automated_pass,
        tool_versions=verdict.tool_versions,
        reviewer_verdict=None
        if verdict.reviewer_verdict is None
        else verdict.reviewer_verdict.value,
        reviewer_reasons=list(verdict.reviewer_reasons),
        reviewer_model=verdict.reviewer_model,
        human_verdict=None if verdict.human_verdict is None else verdict.human_verdict.value,
        human_reasons=list(verdict.human_reasons),
        human_resolution=None
        if verdict.human_resolution is None
        else verdict.human_resolution.value,
        human_reviewer=verdict.human_reviewer,
        review_seconds=verdict.review_seconds,
        blind=verdict.blind,
        batch_id=verdict.batch_id,
        reviewed_at=verdict.reviewed_at,
    )


def snapshot_out(snapshot: MetricSnapshot) -> SnapshotOut:
    return SnapshotOut(
        id=snapshot.id,
        agent=snapshot.agent,
        cost_basis=snapshot.cost_basis.value,
        config_hash=snapshot.config_hash,
        computed_at=snapshot.computed_at,
        payload=snapshot.payload,
    )


def build_router(maker: SessionMaker) -> APIRouter:
    """Every route closes over the session maker, so the app has no globals."""

    router = APIRouter()

    @router.get("/health")
    async def health() -> dict[str, str]:
        try:
            async with session_scope(maker) as session:
                await session.execute(text("SELECT 1"))
        except Exception as exc:  # the probe answers 503 instead of a stack trace
            raise HTTPException(status_code=503, detail="database is unreachable") from exc
        return {"status": "ok"}

    @router.get("/tasks", response_model=TaskList)
    async def list_tasks() -> TaskList:
        async with session_scope(maker) as session:
            tasks = list((await session.scalars(select(Task).order_by(Task.id))).all())
            summaries = [task_summary(task) for task in tasks]
        return TaskList(tasks=summaries, count=len(summaries))

    @router.get("/tasks/{task_id}", response_model=TaskDetail)
    async def get_task(task_id: str) -> TaskDetail:
        async with session_scope(maker) as session:
            task = await session.get(Task, task_id)
            if task is None:
                raise HTTPException(status_code=404, detail=f"no task {task_id}")
            return task_detail(task)

    @router.get("/runs", response_model=RunList)
    async def list_runs(
        task: Annotated[str | None, Query(description="Filter by task id.")] = None,
        agent: Annotated[str | None, Query(description="Filter by agent.")] = None,
        status: Annotated[RunStatus | None, Query(description="Filter by run status.")] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> RunList:
        conditions = []
        if task is not None:
            conditions.append(Run.task_id == task)
        if agent is not None:
            conditions.append(Run.agent == agent)
        if status is not None:
            conditions.append(Run.status == status)

        async with session_scope(maker) as session:
            statement = select(Run).where(*conditions)
            rows = list(
                (
                    await session.scalars(
                        statement.order_by(Run.task_id, Run.agent, Run.attempt, Run.id)
                        .limit(limit)
                        .offset(offset)
                    )
                ).all()
            )
            total = await session.scalar(select(func.count()).select_from(Run).where(*conditions))
            summaries = [run_summary(run) for run in rows]
        return RunList(runs=summaries, count=len(summaries), total=int(total or 0))

    @router.get("/runs/{run_id}", response_model=RunDetail)
    async def get_run(run_id: uuid.UUID) -> RunDetail:
        async with session_scope(maker) as session:
            run = await session.get(Run, run_id)
            if run is None:
                raise HTTPException(status_code=404, detail=f"no run {run_id}")
            verdict = await session.get(Verdict, run.id)
            rejections = list(
                (
                    await session.scalars(
                        select(Rejection).where(Rejection.run_id == run.id).order_by(Rejection.id)
                    )
                ).all()
            )
            return run_detail(run, verdict, rejections)

    @router.get("/snapshots", response_model=SnapshotList)
    async def list_snapshots() -> SnapshotList:
        async with session_scope(maker) as session:
            snapshots = [snapshot_out(row) for row in await latest_snapshots(session)]
        return SnapshotList(snapshots=snapshots, count=len(snapshots))

    @router.get("/snapshots/{snapshot_id}", response_model=SnapshotOut)
    async def get_snapshot(snapshot_id: uuid.UUID) -> SnapshotOut:
        async with session_scope(maker) as session:
            snapshot = await snapshot_by_id(session, snapshot_id)
            if snapshot is None:
                raise HTTPException(status_code=404, detail=f"no snapshot {snapshot_id}")
            return snapshot_out(snapshot)

    return router
