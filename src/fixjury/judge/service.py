"""One judged evaluation, end to end (PLAN_V1.1 §2.2).

The flow is short on purpose: rebuild the request from rows that already exist, hand the
judge the context it is allowed to see, aggregate the answers, and store one result. The
judge never re-runs an agent, never reads a container and never sees held-out test source.

The independence this release can offer is stated where it is stored: a different and
stronger model, its own context and no agent identity. The provider is the same
subscription, and the session is the agent's unless one is configured — that is a
documented limitation, and the note stored with each result says which of the two is true.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from fixjury.config import settings
from fixjury.evaluation.contracts import (
    EvaluationConfiguration,
    EvaluationContext,
    EvaluationResult,
)
from fixjury.evaluation.scoring import (
    ScoringError,
    ScoringProfile,
    assemble_result,
    load_profile,
)
from fixjury.evaluation.service import (
    EvaluationError,
    build_request,
    default_configuration,
)
from fixjury.evaluation.store import store_evaluation, store_failed_evaluation
from fixjury.judge import modes
from fixjury.judge.evaluator import Assessment, JudgeError
from fixjury.judge.prompts import PromptError
from fixjury.judge.provider import ChatCompletionsProvider
from fixjury.judge.schema import JudgeResponseError
from fixjury.judge.store import configuration_hash, store_configuration, store_judge_run
from fixjury.llm import LlmError
from fixjury.models import EvaluationRun, Run
from fixjury.verify.policy import parse_changed_files

#: Stored with every judged result. The owner approved model separation, not provider
#: separation (decision 3), and the number means something different because of it.
INDEPENDENCE_NOTE = (
    "the judge shares the agent's subscription: independence is a different model, a "
    "separate context and no agent identity, not a separate provider"
)

#: Appended when the judge also reuses the agent's provider session.
SHARED_SESSION_NOTE = (
    "; it also shares the agent's provider session (FIXJURY_JUDGE_SESSION is unset)"
)

#: Hosts where plaintext HTTP is not a credential leak: the traffic never leaves the machine.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

#: One judge's summary and the whole paragraph have to fit the stored column.
SUMMARY_LIMIT = 2000
REASONING_LIMIT = 7500


class JudgeServiceError(RuntimeError):
    """The evaluation cannot be judged."""


@dataclass(frozen=True)
class JudgedEvaluation:
    """What one invocation produced, for the CLI to print and the tests to assert."""

    evaluation: EvaluationRun
    result: EvaluationResult
    assessments: tuple[Assessment, ...]


def require_secure_endpoint(base_url: str) -> None:
    """Refuse to send a credential over a scheme that does not protect it."""

    split = urlsplit(base_url)
    if split.scheme == "https":
        return
    if split.scheme == "http" and (split.hostname or "") in LOOPBACK_HOSTS:
        return
    raise JudgeServiceError(f"the judge endpoint must be https, or http on loopback: {base_url!r}")


def setup_from_settings() -> modes.JudgeSetup:
    """The provider and models this deployment uses to judge."""

    base_url = settings.judge_base_url or settings.llm_base_url
    require_secure_endpoint(base_url)
    if not settings.judge_model:
        raise JudgeServiceError("no judge model configured: set FIXJURY_JUDGE_MODEL")
    if settings.llm_model and settings.judge_model == settings.llm_model:
        # A judge that runs on the model under test is not an independent judge, and every
        # stored result would claim it was.
        raise JudgeServiceError(
            "FIXJURY_JUDGE_MODEL must differ from FIXJURY_LLM_MODEL: the judge cannot be "
            "the model it evaluates"
        )
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
    """The configuration a judged evaluation runs under, validated as a whole."""

    base = default_configuration()
    payload = {
        **base.model_dump(mode="json"),
        "mode": (mode or settings.judge_mode),
        "confidence_threshold": settings.judge_confidence_threshold,
        "token_budget": settings.judge_token_budget,
        "timeout_s": max(1, int(settings.judge_timeout_s)),
    }
    try:
        # Through model_validate, not model_copy: an override from the environment has to
        # pass the same ranges as everything else, and model_copy would skip them.
        return EvaluationConfiguration.model_validate(payload)
    except ValidationError as exc:
        raise JudgeServiceError(f"the judge configuration is invalid: {exc}") from exc


def independence_note() -> str:
    """What is true about the independence of this judge, for the stored limitations."""

    if settings.judge_session:
        return INDEPENDENCE_NOTE
    return INDEPENDENCE_NOTE + SHARED_SESSION_NOTE


def _recorded_configuration() -> EvaluationConfiguration:
    """The configuration to write on a failure row, even when the settings are invalid.

    ``judge_configuration`` raises on a bad `FIXJURY_JUDGE_MODE`, and a recorder that called
    it again would raise for the same reason — leaving a failed judgement unrecorded, which
    is the state this table exists to prevent. The original error still reaches the row.
    """

    try:
        return judge_configuration()
    except JudgeServiceError:
        return default_configuration()


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
    summaries = [assessment.summary() for assessment in run.assessments]
    if aggregation.verdict is None:
        await store_failed_evaluation(
            session,
            run_id=request.run_id,
            task_id=request.subject_ref,
            subject_ref=request.subject_ref,
            config=configuration,
            error="the judge produced no assessment",
            assessments=summaries,
        )
        raise JudgeServiceError("the judge produced no assessment")

    try:
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
            assessments=tuple(summaries),
            confidence=aggregation.confidence,
            limitations=(independence_note(),),
            escalated=any(assessment.escalated for assessment in run.assessments),
            reasoning_summary=_reasoning(run),
        ).model_copy(update={"judge_config_hash": judged_hash})
    except (ScoringError, ValueError) as exc:
        # The judge answered and was paid for; the scoring step is what failed. Record the
        # calls that happened so a failure row does not report a judgement as free.
        await store_failed_evaluation(
            session,
            run_id=request.run_id,
            task_id=request.subject_ref,
            subject_ref=request.subject_ref,
            config=configuration,
            error=str(exc),
            assessments=summaries,
        )
        raise
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


async def record_failure(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    error: str,
    config: EvaluationConfiguration | None = None,
) -> EvaluationRun:
    """Store a failed evaluation so a benchmark can count it and say why.

    The run keeps its own status: an evaluation that could not reach a verdict is not
    evidence against the patch, and a benchmark that recorded it as one would report a
    provider outage as a rejection.
    """

    run = await session.get(Run, run_id)
    if run is None:
        raise JudgeServiceError(f"run {run_id} does not exist")
    return await store_failed_evaluation(
        session,
        run_id=run_id,
        task_id=run.task_id,
        subject_ref=run.task_id,
        config=config or _recorded_configuration(),
        error=error,
    )


def _reasoning(run: modes.JudgeRun) -> str:
    """The judges' own summaries, labelled by judge, in one bounded paragraph."""

    parts = [
        f"{assessment.judge_id} ({assessment.model}, {assessment.response.verdict.value}, "
        f"confidence {assessment.response.confidence:.2f}): "
        f"{assessment.response.reasoning_summary.strip()[:SUMMARY_LIMIT]}"
        for assessment in run.assessments
    ]
    return "\n\n".join(part for part in parts if part.strip())[:REASONING_LIMIT]


#: Everything a judged evaluation can raise on a corpus of mixed quality. A bench uses one
#: tuple so a new failure mode is caught by both the command and the batch, not only one.
JUDGE_FAILURES: tuple[type[Exception], ...] = (
    JudgeServiceError,
    JudgeError,
    JudgeResponseError,
    PromptError,
    ScoringError,
    EvaluationError,
    LlmError,
    ValueError,
)


__all__ = [
    "INDEPENDENCE_NOTE",
    "JUDGE_FAILURES",
    "REASONING_LIMIT",
    "SHARED_SESSION_NOTE",
    "JudgeServiceError",
    "JudgedEvaluation",
    "evaluate_run",
    "independence_note",
    "judge_configuration",
    "record_failure",
    "require_secure_endpoint",
    "setup_from_settings",
]
