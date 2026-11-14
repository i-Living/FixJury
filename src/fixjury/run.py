"""One run end to end: task → checkout → adapter → ``runs`` row (PLAN §11).

The platform owns every step that decides what a run means. The adapter only
sees a frozen request and a workspace; the diff, the status and the row are
written here, so an adapter cannot report a verdict about itself.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from docker.errors import DockerException
from sqlalchemy.ext.asyncio import AsyncSession

from fixjury.agents.base import AgentResult, LlmConfig, RunRequest
from fixjury.agents.baseline import DEFAULT_MAX_STEPS
from fixjury.agents.registry import build_adapter
from fixjury.config import settings
from fixjury.metrics.cost import pricing
from fixjury.models import (
    UNABLE_REASON_LENGTH,
    CutoffAssumption,
    Difficulty,
    Run,
    RunStatus,
    Task,
)
from fixjury.sandbox.container import SandboxError
from fixjury.spec.render import task_payload
from fixjury.tasks.schema import TaskDocument
from fixjury.telemetry import current_trace_id, span

LOGGER = logging.getLogger(__name__)
WORKSPACE_PREFIX = "fixjury-run-"
CHECKOUT_CACHE_DIRNAME = "fixjury-repos"
# A clone of a real repository takes seconds; ten minutes means git is waiting for
# something that will never come, and the bench has no way to notice on its own.
CHECKOUT_TIMEOUT_S = 600.0


def bounded_reason(reason: str | None) -> str | None:
    """A reason that fits ``runs.unable_reason``.

    An adapter builds the reason from a failure message, and a git error can be long;
    PostgreSQL rejects the overflow at flush, which would throw away the run and its
    usage after the agent had already finished. The tail is kept — that is where the
    error names what went wrong.
    """

    if reason is None:
        return None
    text = reason.strip()
    if len(text) <= UNABLE_REASON_LENGTH:
        return text or None
    return "…" + text[-(UNABLE_REASON_LENGTH - 1) :]


class RunError(RuntimeError):
    """A run cannot be prepared or completed."""


# Re-exported: the file it reads is configuration, the module that owns that
# reading is the cost module, and both the queue and a run must agree on it.


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

    Every run and every verification of the same task clones the same repository, and
    a fetch that stalls on the network costs the whole benchmark — one pair hung for
    ten minutes on a clone that takes five seconds. So the repository is fetched once
    into a bare mirror and the workspaces are cloned from it locally: the network is
    touched once per repository, and a stall can only happen there.

    ``GIT_TERMINAL_PROMPT=0`` is not decoration either: git asked for credentials on a
    repository that does not need them and blocked on an inherited pipe.
    """

    git = _git()
    dest.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    mirror = _ensure_mirror(repo_url, git, env)
    if mirror is not None and _clone_local(git, mirror, dest, base_commit, env):
        return
    if mirror is not None:
        # The cached mirror may predate the commit: fetch it once and try again.
        refresh_mirror(repo_url)
        shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True, exist_ok=True)
        if _clone_local(git, mirror, dest, base_commit, env):
            return
        # A mirror that cannot serve this commit — behind the remote, or half written
        # by a killed run — is not a reason to fail the pair.
        shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True, exist_ok=True)
    _clone_network(git, repo_url, dest, base_commit, env)


def mirror_path(repo_url: str) -> Path:
    """Where the bare mirror of ``repo_url`` lives; one directory per repository."""

    root = (
        Path(settings.checkout_cache)
        if settings.checkout_cache
        else Path(tempfile.gettempdir()) / CHECKOUT_CACHE_DIRNAME
    )
    return root / hashlib.sha256(repo_url.encode("utf-8")).hexdigest()[:16]


