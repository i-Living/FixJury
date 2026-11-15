"""Persisting an evaluation (PLAN_V1.1 §1.4, SPEC §8).

One write creates three rows: the evaluation run, its result and its findings. They are
flushed together, so a reader never sees a verdict without the findings that explain it.

Re-evaluating a run is allowed and expected — a new judge configuration is a new
evaluation — so this never updates an existing result in place.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fixjury.config import settings
from fixjury.evaluation.contracts import (
    AssessmentSummary,
    CriterionScore,
    EvaluationFinding,
    EvaluationRequest,
    EvaluationResult,
    EvaluationScore,
)
from fixjury.models import (
    EvaluationFinding as EvaluationFindingRow,
)
from fixjury.models import (
    EvaluationResult as EvaluationResultRow,
)
from fixjury.models import (
    EvaluationRun,
    EvaluationSource,
    EvaluationStatus,
)


class StoreError(RuntimeError):
    """The evaluation cannot be stored: it contradicts its request or its subject."""


@dataclass(frozen=True)
class StoredEvaluation:
    """An evaluation as it was read back: the run, its result and its findings."""

    run: EvaluationRun
    result: EvaluationResult | None
    findings: tuple[EvaluationFinding, ...]


def _usage(result: EvaluationResult) -> tuple[int, int, Decimal]:
    """Token and cost totals over the assessments that produced this result."""

    tokens_in = sum(assessment.tokens_in for assessment in result.assessments)
    tokens_out = sum(assessment.tokens_out for assessment in result.assessments)
    cost = sum((assessment.cost_list_usd for assessment in result.assessments), Decimal("0"))
    return tokens_in, tokens_out, cost


def _task_subject(request: EvaluationRequest) -> str | None:
    """The benchmark stores a task id as its subject; any other source has no task row."""

    if request.source is EvaluationSource.benchmark:
        return request.subject_ref
    return None


async def store_evaluation(
    session: AsyncSession,
    *,
    request: EvaluationRequest,
    result: EvaluationResult,
    started_at: datetime | None = None,
) -> EvaluationRun:
    """Write one evaluation and return the run row that now owns it."""

    if result.config_hash != request.config.config_hash():
        raise StoreError(
            "the result was computed under a different configuration than the request carries"
        )

    finished_at = datetime.now(UTC)
    tokens_in, tokens_out, cost = _usage(result)
    run = EvaluationRun(
        source=request.source,
        subject_ref=request.subject_ref,
        task_id=_task_subject(request),
        run_id=request.run_id,
        config=request.config.model_dump(mode="json"),
        config_hash=result.config_hash,
        scoring_profile=result.score.profile,
        scoring_version=result.score.profile_version,
        assessments=[assessment.model_dump(mode="json") for assessment in result.assessments],
        judge_config_hash=result.judge_config_hash,
        status=EvaluationStatus.finished,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_list_usd=cost,
        pricing_version=settings.pricing_version,
        started_at=started_at,
        finished_at=finished_at,
    )
    session.add(run)
    await session.flush()

    session.add(
        EvaluationResultRow(
            evaluation_run_id=run.id,
            verdict=result.verdict,
            judge_verdict=result.judge_verdict,
            overall_score=Decimal(str(result.score.overall)),
            confidence=Decimal(str(result.confidence)),
            criteria=[item.model_dump(mode="json") for item in result.score.criteria],
            reasoning_summary=result.reasoning_summary,
            limitations=list(result.limitations),
            escalated=result.escalated,
        )
    )
    for finding in result.findings:
        session.add(
            EvaluationFindingRow(
                evaluation_run_id=run.id,
                severity=finding.severity,
                category=finding.category,
                source=finding.source,
                confirmed=finding.confirmed,
                description=finding.description,
                evidence_ref=finding.evidence_ref,
                path=finding.path,
                line=finding.line,
                impact=finding.impact,
            )
        )
    await session.flush()
    return run


def result_from_rows(
    run: EvaluationRun,
    row: EvaluationResultRow,
    findings: list[EvaluationFindingRow],
) -> EvaluationResult:
    """Rebuild the contract from the three rows that store it."""

    return EvaluationResult(
        verdict=row.verdict,
        judge_verdict=row.judge_verdict,
        score=EvaluationScore(
            overall=float(row.overall_score),
            criteria=tuple(CriterionScore.model_validate(item) for item in row.criteria),
            profile=run.scoring_profile,
            profile_version=run.scoring_version,
        ),
        confidence=float(row.confidence),
        findings=tuple(
            EvaluationFinding(
                severity=finding.severity,
                category=finding.category,
                description=finding.description,
                evidence_ref=finding.evidence_ref,
                impact=finding.impact,
                source=finding.source,
                confirmed=finding.confirmed,
                path=finding.path,
                line=finding.line,
            )
            for finding in findings
        ),
        assessments=tuple(AssessmentSummary.model_validate(item) for item in run.assessments),
        limitations=tuple(row.limitations),
        escalated=row.escalated,
        reasoning_summary=row.reasoning_summary,
        config_hash=run.config_hash,
        judge_config_hash=run.judge_config_hash,
    )


async def load_evaluation(
    session: AsyncSession, evaluation_run_id: uuid.UUID
) -> StoredEvaluation | None:
    """Read one evaluation back, findings included."""

    run = await session.get(EvaluationRun, evaluation_run_id)
    if run is None:
        return None
    row = await session.get(EvaluationResultRow, evaluation_run_id)
    if row is None:
        return StoredEvaluation(run=run, result=None, findings=())
    findings = list(
        (
            await session.execute(
                select(EvaluationFindingRow)
                .where(EvaluationFindingRow.evaluation_run_id == evaluation_run_id)
                .order_by(EvaluationFindingRow.id)
            )
        )
        .scalars()
        .all()
    )
    return StoredEvaluation(
        run=run,
        result=result_from_rows(run, row, findings),
        findings=result_findings(findings),
    )


def result_findings(rows: list[EvaluationFindingRow]) -> tuple[EvaluationFinding, ...]:
    """The findings of a stored evaluation, in the contract's shape."""

    return tuple(
        EvaluationFinding(
            severity=row.severity,
            category=row.category,
            description=row.description,
            evidence_ref=row.evidence_ref,
            impact=row.impact,
            source=row.source,
            confirmed=row.confirmed,
            path=row.path,
            line=row.line,
        )
        for row in rows
    )


async def evaluations_for_run(session: AsyncSession, run_id: uuid.UUID) -> list[EvaluationRun]:
    """Every evaluation of one run, newest first."""

    rows = await session.execute(
        select(EvaluationRun)
        .where(EvaluationRun.run_id == run_id)
        .order_by(EvaluationRun.created_at.desc())
    )
    return list(rows.scalars().all())


__all__ = [
    "StoreError",
    "StoredEvaluation",
    "evaluations_for_run",
    "load_evaluation",
    "result_findings",
    "result_from_rows",
    "store_evaluation",
]
