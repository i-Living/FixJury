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
from fixjury.models import Rejection, RejectionSource, Run, Verdict
from fixjury.tasks.loader import read_task, task_path
from fixjury.verify.policy import Violation, check_policy
from fixjury.verify.runner import verify_workspace
from fixjury.verify.verdict import VerificationResult, assemble

LOGGER = logging.getLogger(__name__)
HELDOUT_DIRNAME = "heldout"


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
        finally:
            if not keep_workspace:
                shutil.rmtree(workspace, ignore_errors=True)

    return await store_verdict(session, run_id, result)


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
