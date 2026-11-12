"""Freezing a task draft into a corpus task (PLAN §8, task 1.2).

The draft carries the human decisions (what the fix must achieve, which checks
count, which paths are in scope). GitHub supplies the facts that must not be
guessed: license, issue snapshot, ``base_commit``, ``fix_commit`` and both
dates. Freezing copies the facts in and refuses to overwrite a frozen file.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
from dataclasses import dataclass

import httpx

from fixjury.github import (
    JsonObject,
    get_array,
    get_object,
    optional_object,
    optional_string,
    repo_api_url,
    require_array,
    require_int,
    require_string,
)
from fixjury.tasks.schema import ALLOWED_LICENSES, CUTOFF_DATE, TaskDocument


class FreezeError(RuntimeError):
    """The draft cannot be frozen as it stands."""


@dataclass(frozen=True)
class FreezeFacts:
    """Facts copied from GitHub when a task is frozen."""

    repo: str
    license: str
    default_branch: str
    issue_number: int
    issue_url: str
    issue_title: str
    issue_body: str
    issue_created_at: dt.date
    base_commit: str
    fix_commit: str
    fix_merged_at: dt.date


def _iso_date(value: str, *, context: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError as exc:
        raise FreezeError(f"{context}: {value!r} is not an ISO date") from exc


def _iso_datetime(value: str, *, context: str) -> dt.datetime:
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FreezeError(f"{context}: {value!r} is not an ISO timestamp") from exc


async def collect_facts(
    *,
    client: httpx.AsyncClient,
    api_url: str,
    repo_url: str,
    issue_number: int,
    token: str = "",
    fix_pr: int | None = None,
    fix_commit: str | None = None,
) -> FreezeFacts:
    """Read everything a frozen task needs from GitHub."""

    if fix_pr is not None and fix_commit is not None:
        raise FreezeError("pass either fix_pr or fix_commit, not both")

    api = repo_api_url(api_url, repo_url)
    repository = await get_object(client, api, token=token)
    license_object = optional_object(repository, "license") or {}
    license_id = optional_string(license_object, "spdx_id")
    if license_id is None or license_id not in ALLOWED_LICENSES:
        allowed = ", ".join(ALLOWED_LICENSES)
        raise FreezeError(f"repository license {license_id!r} is not one of {allowed}")
    default_branch = require_string(repository, "default_branch", context="repository")

    issue = await get_object(client, f"{api}/issues/{issue_number}", token=token)
    if "pull_request" in issue:
        raise FreezeError(f"issue #{issue_number} is a pull request, not an issue")
    issue_url = require_string(issue, "html_url", context="issue")
    issue_title = require_string(issue, "title", context="issue")
    issue_body = optional_string(issue, "body") or ""
    issue_created_at = _iso_date(
        require_string(issue, "created_at", context="issue"), context="issue.created_at"
    )
    if issue_created_at < CUTOFF_DATE:
        raise FreezeError(
            f"issue was created on {issue_created_at} and predates the "
            f"{CUTOFF_DATE.isoformat()} contamination cut-off"
        )

    resolved_commit, merged_at = await _resolve_fix(
        client=client,
        api=api,
        token=token,
        issue_number=issue_number,
        default_branch=default_branch,
        fix_pr=fix_pr,
        fix_commit=fix_commit,
    )
    if merged_at.date() < CUTOFF_DATE:
        raise FreezeError(
            f"fix was merged on {merged_at.date()} and predates the "
            f"{CUTOFF_DATE.isoformat()} contamination cut-off"
        )

    commit = await get_object(client, f"{api}/commits/{resolved_commit}", token=token)
    parents = require_array(commit, "parents", context="commit")
    if not parents:
        raise FreezeError(f"commit {resolved_commit} has no parents; cannot derive base_commit")
    base_commit = require_string(parents[0], "sha", context="commit.parents[0]")

    return FreezeFacts(
        repo=repo_url.rstrip("/"),
        license=license_id,
        default_branch=default_branch,
        issue_number=issue_number,
        issue_url=issue_url,
        issue_title=issue_title,
        issue_body=issue_body,
        issue_created_at=issue_created_at,
        base_commit=base_commit,
        fix_commit=resolved_commit,
        fix_merged_at=merged_at.date(),
    )


async def _resolve_fix(
    *,
    client: httpx.AsyncClient,
    api: str,
    token: str,
    issue_number: int,
    default_branch: str,
    fix_pr: int | None,
    fix_commit: str | None,
) -> tuple[str, dt.datetime]:
    if fix_pr is not None:
        pull = await get_object(client, f"{api}/pulls/{fix_pr}", token=token)
        if pull.get("merged") is not True:
            raise FreezeError(f"pull request #{fix_pr} is not merged")
        commit = require_string(pull, "merge_commit_sha", context="pull request")
        merged_at = _iso_datetime(
            require_string(pull, "merged_at", context="pull request"),
            context="pull request.merged_at",
        )
        await _assert_on_default_branch(
            client=client, api=api, token=token, commit=commit, default_branch=default_branch
        )
        return commit, merged_at

    commit = (
        fix_commit.lower()
        if fix_commit is not None
        else await _closing_commit(client=client, api=api, token=token, issue_number=issue_number)
    )

    merged_pull = await _merged_pull_request(client=client, api=api, token=token, commit=commit)
    if merged_pull is None:
        raise FreezeError(f"commit {commit} is not part of a merged pull request")
    merged_at = _iso_datetime(
        require_string(merged_pull, "merged_at", context="pull request"),
        context="pull request.merged_at",
    )
    await _assert_on_default_branch(
        client=client, api=api, token=token, commit=commit, default_branch=default_branch
    )
    return commit, merged_at


async def _merged_pull_request(
    *, client: httpx.AsyncClient, api: str, token: str, commit: str
) -> JsonObject | None:
    pulls = await get_array(client, f"{api}/commits/{commit}/pulls", token=token)
    exact = [
        pull
        for pull in pulls
        if pull.get("merged") is True and pull.get("merge_commit_sha") == commit
    ]
    merged = exact or [pull for pull in pulls if pull.get("merged") is True]
    return merged[0] if merged else None


async def _assert_on_default_branch(
    *, client: httpx.AsyncClient, api: str, token: str, commit: str, default_branch: str
) -> None:
    compare = await get_object(client, f"{api}/compare/{commit}...{default_branch}", token=token)
    status = require_string(compare, "status", context="compare")
    if status not in {"ahead", "identical"}:
        raise FreezeError(
            f"commit {commit} is not on {default_branch} (compare status {status!r}); "
            "base_commit would not be a merge-base"
        )


async def _closing_commit(
    *, client: httpx.AsyncClient, api: str, token: str, issue_number: int
) -> str:
    events = await get_array(
        client,
        f"{api}/issues/{issue_number}/timeline",
        token=token,
        params={"per_page": "100"},
    )
    closing = [
        optional_string(event, "commit_id")
        for event in events
        if optional_string(event, "event") == "closed"
    ]
    commits = [commit for commit in closing if commit]
    if commits:
        return commits[-1]

    candidates: list[int] = []
    for event in events:
        if optional_string(event, "event") != "cross-referenced":
            continue
        source = optional_object(event, "source") or {}
        source_issue = optional_object(source, "issue") or {}
        if optional_object(source_issue, "pull_request") is not None:
            candidates.append(require_int(source_issue, "number", context="cross-referenced issue"))
    for number in reversed(candidates):
        pull = await get_object(client, f"{api}/pulls/{number}", token=token)
        if pull.get("merged") is True:
            return require_string(pull, "merge_commit_sha", context="pull request")

    raise FreezeError(
        f"issue #{issue_number} is not closed by a merged pull request; "
        "pass --fix-pr or --fix-commit explicitly"
    )


def default_frozen_by() -> str:
    """Who froze the task: git identity first, then ``$USER``."""

    git = shutil.which("git")
    result: subprocess.CompletedProcess[str] | None = None
    if git is not None:
        try:
            result = subprocess.run(  # noqa: S603 - resolved path, fixed argv, no shell
                [git, "config", "user.name"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            result = None
    if result is not None and result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return os.environ.get("USER", "unknown").strip() or "unknown"


def freeze_document(
    document: TaskDocument,
    facts: FreezeFacts,
    *,
    frozen_by: str,
    now: dt.datetime | None = None,
) -> TaskDocument:
    """Return the frozen form of a draft, with every GitHub fact filled in."""

    if document.frozen:
        raise FreezeError(
            f"task {document.id} is already frozen; add a new version (T-xxx-v2) instead"
        )
    if not frozen_by.strip():
        raise FreezeError("frozen_by must not be empty")
    if document.repo != facts.repo:
        raise FreezeError(f"draft repo {document.repo!r} does not match {facts.repo!r}")
    if document.issue.number != facts.issue_number:
        raise FreezeError(
            f"draft issue number {document.issue.number} does not match {facts.issue_number}"
        )

    conflicts = {
        "license": (document.license, facts.license),
        "base_commit": (document.base_commit, facts.base_commit),
        "fix_commit": (document.fix_commit, facts.fix_commit),
    }
    for field, (drafted, actual) in conflicts.items():
        if drafted is not None and drafted != actual:
            raise FreezeError(f"draft {field} {drafted!r} does not match GitHub value {actual!r}")

    expected_ref = f"benchmarks/heldout/{document.id}"
    if document.heldout_ref is not None and document.heldout_ref != expected_ref:
        raise FreezeError(f"draft heldout_ref {document.heldout_ref!r} must be {expected_ref!r}")

    payload = document.model_dump()
    payload["issue"] = {
        **payload["issue"],
        "created_at": facts.issue_created_at,
        "fix_merged_at": facts.fix_merged_at,
        "body": facts.issue_body,
    }
    payload.update(
        {
            "license": facts.license,
            "base_commit": facts.base_commit,
            "fix_commit": facts.fix_commit,
            "heldout_ref": expected_ref,
            "cutoff_assumption": "clean",
            "frozen_at": now or dt.datetime.now(dt.UTC).replace(microsecond=0),
            "frozen_by": frozen_by.strip(),
        }
    )
    return TaskDocument.model_validate(payload)