def _run_git(argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            argv,
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=CHECKOUT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise RunError(f"{' '.join(argv[:3])} did not finish in {CHECKOUT_TIMEOUT_S:.0f}s") from exc


def _ensure_mirror(repo_url: str, git: str, env: dict[str, str]) -> Path | None:
    """The bare mirror of ``repo_url``, fetched only if it is not cached yet."""

    mirror = mirror_path(repo_url)
    if (mirror / "HEAD").is_file():
        return mirror
    mirror.parent.mkdir(parents=True, exist_ok=True)
    staging = mirror.with_name(f"{mirror.name}.{os.getpid()}")
    shutil.rmtree(staging, ignore_errors=True)
    completed = _run_git([git, "clone", "--quiet", "--bare", repo_url, str(staging)], env)
    if completed.returncode != 0:
        shutil.rmtree(staging, ignore_errors=True)
        LOGGER.warning("cannot cache %s: %s", repo_url, completed.stderr.strip())
        return None
    try:
        os.replace(staging, mirror)
    except OSError:
        # Another worker cached it first; its copy is as good as this one.
        shutil.rmtree(staging, ignore_errors=True)
    return mirror if (mirror / "HEAD").is_file() else None


def _clone_local(git: str, mirror: Path, dest: Path, base_commit: str, env: dict[str, str]) -> bool:
    """Clone from the mirror and check out the commit; ``False`` if the mirror is stale."""

    clone = _run_git(
        [git, "clone", "--quiet", "--no-checkout", "--local", str(mirror), str(dest)], env
    )
    if clone.returncode != 0:
        return False
    return _checkout(git, dest, base_commit, env)


def _clone_network(
    git: str, repo_url: str, dest: Path, base_commit: str, env: dict[str, str]
) -> None:
    clone = _run_git([git, "clone", "--quiet", "--no-checkout", repo_url, str(dest)], env)
    if clone.returncode != 0:
        message = clone.stderr.strip() or clone.stdout.strip()
        raise RunError(f"git clone failed: {message}")
    if not _checkout(git, dest, base_commit, env):
        raise RunError(f"commit {base_commit} is not in {repo_url}")


def _checkout(git: str, dest: Path, base_commit: str, env: dict[str, str]) -> bool:
    """Check out ``base_commit`` and confirm it; ``False`` when the clone lacks it."""

    checkout = _run_git([git, "-C", str(dest), "checkout", "--quiet", base_commit], env)
    if checkout.returncode != 0:
        return False
    head = _run_git([git, "-C", str(dest), "rev-parse", "HEAD"], env)
    if head.returncode != 0:
        return False
    actual = head.stdout.strip()
    if actual != base_commit:
        raise RunError(f"checked out {actual}, expected {base_commit}")
    return True


def refresh_mirror(repo_url: str) -> None:
    """Fetch the mirror again, for a commit that landed after it was cached."""

    git = _git()
    mirror = mirror_path(repo_url)
    if not (mirror / "HEAD").is_file():
        return
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    _run_git([git, "-C", str(mirror), "fetch", "--quiet", "--prune", "origin"], env)


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
    row: Run | None = None,
) -> Run:
    """Run one agent on one task and persist the row. Returns the stored run.

    ``row`` lets the worker execute a run that already exists (queued) instead of
    creating a second one, which is what makes a retry idempotent.
    """

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
        llm=LlmConfig(
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            session=settings.llm_session,
        ),
    )

    trace_id: str | None = None
    started_at = datetime.now(UTC)
    status = RunStatus.finished
    result: AgentResult | None = None
    try:
        checkout_base(document.repo, base_commit, workspace)
        with span(
            "run",
            {
                "fixjury.task": document.id,
                "fixjury.agent": adapter.name,
                "fixjury.agent_version": adapter.version,
                "fixjury.base_commit": base_commit,
            },
        ):
            result = await adapter.run(request)
            trace_id = current_trace_id()
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
    prices = pricing()
    digest = config_hash(
        task_id=document.id,
        base_commit=base_commit,
        agent=adapter.name,
        agent_version=adapter.version,
        model=model,
        image=resolved_image,
        timeout_s=settings.sandbox_timeout_s,
        max_steps=DEFAULT_MAX_STEPS,
        pricing_version=prices.version,
    )
    stored = row if row is not None else Run(task_id=document.id, attempt=attempt)
    stored.agent = adapter.name
    stored.agent_version = adapter.version
    stored.model = model
    stored.status = status
    stored.declared_success = None if result is None else result.declared_success
    stored.unable_reason = None if result is None else bounded_reason(result.unable_reason)
    stored.diff = None if result is None else result.diff
    stored.diff_sha256 = None if result is None else result.diff_sha256
    stored.tokens_in = 0 if result is None else result.tokens_in
    stored.tokens_out = 0 if result is None else result.tokens_out
    stored.sandbox_seconds = Decimal("0") if result is None else result.sandbox_seconds
    stored.cost_list_usd = prices.list_price(
        model=model,
        tokens_in=stored.tokens_in,
        tokens_out=stored.tokens_out,
        sandbox_seconds=stored.sandbox_seconds,
    )
    stored.pricing_version = prices.version
    stored.trace_id = trace_id
    stored.config_hash = digest
    stored.started_at = started_at
    stored.finished_at = finished_at

    await ensure_task(session, document)
    if row is None:
        session.add(stored)
    await session.flush()
    return stored
    session.add(stored)
    await session.flush()
    return stored
