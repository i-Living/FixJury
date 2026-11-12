from __future__ import annotations

import asyncio
import datetime as dt

import httpx
import pytest

from fixjury.github import GithubError, parse_repo_url
from fixjury.tasks.freeze import (
    FreezeError,
    FreezeFacts,
    collect_facts,
    default_frozen_by,
    freeze_document,
)
from fixjury.tasks.schema import TaskDocument

BASE_SHA = "a" * 40
FIX_SHA = "b" * 40
API = "https://api.github.com"
REPO = "https://github.com/pallets/click"


def make_draft(**overrides: object) -> TaskDocument:
    payload: dict[str, object] = {
        "id": "T-001",
        "repo": REPO,
        "issue": {
            "number": 2712,
            "url": "https://github.com/pallets/click/issues/2712",
        },
        "title": "Completion breaks for dashed options",
        "acceptance": ["completion output matches the option name"],
        "checks": ["pytest tests/test_shell_completion.py"],
        "allowed_paths": ["src/click/shell_completion.py", "tests/"],
        "difficulty": "medium",
    }
    payload.update(overrides)
    return TaskDocument.model_validate(payload)


def routes_for_success() -> dict[str, object]:
    return {
        "/repos/pallets/click": {
            "license": {"spdx_id": "BSD-3-Clause"},
            "default_branch": "main",
        },
        "/repos/pallets/click/issues/2712": {
            "number": 2712,
            "html_url": "https://github.com/pallets/click/issues/2712",
            "title": "Dashed option names break completion",
            "body": "Completion output for --foo-bar is wrong.\n",
            "created_at": "2025-09-14T10:00:00Z",
        },
        "/repos/pallets/click/pulls/2800": {
            "merged": True,
            "merge_commit_sha": FIX_SHA,
            "merged_at": "2025-10-02T11:00:00Z",
        },
        f"/repos/pallets/click/compare/{FIX_SHA}...main": {"status": "ahead"},
        f"/repos/pallets/click/commits/{FIX_SHA}": {"parents": [{"sha": BASE_SHA}]},
    }


