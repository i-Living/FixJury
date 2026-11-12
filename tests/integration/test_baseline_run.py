from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from fixjury.agents.base import AgentResult, RunRequest
from fixjury.agents.baseline import run_baseline
from fixjury.sandbox import SandboxSpec, open_sandbox

pytestmark = pytest.mark.integration

IMAGE = "python:3.12-slim"


class ScriptedModel:
    """Answers with a fixed plan, so the run is reproducible byte for byte."""

    def __init__(self, *answers: str) -> None:
        self.answers = answers
        self.seen: list[list[dict[str, str]]] = []

    async def __call__(self, messages: list[dict[str, str]]) -> tuple[str, int, int]:
        self.seen.append([dict(message) for message in messages])
        index = min(len(self.seen) - 1, len(self.answers) - 1)
        return self.answers[index], 11, 7


def git(workdir: Path, *argv: str) -> None:
    subprocess.run(  # noqa: S603 - fixed argv, test repository
        ["git", *argv],  # noqa: S607 - git comes from PATH in tests
        cwd=workdir,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    git(tmp_path, "init", "--quiet", "--initial-branch=main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "--quiet", "-m", "initial")
    return tmp_path


def request_for(workspace: Path) -> RunRequest:
    return RunRequest(
        run_id="run-integration",
        repo_url="https://github.com/pallets/click",
        base_commit="a" * 40,
        spec="Fix the defect.",
        workspace=workspace,
        image=IMAGE,
        max_steps=5,
    )


def test_a_mocked_model_produces_a_reviewable_diff(workspace: Path) -> None:
    plan = (
        json.dumps({"tool": "read", "path": "module.py"}),
        json.dumps({"tool": "write", "path": "module.py", "content": "VALUE = 2\n"}),
        json.dumps({"tool": "exec", "argv": ["cat", "module.py"]}),
        json.dumps({"tool": "finish", "summary": "changed VALUE to 2", "unable": False}),
    )

    async def scenario() -> AgentResult:
        async with open_sandbox(SandboxSpec(image=IMAGE, workspace=workspace)) as sandbox:
            return await run_baseline(request_for(workspace), sandbox, ScriptedModel(*plan))

    result = asyncio.run(scenario())

    assert result.declared_success is True
    assert result.steps == 4
    assert result.tokens_in == 44
    assert result.tokens_out == 28
    assert "-VALUE = 1" in result.diff
    assert "+VALUE = 2" in result.diff
    assert len(result.diff_sha256) == 64
    assert (workspace / "module.py").read_text(encoding="utf-8") == "VALUE = 2\n"


def test_the_same_plan_produces_the_same_digest(workspace: Path) -> None:
    plan = (
        json.dumps({"tool": "write", "path": "module.py", "content": "VALUE = 3\n"}),
        json.dumps({"tool": "finish", "summary": "done"}),
    )

    async def once() -> str:
        async with open_sandbox(SandboxSpec(image=IMAGE, workspace=workspace)) as sandbox:
            result = await run_baseline(request_for(workspace), sandbox, ScriptedModel(*plan))
            return result.diff_sha256

    first = asyncio.run(once())
    git(workspace, "checkout", "--", "module.py")
    second = asyncio.run(once())

    assert first == second
