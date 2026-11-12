from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from fixjury.agents.base import RunRequest
from fixjury.agents.baseline import ProtocolError, _apply_step, parse_step, run_baseline
from fixjury.sandbox.container import SandboxHandle, SandboxSpec


@dataclass
class FakeContainer:
    """Records commands and pretends to execute them."""

    outputs: dict[str, tuple[int, bytes]] = field(default_factory=dict)
    calls: list[list[str]] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)

    def exec_run(self, cmd: list[str], workdir: str | None = None) -> SimpleNamespace:
        self.calls.append(list(cmd))
        key = " ".join(cmd)
        exit_code, output = self.outputs.get(key, (0, b""))
        return SimpleNamespace(output=output, exit_code=exit_code)

    def put_archive(self, path: str, data: bytes) -> bool:
        self.writes.append(path)
        return True


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


def request_for(workspace: Path, *, max_steps: int = 5) -> RunRequest:
    return RunRequest(
        run_id="run-1",
        repo_url="https://github.com/pallets/click",
        base_commit="a" * 40,
        spec="Fix the defect.",
        workspace=workspace,
        image="python:3.12-slim",
        max_steps=max_steps,
    )


def handle_for(container: FakeContainer) -> SandboxHandle:
    spec = SandboxSpec(image="python:3.12-slim", workspace=Path("/workspace"))
    return SandboxHandle(container=container, spec=spec)


class ScriptedModel:
    """A model that answers in order and records the conversation it saw."""

    def __init__(self, *answers: str) -> None:
        self.answers = answers
        self.seen: list[list[dict[str, str]]] = []

    async def __call__(self, messages: list[dict[str, str]]) -> tuple[str, int, int]:
        self.seen.append([dict(message) for message in messages])
        index = min(len(self.seen) - 1, len(self.answers) - 1)
        return self.answers[index], 10, 5


def test_parse_step_reads_a_plain_object() -> None:
    step = parse_step('{"tool": "exec", "argv": ["pytest", "-q"]}')

    assert step.tool == "exec"
    assert step.arguments["argv"] == ["pytest", "-q"]


def test_parse_step_tolerates_a_markdown_fence() -> None:
    step = parse_step('```json\n{"tool": "finish", "summary": "done"}\n```')

    assert step.tool == "finish"


@pytest.mark.parametrize("answer", ["no json here", '{"tool": "drop_database"}', ""])
def test_parse_step_rejects_answers_it_cannot_execute(answer: str) -> None:
    with pytest.raises(ProtocolError):
        parse_step(answer)


def test_a_non_list_argv_never_reaches_the_container() -> None:
    container = FakeContainer()
    step = parse_step('{"tool": "exec", "argv": "pytest"}')

    with pytest.raises(ProtocolError):
        asyncio.run(_apply_step(handle_for(container), step))
    assert container.calls == []


def test_the_loop_executes_a_tool_and_captures_the_diff(workspace: Path) -> None:
    container = FakeContainer(outputs={"pytest -q": (0, b"1 passed\n")})
    model = ScriptedModel(
        json.dumps({"tool": "exec", "argv": ["pytest", "-q"]}),
        json.dumps({"tool": "finish", "summary": "tests pass", "unable": False}),
    )

    result = asyncio.run(run_baseline(request_for(workspace), handle_for(container), model))

    assert container.calls == [["pytest", "-q"]]
    assert result.declared_success is True
    assert result.unable_reason is None
    assert result.summary == "tests pass"
    assert result.steps == 2
    assert result.tokens_in == 20
    assert result.tokens_out == 10
    assert len(result.diff_sha256) == 64


def test_the_observation_reaches_the_next_turn(workspace: Path) -> None:
    container = FakeContainer(outputs={"pytest -q": (1, b"1 failed\n")})
    model = ScriptedModel(
        json.dumps({"tool": "exec", "argv": ["pytest", "-q"]}),
        json.dumps({"tool": "finish", "summary": "done"}),
    )

    asyncio.run(run_baseline(request_for(workspace), handle_for(container), model))

    second = model.seen[1]
    assert second[-1]["role"] == "user"
    assert "1 failed" in second[-1]["content"]


def test_a_protocol_error_is_fed_back_instead_of_crashing(workspace: Path) -> None:
    model = ScriptedModel("I think the bug is in module.py", json.dumps({"tool": "finish"}))

    result = asyncio.run(run_baseline(request_for(workspace), handle_for(FakeContainer()), model))

    assert result.steps == 2
    assert "error:" in model.seen[1][-1]["content"]
    assert result.declared_success is True


def test_running_out_of_steps_is_reported(workspace: Path) -> None:
    model = ScriptedModel(json.dumps({"tool": "list", "path": "."}))

    result = asyncio.run(
        run_baseline(request_for(workspace, max_steps=3), handle_for(FakeContainer()), model)
    )

    assert result.steps == 3
    assert result.declared_success is False
    assert result.unable_reason is not None
    assert "step limit" in result.unable_reason


def test_an_unable_answer_keeps_the_reason(workspace: Path) -> None:
    model = ScriptedModel(json.dumps({"tool": "finish", "summary": "no idea", "unable": True}))

    result = asyncio.run(run_baseline(request_for(workspace), handle_for(FakeContainer()), model))

    assert result.declared_success is False
    assert result.unable_reason == "model reported unable"


def test_a_binary_edit_is_rejected_as_unreadable(workspace: Path) -> None:
    (workspace / "blob.bin").write_bytes(bytes(range(256)) * 8)
    model = ScriptedModel(json.dumps({"tool": "finish", "summary": "done"}))

    result = asyncio.run(run_baseline(request_for(workspace), handle_for(FakeContainer()), model))

    assert result.declared_success is False
    assert result.unable_reason is not None
    assert "UNREADABLE_DIFF" in result.unable_reason
    assert result.diff == ""


def test_the_prompt_carries_the_spec_and_the_tool_contract() -> None:
    from fixjury.agents.baseline import load_prompt

    prompt = load_prompt()

    assert "{spec}" in prompt
    assert '"tool": "finish"' in prompt
