"""Rebuilding an evaluation request from persisted rows (PLAN_V1.1 §3.1).

An evaluation reads what verification already recorded: the diff, the task specification,
the structured check outcomes and the path-policy result. It never re-runs the agent and
never touches a container, which is what makes re-evaluating an old run cheap and honest.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fixjury.config import settings
from fixjury.evaluation.checks import outcomes_from_stored, to_check_results
from fixjury.evaluation.contracts import (
    EvaluationConfiguration,
    EvaluationRequest,
    digest,
)
from fixjury.models import Rejection, RejectionSource, Run, Verdict
from fixjury.run import agent_spec_text
from fixjury.tasks.loader import TaskFileError, read_task, task_path


class EvaluationError(RuntimeError):
    """The run cannot be evaluated: missing run, missing verdict, unreadable task."""


def default_configuration(profile: str | None = None) -> EvaluationConfiguration:
    """The configuration an evaluation uses unless a caller overrides it."""

    return EvaluationConfiguration(profile=profile or settings.evaluation_profile)


async def _policy_violations(session: AsyncSession, run_id: uuid.UUID) -> tuple[str, ...]:
    rows = await session.execute(
        select(Rejection)
        .where(Rejection.run_id == run_id, Rejection.source == RejectionSource.automated)
        .order_by(Rejection.id)
    )
    return tuple(
        f"{rejection.code.value}: {rejection.note}" if rejection.note else rejection.code.value
        for rejection in rows.scalars()
    )


async def build_request(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    corpus_root: Path,
    config: EvaluationConfiguration | None = None,
) -> EvaluationRequest:
    """Everything the evaluation needs, taken from rows that already exist.

    A run without a verdict is refused rather than evaluated: an opinion about a patch no
    check ever saw is not an evaluation, and producing one would invent evidence.
    """

    run = await session.get(Run, run_id)
    if run is None:
        raise EvaluationError(f"run {run_id} does not exist")
    verdict = await session.get(Verdict, run_id)
    if verdict is None:
        raise EvaluationError(f"run {run_id} has no verdict; verify it before evaluating it")

    try:
        # YAML parsing is CPU work on a file this process may not own; the event loop has
        # other runs to serve.
        document = await asyncio.to_thread(read_task, task_path(corpus_root, run.task_id))
    except (TaskFileError, OSError) as exc:
        raise EvaluationError(f"task {run.task_id} cannot be read: {exc}") from exc
    if not document.base_commit:
        raise EvaluationError(f"task {document.id} has no base_commit")

    diff = run.diff or ""
    diff_sha256 = run.diff_sha256 or hashlib.sha256(diff.encode("utf-8")).hexdigest()
    outcomes = outcomes_from_stored(verdict.check_results or ())
    if not outcomes:
        # A verdict written before `0003_evaluation_core` carries five booleans and no
        # evidence. An evaluation of it would have nothing to cite, so it is refused
        # rather than stored as an inconclusive result nobody can act on: re-verifying
        # the run rewrites the verdict with its structured outcomes.
        raise EvaluationError(
            f"run {run_id} has no stored check evidence; re-verify it so the verdict "
            "records what each check printed"
        )
    checks = to_check_results(outcomes)
    configuration = config or default_configuration()
    return EvaluationRequest(
        subject_ref=document.id,
        run_id=run.id,
        base_commit=document.base_commit,
        diff=diff,
        diff_sha256=diff_sha256,
        spec_text=agent_spec_text(document),
        checks=checks,
        policy_pass=verdict.policy_pass,
        policy_violations=await _policy_violations(session, run_id),
        allowed_paths=tuple(document.allowed_paths),
        forbidden=tuple(document.forbidden),
        # The configuration records which evidence it was made from, so a gate can tell an
        # evaluation of this verdict from an evaluation of an older one.
        config=configuration.model_copy(
            update={"evidence_sha256": digest([item.model_dump(mode="json") for item in checks])}
        ),
    )


__all__ = ["EvaluationError", "build_request", "default_configuration"]
