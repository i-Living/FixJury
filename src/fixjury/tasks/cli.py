"""``fixjury tasks`` — corpus tooling (PLAN §8)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import httpx
import typer

from fixjury.config import settings
from fixjury.github import GithubError
from fixjury.llm import LlmError
from fixjury.spec.generator import (
    DraftError,
    DraftResult,
    fix_candidate_paths,
    generate_draft,
    load_issue,
)
from fixjury.spec.render import render_task
from fixjury.tasks.freeze import (
    FreezeError,
    FreezeFacts,
    collect_facts,
    default_frozen_by,
    freeze_document,
)
from fixjury.tasks.loader import TaskFileError, discover_task_paths, read_task, write_task
from fixjury.tasks.schema import TaskDocument
from fixjury.tasks.validate import validate_corpus

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Frozen task corpus.")


@app.command("validate")
def validate_command(
    corpus: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, help="Corpus root, for example `benchmarks`."),
    ],
    min_tasks: Annotated[
        int, typer.Option(min=0, help="Fail when the corpus holds fewer tasks.")
    ] = 0,
) -> None:
    """Validate every task file, held-out bundle and the corpus index."""

    findings = validate_corpus(corpus, min_tasks=min_tasks)
    total = len(discover_task_paths(corpus))
    broken = {finding.where for finding in findings if finding.where.startswith("tasks/")}
    for finding in findings:
        typer.echo(str(finding))
    typer.echo(f"{max(total - len(broken), 0)}/{total} tasks valid")
    if findings:
        raise typer.Exit(code=1)


@app.command("freeze")
def freeze_command(
    draft: Annotated[
        Path, typer.Option(exists=True, dir_okay=False, help="Draft task file to freeze.")
    ],
    tasks_dir: Annotated[Path, typer.Option(help="Directory that holds frozen tasks.")] = Path(
        "benchmarks/tasks"
    ),
    fix_pr: Annotated[int | None, typer.Option(help="Pull request that merged the fix.")] = None,
    fix_commit: Annotated[
        str | None, typer.Option(help="Fix commit on the default branch.")
    ] = None,
    frozen_by: Annotated[
        str | None, typer.Option(help="Who freezes the task; defaults to the git identity.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Resolve facts and print the task, write nothing.")
    ] = False,
) -> None:
    """Freeze a draft: copy license, commits and dates from GitHub."""

    try:
        document = read_task(draft)
    except TaskFileError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    target = tasks_dir / document.id / "task.yaml"
    if target.exists():
        typer.echo(f"error: {target} already exists; a frozen task is never rewritten", err=True)
        raise typer.Exit(code=1)

    try:
        facts = asyncio.run(_collect(document, fix_pr=fix_pr, fix_commit=fix_commit))
        frozen = freeze_document(document, facts, frozen_by=frozen_by or default_frozen_by())
    except (FreezeError, GithubError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"repo        {facts.repo}")
    typer.echo(f"license     {facts.license}")
    typer.echo(f"issue       #{facts.issue_number} created {facts.issue_created_at}")
    typer.echo(f"base_commit {facts.base_commit}")
    typer.echo(f"fix_commit  {facts.fix_commit} merged {facts.fix_merged_at}")
    if dry_run:
        typer.echo(render_task(frozen), nl=False)
        return
    write_task(target, frozen)
    typer.echo(f"froze {frozen.id} -> {target}")


async def _collect(
    document: TaskDocument, *, fix_pr: int | None, fix_commit: str | None
) -> FreezeFacts:
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await collect_facts(
            client=client,
            api_url=settings.github_api_url,
            repo_url=document.repo,
            issue_number=document.issue.number,
            token=settings.github_token,
            fix_pr=fix_pr,
            fix_commit=fix_commit,
        )


@app.command("draft")
def draft_command(
    repo: Annotated[
        str,
        typer.Option(help="GitHub repository URL, for example https://github.com/pallets/click"),
    ],
    issue: Annotated[int, typer.Option(help="Issue number to turn into a task.")],
    task_id: Annotated[str, typer.Option("--id", help="Task id for the draft, for example T-001.")],
    fix_pr: Annotated[
        int, typer.Option(help="Merged pull request that fixed the issue; grounds allowed_paths.")
    ],
    out: Annotated[
        Path | None, typer.Option(help="Draft path; defaults to benchmarks/drafts/<id>.yaml.")
    ] = None,
    model: Annotated[
        str | None, typer.Option(help="Model name; defaults to FIXJURY_LLM_MODEL.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the draft, write nothing.")
    ] = False,
) -> None:
    """Draft a task file from an issue and the files its upstream fix touched."""

    target = out or Path("benchmarks/drafts") / f"{task_id}.yaml"
    if target.exists():
        typer.echo(f"error: {target} already exists; delete it or pass --out", err=True)
        raise typer.Exit(code=1)

    try:
        result = asyncio.run(
            _draft(repo=repo, issue=issue, task_id=task_id, fix_pr=fix_pr, model=model)
        )
    except (DraftError, LlmError, GithubError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"draft {result.document.id}: {result.tokens_in} in / {result.tokens_out} out tokens, "
        f"{result.attempts} attempt(s)"
    )
    rendered = render_task(result.document)
    if dry_run:
        typer.echo(rendered, nl=False)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8")
    typer.echo(f"wrote {target}")


async def _draft(
    *, repo: str, issue: int, task_id: str, fix_pr: int, model: str | None
) -> DraftResult:
    async with httpx.AsyncClient(timeout=settings.llm_timeout_s) as client:
        context = await load_issue(
            client=client,
            api_url=settings.github_api_url,
            repo_url=repo,
            issue_number=issue,
            token=settings.github_token,
        )
        candidates = await fix_candidate_paths(
            client=client,
            api_url=settings.github_api_url,
            repo_url=repo,
            pull_number=fix_pr,
            token=settings.github_token,
        )
        return await generate_draft(
            client=client,
            base_url=settings.llm_base_url,
            model=model or settings.llm_model,
            api_key=settings.llm_api_key,
            session=settings.llm_session,
            repo=repo,
            issue=context,
            task_id=task_id,
            candidate_paths=candidates,
        )
