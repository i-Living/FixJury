"""``fixjury tasks`` — corpus tooling (PLAN §8)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from fixjury.tasks.loader import discover_task_paths
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
