from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Annotated

import typer

from fixjury import __version__, queue
from fixjury.agents.registry import UnknownAgentError, build_adapter
from fixjury.db import make_engine, make_session_maker, session_scope
from fixjury.models import Run, RunStatus, Verdict
from fixjury.run import RunError, ensure_task, run_task
from fixjury.tasks.cli import app as tasks_app
from fixjury.tasks.loader import read_task, task_path
from fixjury.verify.runner import VerificationError
from fixjury.verify.service import VerifyError, verify_run
from fixjury.worker import redis_client, work_once

app = typer.Typer(no_args_is_help=True, add_completion=False)
app.add_typer(tasks_app, name="tasks")


@app.callback()
def main() -> None:
    """FixJury — agent-agnostic reliability layer for autonomous code repair."""


@app.command()
def version() -> None:
    """Print the installed version."""

    typer.echo(__version__)


@app.command()
def run(
    task: Annotated[str, typer.Option("--task", help="Frozen task id, for example T-001.")],
    agent: Annotated[str, typer.Option("--agent", help="Adapter name.")] = "baseline",
    corpus: Annotated[Path, typer.Option("--corpus", help="Corpus root.")] = Path("benchmarks"),
    attempt: Annotated[
        int, typer.Option("--attempt", min=1, help="Attempt number for this agent.")
    ] = 1,
    image: Annotated[str | None, typer.Option("--image", help="Sandbox image override.")] = None,
    keep_workspace: Annotated[
        bool,
        typer.Option("--keep-workspace", help="Leave the checkout on disk for inspection."),
    ] = False,
) -> None:
    """Run one agent on one frozen task and record the result in ``runs``."""

    document = read_task(task_path(corpus, task))
    try:
        build_adapter(agent)
    except UnknownAgentError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    async def go() -> Run:
        engine = make_engine()
        try:
            async with session_scope(make_session_maker(engine)) as session:
                return await run_task(
                    document,
                    session,
                    agent=agent,
                    attempt=attempt,
                    image=image,
                    keep_workspace=keep_workspace,
                )
        finally:
            await engine.dispose()

    try:
        row = asyncio.run(go())
    except RunError as exc:
        typer.echo(f"run failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    diff_bytes = len(row.diff or "")
    typer.echo(
        f"run {row.id} {row.status.value} agent={row.agent} model={row.model} "
        f"tokens={row.tokens_in}/{row.tokens_out} diff={diff_bytes}B "
        f"sha256={row.diff_sha256 or '-'}"
    )
    if row.status is not RunStatus.finished:
        raise typer.Exit(code=1)


@app.command()
def verify(
    run_id: Annotated[str, typer.Option("--run", help="Run id (UUID) to verify.")],
    corpus: Annotated[Path, typer.Option("--corpus", help="Corpus root.")] = Path("benchmarks"),
    image: Annotated[str | None, typer.Option("--image", help="Sandbox image override.")] = None,
    keep_workspace: Annotated[
        bool,
        typer.Option("--keep-workspace", help="Leave the checkout on disk for inspection."),
    ] = False,
) -> None:
    """Verify a stored run in a fresh container and record its verdict."""

    try:
        identifier = uuid.UUID(run_id)
    except ValueError as exc:
        typer.echo(f"not a run id: {run_id!r}", err=True)
        raise typer.Exit(code=2) from exc

    async def go() -> Verdict:
        engine = make_engine()
        try:
            async with session_scope(make_session_maker(engine)) as session:
                return await verify_run(
                    session,
                    identifier,
                    corpus_root=corpus,
                    image=image,
                    keep_workspace=keep_workspace,
                )
        finally:
            await engine.dispose()

    try:
        verdict = asyncio.run(go())
    except (VerifyError, VerificationError) as exc:
        typer.echo(f"verification failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(
        f"verdict run={verdict.run_id} automated={verdict.automated_pass} "
        f"tests={verdict.tests_pass} lint={verdict.lint_pass} types={verdict.type_pass} "
        f"policy={verdict.policy_pass} heldout={verdict.heldout_pass} "
        f"tools={verdict.tool_versions}"
    )
    if not verdict.automated_pass:
        raise typer.Exit(code=1)


queue_app = typer.Typer(no_args_is_help=True, add_completion=False)
app.add_typer(queue_app, name="queue")


@queue_app.command("add")
def queue_add(
    task: Annotated[str, typer.Option("--task", help="Frozen task id.")],
    agent: Annotated[str, typer.Option("--agent", help="Adapter name.")] = "baseline",
    corpus: Annotated[Path, typer.Option("--corpus", help="Corpus root.")] = Path("benchmarks"),
    attempt: Annotated[int, typer.Option("--attempt", min=1, help="Attempt number.")] = 1,
) -> None:
    """Queue one run; duplicates are refused by (task, agent, attempt)."""

    document = read_task(task_path(corpus, task))

    async def go() -> Run:
        engine = make_engine()
        client = redis_client()
        try:
            async with session_scope(make_session_maker(engine)) as session:
                await ensure_task(session, document)
                return await queue.enqueue(
                    session, client, task_id=document.id, agent=agent, attempt=attempt
                )
        finally:
            await client.aclose()  # type: ignore[attr-defined]
            await engine.dispose()

    try:
        row = asyncio.run(go())
    except queue.QueueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"queued run {row.id} task={row.task_id} agent={row.agent} attempt={row.attempt}")


@queue_app.command("status")
def queue_status() -> None:
    """Print the depth of both queue lists."""

    async def go() -> dict[str, int]:
        client = redis_client()
        try:
            return await queue.depth(client)
        finally:
            await client.aclose()  # type: ignore[attr-defined]

    counts = asyncio.run(go())
    typer.echo(f"queued={counts['queued']} processing={counts['processing']}")


@queue_app.command("resume")
def queue_resume() -> None:
    """Put runs left in ``processing`` back into ``queued`` after a crash."""

    async def go() -> int:
        client = redis_client()
        try:
            return await queue.resume(client)
        finally:
            await client.aclose()  # type: ignore[attr-defined]

    recovered = asyncio.run(go())
    typer.echo(f"requeued {recovered} run(s)")


@app.command()
def worker(
    once: Annotated[bool, typer.Option("--once", help="Process one run and exit.")] = False,
    corpus: Annotated[Path, typer.Option("--corpus", help="Corpus root.")] = Path("benchmarks"),
    image: Annotated[str | None, typer.Option("--image", help="Sandbox image override.")] = None,
    wait_s: Annotated[
        float, typer.Option("--wait", help="Seconds to block on an empty queue.")
    ] = 5.0,
) -> None:
    """Claim runs from the queue and execute them, one at a time."""

    async def go() -> None:
        engine = make_engine()
        client = redis_client()
        try:
            while True:
                async with session_scope(make_session_maker(engine)) as session:
                    row = await work_once(
                        client,
                        session,
                        corpus_root=corpus,
                        image=image,
                        wait_s=wait_s,
                    )
                if row is not None:
                    typer.echo(
                        f"run {row.id} {row.status.value} agent={row.agent} "
                        f"attempt={row.attempt} diff={len(row.diff or '')}B"
                    )
                elif once:
                    typer.echo("queue is empty")
                if once:
                    return
        finally:
            await client.aclose()  # type: ignore[attr-defined]
            await engine.dispose()

    asyncio.run(go())
