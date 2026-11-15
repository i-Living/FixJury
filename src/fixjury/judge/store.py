"""Persisting what the judges said (PLAN_V1.1 §2.6).

Three rows per evaluation settle the audit trail: which configuration judged, what each
judge answered, and why another judge was or was not asked. The configuration is named by
its digest and reused, so a hundred evaluations under the same policy point at one row.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fixjury.evaluation.contracts import EvaluationConfiguration, digest
from fixjury.judge.modes import JudgeRun, JudgeSetup
from fixjury.models import JudgeAssessment, JudgeConfiguration, JudgeEscalation


class JudgeStoreError(RuntimeError):
    """The judge's work cannot be written down."""


def configuration_hash(*, setup: JudgeSetup, config: EvaluationConfiguration) -> str:
    """The digest of everything that makes two judge runs comparable."""

    return digest(
        {
            "provider": setup.provider.name,
            "primary_model": setup.primary_model,
            "escalation_model": setup.escalation_model,
            "config": config.model_dump(mode="json"),
        }
    )


async def store_configuration(
    session: AsyncSession, *, setup: JudgeSetup, config: EvaluationConfiguration
) -> JudgeConfiguration:
    """The configuration row for this setup, created once per digest."""

    config_hash = configuration_hash(setup=setup, config=config)
    existing = await session.scalar(
        select(JudgeConfiguration).where(JudgeConfiguration.config_hash == config_hash)
    )
    if existing is not None:
        return existing
    row = JudgeConfiguration(
        mode=config.mode.value,
        provider=setup.provider.name,
        primary_model=setup.primary_model,
        escalation_model=setup.escalation_model or None,
        config=config.model_dump(mode="json"),
        config_hash=config_hash,
    )
    session.add(row)
    await session.flush()
    return row


async def store_judge_run(
    session: AsyncSession,
    *,
    evaluation_run_id: uuid.UUID,
    configuration: JudgeConfiguration,
    run: JudgeRun,
) -> None:
    """Every assessment and every escalation decision of one evaluation."""

    if not run.assessments:
        raise JudgeStoreError(f"evaluation {evaluation_run_id} has no assessment to store")
    for assessment in run.assessments:
        session.add(
            JudgeAssessment(
                evaluation_run_id=evaluation_run_id,
                judge_configuration_id=configuration.id,
                judge_id=assessment.judge_id,
                provider=assessment.provider,
                model=assessment.model,
                prompt_version=assessment.prompt_version,
                verdict=assessment.response.verdict,
                overall_score=Decimal(str(assessment.overall)),
                confidence=Decimal(str(assessment.response.confidence)),
                escalated=assessment.escalated,
                raw={
                    "text": assessment.raw,
                    "response": assessment.response.model_dump(mode="json"),
                },
                tokens_in=assessment.tokens_in,
                tokens_out=assessment.tokens_out,
                cost_list_usd=assessment.cost_list_usd,
                latency_ms=assessment.latency_ms,
            )
        )
    for position, escalation in enumerate(run.escalations, start=1):
        session.add(
            JudgeEscalation(
                evaluation_run_id=evaluation_run_id,
                round_no=position,
                escalated=escalation.escalate,
                triggers=list(escalation.triggers),
                reason=escalation.reason,
            )
        )
    await session.flush()


__all__ = [
    "JudgeStoreError",
    "configuration_hash",
    "store_configuration",
    "store_judge_run",
]
