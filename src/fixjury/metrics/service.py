"""Persisting metric snapshots (PLAN §7, invariant 5).

Metrics are computed once, here, from ``runs`` + ``verdicts`` + ``rejections``,
and stored in ``metric_snapshots`` with the ``config_hash`` and the
``pricing_version`` they were computed under. The report and the page read
snapshots; neither recomputes anything.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fixjury.metrics.compute import Attempt, as_payload, compute_metrics
from fixjury.models import CostBasis, MetricSnapshot, Run, RunStatus, Verdict


class MetricsError(RuntimeError):
    """The snapshot cannot be computed."""


def _duration(run: Run) -> float | None:
    if run.started_at is None or run.finished_at is None:
        return None
    return (run.finished_at - run.started_at).total_seconds()


def attempts_from_rows(rows: Sequence[tuple[Run, Verdict | None]]) -> list[Attempt]:
    """Flatten the join into the plain rows the metric functions read."""

    attempts: list[Attempt] = []
    for run, verdict in rows:
        attempts.append(
            Attempt(
                task_id=run.task_id,
                agent=run.agent,
                status=run.status.value,
                declared_success=run.declared_success,
                tokens_in=run.tokens_in,
                tokens_out=run.tokens_out,
                cost_list_usd=run.cost_list_usd,
                diff_sha256=run.diff_sha256,
                has_diff=bool(run.diff),
                duration_s=_duration(run),
                automated_pass=None if verdict is None else verdict.automated_pass,
                heldout_pass=None if verdict is None else verdict.heldout_pass,
                policy_pass=None if verdict is None else verdict.policy_pass,
                human_verdict=(
                    None
                    if verdict is None or verdict.human_verdict is None
                    else verdict.human_verdict.value
                ),
            )
        )
    return attempts


def config_hash_of(rows: Sequence[tuple[Run, Verdict | None]]) -> str:
    """Digest of the distinct run configurations behind a snapshot."""

    hashes = sorted({run.config_hash for run, _ in rows})
    return hashlib.sha256("|".join(hashes).encode("utf-8")).hexdigest()


async def compute_snapshot(
    session: AsyncSession,
    *,
    agent: str,
    cost_basis: CostBasis = CostBasis.list_price,
) -> MetricSnapshot:
    """Compute and store one snapshot for one agent."""

    result = await session.execute(
        select(Run, Verdict).outerjoin(Verdict, Verdict.run_id == Run.id).where(Run.agent == agent)
    )
    rows: list[tuple[Run, Verdict | None]] = list(result.all())
    if not rows:
        raise MetricsError(f"no runs for agent {agent!r}")

    attempts = attempts_from_rows(rows)
    report = compute_metrics(attempts, agent=agent)
    # Only finished runs price anything. A queued row still carries the version it
    # will be priced under, but labelling the snapshot with it would announce a
    # price that no run has paid yet — and with no finished run at all there is no
    # price to label, so the payload says null rather than an empty string.
    priced = [(run, verdict) for run, verdict in rows if run.status is RunStatus.finished]
    pricing_versions = sorted({run.pricing_version for run, _ in priced})
    payload = as_payload(
        report,
        cost_basis=cost_basis.value,
        pricing_version=",".join(pricing_versions) if pricing_versions else None,
    )
    snapshot = MetricSnapshot(
        id=uuid.uuid4(),
        agent=agent,
        cost_basis=cost_basis,
        config_hash=config_hash_of(priced),
        payload=payload,
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


async def snapshot_by_id(session: AsyncSession, snapshot_id: uuid.UUID) -> MetricSnapshot | None:
    """One stored snapshot, so a report can be reproduced exactly."""

    return await session.get(MetricSnapshot, snapshot_id)


async def latest_snapshots(
    session: AsyncSession, *, agent: str | None = None
) -> list[MetricSnapshot]:
    """The newest snapshot per agent, newest first."""

    statement = select(MetricSnapshot).order_by(MetricSnapshot.computed_at.desc())
    if agent is not None:
        statement = statement.where(MetricSnapshot.agent == agent)
    rows = (await session.scalars(statement)).all()
    newest: dict[str, MetricSnapshot] = {}
    for row in rows:
        newest.setdefault(row.agent, row)
    return list(newest.values())


def total_cost(rows: Sequence[tuple[Run, Verdict | None]]) -> Decimal:
    """The number the snapshot must agree with (PLAN §11, task 4.4)."""

    return sum((run.cost_list_usd for run, _ in rows), Decimal("0"))
