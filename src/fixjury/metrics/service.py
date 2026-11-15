"""Persisting metric snapshots (PLAN §7, invariant 5).

Metrics are computed once, here, from ``runs`` + ``verdicts`` + ``rejections`` (and, from
V1.1, the evaluations that carry the judge's verdict), and stored in ``metric_snapshots``
with the ``config_hash`` and the ``pricing_version`` they were computed under. The report
and the page read snapshots; neither recomputes anything.

A snapshot names its ``verdict_source``. Two series can describe the same corpus — the
reviewer's judgement (V1) and the judge's (V1.1) — and neither is silently mixed into the
other's denominators.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fixjury.metrics.calibration import as_calibration_payload, calibrate
from fixjury.metrics.compute import (
    Attempt,
    VerdictSource,
    as_payload,
    compute_metrics,
    grouped_payload,
)
from fixjury.models import (
    CostBasis,
    EvaluationResult,
    EvaluationRun,
    EvaluationStatus,
    MetricSnapshot,
    Run,
    RunStatus,
    Task,
    Verdict,
)


class MetricsError(RuntimeError):
    """The snapshot cannot be computed."""


@dataclass(frozen=True)
class Judgement:
    """The newest judge evaluation of one run, flattened to what the metrics read."""

    verdict: str
    confidence: float
    verdicts: tuple[str, ...]
    escalated: bool
    tokens_in: int
    tokens_out: int
    cost_list_usd: Decimal
    judge_config_hash: str
    models: tuple[str, ...]
    prompt_versions: tuple[str, ...]


def _duration(run: Run) -> float | None:
    if run.started_at is None or run.finished_at is None:
        return None
    return (run.finished_at - run.started_at).total_seconds()


def attempts_from_rows(
    rows: Sequence[tuple[Run, Verdict | None, Task | None]],
    *,
    judgements: Mapping[uuid.UUID, Judgement] | None = None,
) -> list[Attempt]:
    """Flatten the join into the plain rows the metric functions read."""

    judged = judgements or {}
    attempts: list[Attempt] = []
    for run, verdict, task in rows:
        judgement = judged.get(run.id)
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
                difficulty=(
                    None if task is None or task.difficulty is None else task.difficulty.value
                ),
                judge_verdict=None if judgement is None else judgement.verdict,
                judge_confidence=None if judgement is None else judgement.confidence,
                judge_verdicts=() if judgement is None else judgement.verdicts,
                judge_escalated=False if judgement is None else judgement.escalated,
                judge_evaluations=0 if judgement is None else 1,
                judge_tokens_in=0 if judgement is None else judgement.tokens_in,
                judge_tokens_out=0 if judgement is None else judgement.tokens_out,
                judge_cost_list_usd=(
                    Decimal("0") if judgement is None else judgement.cost_list_usd
                ),
            )
        )
    return attempts


def _judgement_of(run: EvaluationRun, result: EvaluationResult | None) -> Judgement:
    """One evaluation as the metrics read it; assessments carry usage and vote counts."""

    assessments = run.assessments or []
    models = tuple(
        sorted({str(item.get("model", "")) for item in assessments if item.get("model")})
    )
    versions = tuple(
        sorted(
            {
                str(item.get("prompt_version", ""))
                for item in assessments
                if item.get("prompt_version")
            }
        )
    )
    return Judgement(
        verdict="" if result is None else result.verdict.value,
        confidence=0.0 if result is None else float(result.confidence),
        verdicts=tuple(str(item.get("verdict", "")) for item in assessments if item.get("verdict")),
        escalated=bool(result.escalated) if result is not None else False,
        tokens_in=run.tokens_in,
        tokens_out=run.tokens_out,
        cost_list_usd=run.cost_list_usd,
        judge_config_hash=run.judge_config_hash or "",
        models=models,
        prompt_versions=versions,
    )


async def judgements_for(
    session: AsyncSession, run_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Judgement]:
    """The newest finished evaluation per run.

    A run judged twice is one pair judged twice; the newest evaluation speaks for the pair
    and the older one stays in the database as its own audit trail.
    """

    if not run_ids:
        return {}
    rows = await session.execute(
        select(EvaluationRun, EvaluationResult)
        .outerjoin(EvaluationResult, EvaluationResult.evaluation_run_id == EvaluationRun.id)
        .where(
            EvaluationRun.run_id.in_(list(run_ids)),
            EvaluationRun.status == EvaluationStatus.finished,
        )
        .order_by(EvaluationRun.created_at.desc())
    )
    newest: dict[uuid.UUID, Judgement] = {}
    for evaluation, result in rows.all():
        if evaluation.run_id is None or evaluation.run_id in newest:
            continue
        newest[evaluation.run_id] = _judgement_of(evaluation, result)
    return newest


def judge_block(judgements: Mapping[uuid.UUID, Judgement]) -> dict[str, object]:
    """What judging cost and which judges said what, kept apart from the agent's numbers."""

    counts: dict[str, int] = {}
    for judgement in judgements.values():
        counts[judgement.verdict] = counts.get(judgement.verdict, 0) + 1
    return {
        "evaluations": len(judgements),
        "verdicts": counts,
        "escalated": sum(1 for judgement in judgements.values() if judgement.escalated),
        "tokens_total": sum(
            judgement.tokens_in + judgement.tokens_out for judgement in judgements.values()
        ),
        "cost_total": str(
            sum((judgement.cost_list_usd for judgement in judgements.values()), Decimal("0"))
        ),
        "models": sorted({model for item in judgements.values() for model in item.models}),
        "prompt_versions": sorted(
            {version for item in judgements.values() for version in item.prompt_versions}
        ),
        "judge_config_hashes": sorted(
            {item.judge_config_hash for item in judgements.values() if item.judge_config_hash}
        ),
    }


