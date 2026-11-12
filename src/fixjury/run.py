"""One run end to end: task → checkout → adapter → ``runs`` row (PLAN §11).

The platform owns every step that decides what a run means. The adapter only
sees a frozen request and a workspace; the diff, the status and the row are
written here, so an adapter cannot report a verdict about itself.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from docker.errors import DockerException
from sqlalchemy.ext.asyncio import AsyncSession

from fixjury.agents.base import AgentResult, RunRequest
from fixjury.agents.baseline import DEFAULT_MAX_STEPS
from fixjury.agents.registry import build_adapter
from fixjury.config import settings
from fixjury.models import CutoffAssumption, Difficulty, Run, RunStatus, Task
from fixjury.sandbox.container import SandboxError
from fixjury.spec.render import task_payload
from fixjury.tasks.schema import TaskDocument

LOGGER = logging.getLogger(__name__)
WORKSPACE_PREFIX = "fixjury-run-"


class RunError(RuntimeError):
    """A run cannot be prepared or completed."""


def agent_spec_text(document: TaskDocument) -> str:
    """Render the task as the text an agent reads. Deterministic, no secrets."""

    lines = [f"# Task {document.id} — {document.title}", "", f"Repository: {document.repo}"]
    if document.base_commit:
        lines.append(f"Base commit: {document.base_commit}")
    body = (document.issue.body or "").strip()
    lines += ["", "## Reported issue", "", body, ""]
    if document.acceptance:
        lines += ["## Acceptance criteria", ""]
        lines += [f"- {item}" for item in document.acceptance]
        lines.append("")
    if document.checks:
        lines += ["## Checks the fix must pass", ""]
        lines += [f"- `{item}`" for item in document.checks]
        lines.append("")
    if document.allowed_paths:
        lines += ["## Allowed paths", "", "Only change files under:", ""]
        lines += [f"- `{item}`" for item in document.allowed_paths]
        lines.append("")
    if document.forbidden:
        lines += ["## Forbidden", ""]
        lines += [f"- {item}" for item in document.forbidden]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def config_hash(
    *,
    task_id: str,
    base_commit: str,
    agent: str,
    agent_version: str,
    model: str,
    image: str,
    timeout_s: int,
    max_steps: int,
    pricing_version: str,
) -> str:
    """Digest of everything that would make two runs incomparable."""

    payload = {
        "task_id": task_id,
        "base_commit": base_commit,
        "agent": agent,
        "agent_version": agent_version,
        "model": model,
        "image": image,
        "timeout_s": timeout_s,
        "max_steps": max_steps,
        "pricing_version": pricing_version,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _git() -> str:
    binary = shutil.which("git")
    if binary is None:
        raise RunError("git is not installed")
    return binary


def checkout_base(repo_url: str, base_commit: str, dest: Path) -> None:
    """Clone ``repo_url`` and check out exactly ``base_commit``.

    No ``--filter``: a partial clone left the checkout without blobs in this
    environment, and a checkout that silently misses files is worse than a
    slightly slower clone.
    """

    git = _git()
    dest.mkdir(parents=True, exist_ok=True)
    commands = [
        [git, "clone", "--quiet", "--no-checkout", repo_url, str(dest)],
        [git, "-C", str(dest), "checkout", "--quiet", base_commit],
        [git, "-C", str(dest), "rev-parse", "HEAD"],
    ]
    outputs: list[str] = []
    for argv in commands:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            argv, capture_output=True, text=True, check=False
        )
        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip()
            raise RunError(f"{' '.join(argv[:3])} failed: {message}")
        outputs.append(completed.stdout)
    head = outputs[-1].strip()
    if head != base_commit:
        raise RunError(f"checked out {head}, expected {base_commit}")


def _task_row(document: TaskDocument) -> Task:
    spec = json.loads(json.dumps(task_payload(document), default=str))
    return Task(
        id=document.id,
        repo_url=document.repo,
        license=document.license or "unknown",
        base_commit=document.base_commit or "",
        fix_commit=document.fix_commit,
        fix_merged_at=document.issue.fix_merged_at,
        issue_number=document.issue.number,
        issue_url=document.issue.url,
        issue_created_at=document.issue.created_at,
        title=document.title,
        body=document.issue.body or "",
        difficulty=Difficulty(document.difficulty.value),
        spec=spec,
        cutoff_assumption=CutoffAssumption(document.cutoff_assumption.value),
        heldout_ref=document.heldout_ref or f"benchmarks/heldout/{document.id}",
        frozen_by=document.frozen_by or "unknown",
    )


async def ensure_task(session: AsyncSession, document: TaskDocument) -> Task:
    """Insert the task once; a frozen task is never rewritten."""

    existing = await session.get(Task, document.id)
    if existing is not None:
        if existing.base_commit != (document.base_commit or ""):
            raise RunError(
                f"task {document.id} is frozen at {existing.base_commit}, "
                f"the corpus says {document.base_commit}; add a new task version"
            )
        return existing
    row = _task_row(document)
    session.add(row)
    await session.flush()
    return row


async def run_task(
    document: TaskDocument,
    session: AsyncSession,
    *,
    agent: str = "baseline",
    attempt: int = 1,
    image: str | None = None,
    keep_workspace: bool = False,
) -> Run:
    """Run one agent on one task and persist the row. Returns the stored run."""

    base_commit = document.base_commit
    if not base_commit:
        raise RunError(f"task {document.id} has no base_commit; freeze it first")
    adapter = build_adapter(agent)
    resolved_image = image or settings.sandbox_image
    workspace = Path(tempfile.mkdtemp(prefix=WORKSPACE_PREFIX))
    request = RunRequest(
        run_id=str(uuid.uuid4()),
        repo_url=document.repo,
        base_commit=base_commit,
        spec=agent_spec_text(document),
        workspace=workspace,
        image=resolved_image,
        max_steps=DEFAULT_MAX_STEPS,
        timeout_s=settings.sandbox_timeout_s,
    )

    started_at = datetime.now(UTC)
    status = RunStatus.finished
    result: AgentResult | None = None
    try:
        checkout_base(document.repo, base_commit, workspace)
        result = await adapter.run(request)
    except (RunError, SandboxError, DockerException) as exc:
        status = RunStatus.infra_failed
        LOGGER.warning("run for %s failed before verification: %s", document.id, exc)
    except Exception as exc:
        status = RunStatus.infra_failed
        LOGGER.warning("adapter %s crashed on %s: %s", agent, document.id, exc)
    finally:
        finished_at = datetime.now(UTC)
        if not keep_workspace:
            shutil.rmtree(workspace, ignore_errors=True)

    model = settings.llm_model or "unknown"
    row = Run(
        task_id=document.id,
        agent=adapter.name,
        agent_version=adapter.version,
        model=model,
        attempt=attempt,
        status=status,
        declared_success=None if result is None else result.declared_success,
        diff=None if result is None else result.diff,
        diff_sha256=None if result is None else result.diff_sha256,
        tokens_in=0 if result is None else result.tokens_in,
        tokens_out=0 if result is None else result.tokens_out,
        sandbox_seconds=Decimal("0") if result is None else result.sandbox_seconds,
        cost_list_usd=Decimal("0"),
        pricing_version=settings.pricing_version,
        config_hash=config_hash(
            task_id=document.id,
            base_commit=base_commit,
            agent=adapter.name,
            agent_version=adapter.version,
            model=model,
            image=resolved_image,
            timeout_s=settings.sandbox_timeout_s,
            max_steps=DEFAULT_MAX_STEPS,
            pricing_version=settings.pricing_version,
        ),
        started_at=started_at,
        finished_at=finished_at,
    )
    await ensure_task(session, document)
    session.add(row)
    await session.flush()
    return row
