"""Persistence for verification: one verdict per run, rewritten on re-verification.

``fixjury verify --run <id>`` is idempotent on purpose (PLAN §11, task 3.1): the
same diff and the same task must produce the same verdict, so re-running deletes
the previous verdict and its automated rejections instead of accumulating them.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from fixjury.config import settings
from fixjury.models import UNABLE_REASON_LENGTH, Rejection, RejectionSource, Run, RunStatus, Verdict
from fixjury.run import RunError
from fixjury.sandbox.container import SandboxError
from fixjury.sandbox.tools import ToolError
from fixjury.tasks.loader import read_task, task_path
from fixjury.verify.policy import Violation, check_policy
from fixjury.verify.runner import VerificationError, verify_workspace
from fixjury.verify.verdict import VerificationResult, assemble

LOGGER = logging.getLogger(__name__)
HELDOUT_DIRNAME = "heldout"
VERIFICATION_REASON_PREFIX = "verification failed: "


class VerifyError(RuntimeError):
    """The run cannot be verified: missing run, missing task, unusable diff."""


async def verify_run(
    session: AsyncSession,
    run_id: uuid.UUID,
    *,
    corpus_root: Path,
    image: str | None = None,
    keep_workspace: bool = False,
) -> Verdict:
    """Verify one stored run and replace its verdict. Returns the stored row."""

    run = await session.get(Run, run_id)
    if run is None:
        raise VerifyError(f"run {run_id} does not exist")
    document = read_task(task_path(corpus_root, run.task_id))
    diff = run.diff or ""
    bundle = corpus_root / HELDOUT_DIRNAME / run.task_id
    heldout_bundle = bundle if bundle.is_dir() else None

    if not diff.strip():
        result = assemble(
            (),
            policy=check_policy(
                diff, allowed_paths=document.allowed_paths, forbidden=document.forbidden
            ),
            heldout_pass=None,
            tool_versions={},
            note="the run produced no diff",
        )
    else:
        workspace = Path(tempfile.mkdtemp(prefix="fixjury-verify-"))
        try:
            result = await verify_workspace(
                document,
                diff,
                workspace=workspace,
                image=image or settings.sandbox_image,
                heldout_bundle=heldout_bundle,
            )
        except (VerificationError, ToolError, SandboxError, RunError) as exc:
            # An install step, a checkout that never finished and the sandbox itself
            # are not the run's fault, but the pair has no verdict all the same: record
            # it as the exclusion it is, so the report can drop it with a reason
            # instead of reading a data defect. The caller still sees one error type —
            # and one that ``bench`` catches, or a single unusable task ends the bench.
            await _exclude(session, run, reason=str(exc))
            # Committed here, not by the caller: VerifyError is raised next, and the
            # caller's session scope rolls back on any exception, which would take the
            # exclusion reason with it. AGENTS.md requires every dropped run to keep
            # its reason.
            await session.commit()
            raise VerifyError(str(exc)) from exc
        finally:
            if not keep_workspace:
                shutil.rmtree(workspace, ignore_errors=True)

    await _restore(session, run)
    return await store_verdict(session, run_id, result)


async def _exclude(session: AsyncSession, run: Run, *, reason: str) -> None:
    """Store why this pair leaves the metrics: an unbuildable verification environment.

    ``infra_failed`` is the §3.2 word for exactly this, and the reason goes in the
    column that already answers "why is this run not usable".
    """

    run.status = RunStatus.infra_failed
    run.unable_reason = _exclusion_reason(reason)
    await session.flush()


def _exclusion_reason(reason: str) -> str:
    """The recorded reason, with the marker ``_restore`` looks for kept in front.

    ``bounded_reason`` keeps the tail of a long message and drops the head, which would
    take the marker with it and leave the run looking permanently excluded.
    """

    detail = reason.strip()
    budget = UNABLE_REASON_LENGTH - len(VERIFICATION_REASON_PREFIX)
    if len(detail) > budget:
        detail = f"…{detail[len(detail) - budget + 1 :]}"
    return f"{VERIFICATION_REASON_PREFIX}{detail}"


async def _restore(session: AsyncSession, run: Run) -> None:
    """A run that verified on a retry is not an exclusion any more.

    Only the exclusion this module writes is lifted: an agent's own ``unable_reason``
    on a finished run says why the agent gave up, and that stays.
    """

    if run.status is not RunStatus.infra_failed:
        return
    if not (run.unable_reason or "").startswith(VERIFICATION_REASON_PREFIX):
        return
    run.status = RunStatus.finished
    run.unable_reason = None
    await session.flush()


async def store_verdict(
    session: AsyncSession, run_id: uuid.UUID, result: VerificationResult
) -> Verdict:
    """Replace the verdict for ``run_id`` with ``result``; never duplicate rows."""

    existing = await session.get(Verdict, run_id)
    if existing is not None:
        await session.delete(existing)
        await session.flush()
    await session.execute(delete(Rejection).where(Rejection.run_id == run_id))

    verdict = Verdict(
        run_id=run_id,
        tests_pass=result.tests_pass,
        lint_pass=result.lint_pass,
        type_pass=result.type_pass,
        policy_pass=result.policy_pass,
        heldout_pass=result.heldout_pass,
        automated_pass=result.automated_pass,
        tool_versions=dict(result.tool_versions),
    )
    session.add(verdict)
    for violation in result.rejections:
        session.add(
            Rejection(
                run_id=run_id,
                source=RejectionSource.automated,
                code=violation.code,
                note=violation.note,
            )
        )
    await session.flush()
    return verdict


__all__ = ["VerifyError", "Violation", "store_verdict", "verify_run"]