def client_for(routes: dict[str, object]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = routes.get(request.url.path)
        if payload is None:
            return httpx.Response(404, json={"message": f"no route for {request.url.path}"})
        return httpx.Response(200, json=payload)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def collect(
    routes: dict[str, object], *, fix_pr: int | None = None, fix_commit: str | None = None
) -> FreezeFacts:
    async def run() -> FreezeFacts:
        async with client_for(routes) as client:
            return await collect_facts(
                client=client,
                api_url=API,
                repo_url=REPO,
                issue_number=2712,
                fix_pr=fix_pr,
                fix_commit=fix_commit,
            )

    return asyncio.run(run())


def test_parse_repo_url() -> None:
    assert parse_repo_url(REPO) == ("pallets", "click")
    with pytest.raises(GithubError):
        parse_repo_url("https://gitlab.com/pallets/click")


def test_collect_facts_with_explicit_pull_request() -> None:
    facts = collect(routes_for_success(), fix_pr=2800)

    assert facts.license == "BSD-3-Clause"
    assert facts.base_commit == BASE_SHA
    assert facts.fix_commit == FIX_SHA
    assert facts.issue_created_at == dt.date(2025, 9, 14)
    assert facts.fix_merged_at == dt.date(2025, 10, 2)
    assert facts.issue_body == "Completion output for --foo-bar is wrong.\n"


def test_collect_facts_finds_the_fix_through_the_issue_timeline() -> None:
    routes = routes_for_success()
    routes["/repos/pallets/click/issues/2712/timeline"] = [
        {"event": "labeled"},
        {"event": "closed", "commit_id": FIX_SHA},
    ]
    routes[f"/repos/pallets/click/commits/{FIX_SHA}/pulls"] = [
        {"merged": True, "merge_commit_sha": FIX_SHA, "merged_at": "2025-10-02T11:00:00Z"}
    ]

    facts = collect(routes)

    assert facts.fix_commit == FIX_SHA


def test_collect_facts_rejects_a_non_permissive_license() -> None:
    routes = routes_for_success()
    routes["/repos/pallets/click"] = {
        "license": {"spdx_id": "GPL-3.0"},
        "default_branch": "main",
    }

    with pytest.raises(FreezeError, match="license"):
        collect(routes, fix_pr=2800)


def test_collect_facts_rejects_a_pre_cutoff_issue() -> None:
    routes = routes_for_success()
    routes["/repos/pallets/click/issues/2712"] = {
        "number": 2712,
        "html_url": "https://github.com/pallets/click/issues/2712",
        "title": "old",
        "body": "old",
        "created_at": "2025-03-10T10:00:00Z",
    }

    with pytest.raises(FreezeError, match="cut-off"):
        collect(routes, fix_pr=2800)


def test_collect_facts_rejects_an_unmerged_pull_request() -> None:
    routes = routes_for_success()
    routes["/repos/pallets/click/pulls/2800"] = {"merged": False}

    with pytest.raises(FreezeError, match="not merged"):
        collect(routes, fix_pr=2800)


def test_collect_facts_rejects_a_fix_outside_the_default_branch() -> None:
    routes = routes_for_success()
    routes[f"/repos/pallets/click/compare/{FIX_SHA}...main"] = {"status": "diverged"}

    with pytest.raises(FreezeError, match="not on main"):
        collect(routes, fix_pr=2800)


def test_collect_facts_rejects_a_pull_request_argument_and_commit_together() -> None:
    with pytest.raises(FreezeError, match="either"):
        collect(routes_for_success(), fix_pr=2800, fix_commit=FIX_SHA)


def test_collect_facts_reports_an_issue_that_is_not_closed_by_a_merge() -> None:
    routes = routes_for_success()
    routes["/repos/pallets/click/issues/2712/timeline"] = [{"event": "closed", "commit_id": None}]

    with pytest.raises(FreezeError, match="not closed by a merged pull request"):
        collect(routes)


def test_collect_facts_reports_a_missing_route_as_a_github_error() -> None:
    with pytest.raises(GithubError, match="404"):
        collect({}, fix_pr=2800)


def test_freeze_document_fills_every_fact() -> None:
    facts = collect(routes_for_success(), fix_pr=2800)

    frozen = freeze_document(
        make_draft(),
        facts,
        frozen_by="owner",
        now=dt.datetime(2026, 11, 12, 12, 0, tzinfo=dt.UTC),
    )

    assert frozen.frozen is True
    assert frozen.base_commit == BASE_SHA
    assert frozen.fix_commit == FIX_SHA
    assert frozen.license == "BSD-3-Clause"
    assert frozen.heldout_ref == "benchmarks/heldout/T-001"
    assert frozen.cutoff_assumption.value == "clean"
    assert frozen.frozen_by == "owner"
    assert frozen.frozen_at == dt.datetime(2026, 11, 12, 12, 0, tzinfo=dt.UTC)
    assert frozen.issue.fix_merged_at == dt.date(2025, 10, 2)


def test_freeze_document_refuses_a_frozen_task() -> None:
    facts = collect(routes_for_success(), fix_pr=2800)
    frozen = freeze_document(make_draft(), facts, frozen_by="owner")

    with pytest.raises(FreezeError, match="already frozen"):
        freeze_document(frozen, facts, frozen_by="owner")


def test_freeze_document_reports_conflicting_drafts() -> None:
    facts = collect(routes_for_success(), fix_pr=2800)

    with pytest.raises(FreezeError, match="base_commit"):
        freeze_document(make_draft(base_commit="c" * 40), facts, frozen_by="owner")


def test_freeze_document_requires_a_frozen_by() -> None:
    facts = collect(routes_for_success(), fix_pr=2800)

    with pytest.raises(FreezeError, match="frozen_by"):
        freeze_document(make_draft(), facts, frozen_by="  ")


def test_default_frozen_by_is_not_empty() -> None:
    assert default_frozen_by().strip()
