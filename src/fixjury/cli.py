from __future__ import annotations

import typer

from fixjury import __version__
from fixjury.tasks.cli import app as tasks_app

app = typer.Typer(no_args_is_help=True, add_completion=False)
app.add_typer(tasks_app, name="tasks")


@app.callback()
def main() -> None:
    """FixJury — agent-agnostic reliability layer for autonomous code repair."""


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)
