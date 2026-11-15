"""One judged evaluation, end to end (PLAN_V1.1 §2.2).

The flow is short on purpose: rebuild the request from rows that already exist, hand the
judge the context it is allowed to see, aggregate the answers, and store one result. The
judge never re-runs an agent, never reads a container and never sees held-out test source.

The independence this release can offer is stated where it is stored: a different and
stronger model on the same subscription, its own session and a context with no agent
identity. That sentence travels with every result as a limitation, because a reader of the
number is entitled to know what it is not.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from fixjury.config import settings
from fixjury.evaluation.contracts import (
    EvaluationConfiguration,
    EvaluationContext,
    EvaluationResult,
    JudgeMode,
)
from fixjury.evaluation.scoring import ScoringProfile, assemble_result, load_profile
from fixjury.evaluation.service import build_request, default_configuration
from fixjury.evaluation.store import store_evaluation
from fixjury.judge import modes
from fixjury.judge.evaluator import Assessment
from fixjury.judge.provider import ChatCompletionsProvider
from fixjury.judge.store import configuration_hash, store_configuration, store_judge_run
from fixjury.models import EvaluationRun
from fixjury.verify.policy import parse_changed_files

#: Stored with every judged result. The owner approved model separation, not provider
#: separation (decision 3), and the number means something different because of it.
INDEPENDENCE_NOTE = (
    "the judge shares the agent's subscription: independence is a different model, a "
    "separate session and a context without agent identity, not a separate provider"
)


class JudgeServiceError(RuntimeError):
    """The evaluation cannot be judged."""


@dataclass(frozen=True)
class JudgedEvaluation:
    """What one invocation produced, for the CLI to print and the tests to assert."""

    evaluation: EvaluationRun
    result: EvaluationResult
    assessments: tuple[Assessment, ...]


def setup_from_settings() -> modes.JudgeSetup:
    """The provider and models this deployment uses to judge."""

    base_url = settings.judge_base_url or settings.llm_base_url
    if not settings.judge_model:
        raise JudgeServiceError("no judge model configured: set FIXJURY_JUDGE_MODEL")
    host = urlsplit(base_url).netloc or "custom"
    provider = ChatCompletionsProvider(
        name=host,
        base_url=base_url,
        api_key=settings.judge_api_key or settings.llm_api_key,
        session=settings.judge_session or settings.llm_session,
    )
    return modes.JudgeSetup(
        provider=provider,
        primary_model=settings.judge_model,
        escalation_model=settings.judge_escalation_model,
    )


def judge_configuration(*, mode: str | None = None) -> EvaluationConfiguration:
    """The configuration a judged evaluation runs under."""

    base = default_configuration()
    return base.model_copy(
        update={
            "mode": JudgeMode(mode or settings.judge_mode),
            "confidence_threshold": settings.judge_confidence_threshold,
            "token_budget": settings.judge_token_budget,
            "timeout_s": int(settings.judge_timeout_s),
        }
    )


async def evaluate_run(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    corpus_root: Path,
    config: EvaluationConfiguration | None = None,
    setup: modes.JudgeSetup | None = None,
    profile: ScoringProfile | None = None,
) -> JudgedEvaluation:
    """Judge one persisted run and store the result."""

    configuration = config or judge_configuration()
    resolved_setup = setup or setup_from_settings()
    request = await build_request(
        session, run_id=run_id, corpus_root=corpus_root, config=configuration
    )
    context = EvaluationContext.from_request(
        request, changed_paths=tuple(f.path for f in parse_changed_files(request.diff))
    )
    run = await modes.run(resolved_setup, context=context, config=configuration)
    aggregation = run.aggregation
    if aggregation.verdict is None:
        raise JudgeServiceError("the judge produced no assessment")

    scoring_profile = profile or load_profile(
        Path(settings.evaluation_config), name=configuration.profile
    )
    judged_hash = configuration_hash(setup=resolved_setup, config=configuration)
    result = assemble_result(
        request,
        profile=scoring_profile,
        judge_verdict=aggregation.verdict,
        judge_scores=aggregation.judge_scores,
        findings=aggregation.findings,
        assessments=tuple(assessment.summary() for assessment in run.assessments),
        confidence=aggregation.confidence,
        limitations=(INDEPENDENCE_NOTE,),
        escalated=any(assessment.escalated for assessment in run.assessments),
        reasoning_summary=_reasoning(run),
    ).model_copy(update={"judge_config_hash": judged_hash})
    stored = await store_evaluation(session, request=request, result=result)
    configuration_row = await store_configuration(
        session, setup=resolved_setup, config=configuration
    )
    await store_judge_run(
        session,
        evaluation_run_id=stored.id,
        configuration=configuration_row,
        run=run,
    )
    return JudgedEvaluation(evaluation=stored, result=result, assessments=run.assessments)


def _reasoning(run: modes.JudgeRun) -> str:
    """The judges' own summaries, labelled by judge, in one auditable paragraph."""

    parts = [
        f"{assessment.judge_id} ({assessment.model}, {assessment.response.verdict.value}, "
        f"confidence {assessment.response.confidence:.2f}): "
        f"{assessment.response.reasoning_summary.strip()}"
        for assessment in run.assessments
    ]
    return "\n\n".join(part for part in parts if part.strip())


__all__ = [
    "INDEPENDENCE_NOTE",
    "JudgeServiceError",
    "JudgedEvaluation",
    "evaluate_run",
    "judge_configuration",
    "setup_from_settings",
]