def config_hash_of(rows: Sequence[tuple[Run, Verdict | None, Task | None]]) -> str:
    """Digest of the distinct run configurations behind a snapshot."""

    hashes = sorted({run.config_hash for run, _, _ in rows})
    return hashlib.sha256("|".join(hashes).encode("utf-8")).hexdigest()


async def compute_snapshot(
    session: AsyncSession,
    *,
    agent: str,
    cost_basis: CostBasis = CostBasis.list_price,
    verdict_source: VerdictSource = VerdictSource.judge,
) -> MetricSnapshot:
    """Compute and store one snapshot for one agent."""

    result = await session.execute(
        select(Run, Verdict, Task)
        .outerjoin(Verdict, Verdict.run_id == Run.id)
        .outerjoin(Task, Task.id == Run.task_id)
        .where(Run.agent == agent)
    )
    rows: list[tuple[Run, Verdict | None, Task | None]] = list(result.all())
    if not rows:
        raise MetricsError(f"no runs for agent {agent!r}")

    judgements = await judgements_for(session, [run.id for run, _, _ in rows])
    attempts = attempts_from_rows(rows, judgements=judgements)
    report = compute_metrics(attempts, agent=agent, verdict_source=verdict_source)
    if verdict_source is VerdictSource.judge and report.evaluated_pairs == 0:
        # An all-null snapshot would read as "the judge accepted nothing"; the truth is that
        # nothing was judged, and the message says which command produces the missing rows.
        raise MetricsError(
            f"no judged pairs for agent {agent!r}: run `fixjury judge evaluate --run <id>` on "
            "verified runs, or pass --verdict-source human for the V1 series"
        )
    # Only finished runs price anything. A queued row still carries the version it
    # will be priced under, but labelling the snapshot with it would announce a
    # price that no run has paid yet — and with no finished run at all there is no
    # price to label, so the payload says null rather than an empty string.
    priced = [
        (run, verdict, task) for run, verdict, task in rows if run.status is RunStatus.finished
    ]
    pricing_versions = sorted({run.pricing_version for run, _, _ in priced})
    payload = as_payload(
        report,
        cost_basis=cost_basis.value,
        pricing_version=",".join(pricing_versions) if pricing_versions else None,
    )
    if judgements:
        payload["judge"] = judge_block(judgements)
        payload["calibration"] = as_calibration_payload(calibrate(attempts))
    payload["groups"] = grouped_payload(attempts, agent=agent, verdict_source=verdict_source)
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


def total_cost(rows: Sequence[tuple[Run, Verdict | None, Task | None]]) -> Decimal:
    """The number the snapshot must agree with (PLAN §11, task 4.4)."""

    return sum((run.cost_list_usd for run, _, _ in rows), Decimal("0"))
