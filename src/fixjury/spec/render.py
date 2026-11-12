"""Deterministic YAML rendering for task documents (PLAN §8)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import yaml

from fixjury.tasks.schema import TaskDocument


class _TaskDumper(yaml.SafeDumper):
    """Safe dumper that keeps multi-line strings readable."""


def _represent_string(dumper: yaml.SafeDumper, data: str) -> yaml.Node:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


def _represent_date(dumper: yaml.SafeDumper, data: dt.date) -> yaml.Node:
    return dumper.represent_scalar("tag:yaml.org,2002:timestamp", data.isoformat())


def _represent_datetime(dumper: yaml.SafeDumper, data: dt.datetime) -> yaml.Node:
    return dumper.represent_scalar("tag:yaml.org,2002:timestamp", data.isoformat())


_TaskDumper.add_representer(str, _represent_string)
_TaskDumper.add_representer(dt.date, _represent_date)
_TaskDumper.add_representer(dt.datetime, _represent_datetime)


def _issue_payload(document: TaskDocument) -> dict[str, Any]:
    issue: dict[str, Any] = {
        "number": document.issue.number,
        "url": document.issue.url,
        "created_at": document.issue.created_at,
        "fix_merged_at": document.issue.fix_merged_at,
        "body": document.issue.body,
    }
    return {key: value for key, value in issue.items() if value is not None}


def task_payload(document: TaskDocument) -> dict[str, Any]:
    """Build the mapping that is written to ``task.yaml``, in a stable order."""

    payload: dict[str, Any] = {
        "id": document.id,
        "repo": document.repo,
        "license": document.license,
        "base_commit": document.base_commit,
        "fix_commit": document.fix_commit,
        "issue": _issue_payload(document),
        "title": document.title,
        "acceptance": list(document.acceptance),
        "checks": list(document.checks),
        "forbidden": list(document.forbidden),
        "allowed_paths": list(document.allowed_paths),
        "difficulty": document.difficulty.value,
        "heldout_ref": document.heldout_ref,
        "cutoff_assumption": document.cutoff_assumption.value,
        "frozen_at": document.frozen_at,
        "frozen_by": document.frozen_by,
    }
    return {
        key: value
        for key, value in payload.items()
        if value is not None and not (key == "forbidden" and value == [])
    }


def render_task(document: TaskDocument) -> str:
    """Render a task document as YAML, always ending with a single newline."""

    text = yaml.dump(
        task_payload(document),
        Dumper=_TaskDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=100,
    )
    return text if text.endswith("\n") else text + "\n"
