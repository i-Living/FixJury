from __future__ import annotations

import asyncio
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from fixjury.agents.base import LlmConfig, RunRequest
from fixjury.agents.container_harness import ContainerHarnessAdapter, HarnessSpec

pytestmark = pytest.mark.integration

IMAGE = "python:3.12-slim"


def git(workdir: Path, *argv: str) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed argv, test repository
        ["git", *argv],  # noqa: S607 - git comes from PATH in tests
        cwd=workdir,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(tmp_path, "init", "--quiet", "--initial-branch=main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "--quiet", "-m", "initial")
    return tmp_path


def request_for(workspace: Path, *, timeout_s: int = 120) -> RunRequest:
    return RunRequest(
        run_id="test-run",
        repo_url="https://github.com/example/project",
        base_commit="a" * 40,
        spec="# Task\n\nFix the value.\n",
        workspace=workspace,
        image=IMAGE,
        timeout_s=timeout_s,
        llm=LlmConfig(base_url="http://127.0.0.1:9/v1", model="stub"),
    )


def test_the_harness_runs_in_a_container_and_the_platform_captures_the_diff(
    workspace: Path,
) -> None:
    adapter = ContainerHarnessAdapter(
        HarnessSpec(
            name="scripted",
            version="1",
            image=IMAGE,
            command=(
                "sh",
                "-c",
                # The harness must see the task text; grep fails the command if it does not.
                "grep -q 'Fix the value' TASK.md && printf 'VALUE = 2\\n' > src/module.py"
                " && echo patched",
            ),
        )
    )

    result = asyncio.run(adapter.run(request_for(workspace)))

    assert result.declared_success is True
    assert result.unable_reason is None
    assert "VALUE = 2" in result.diff
    assert len(result.diff_sha256) == 64
    assert result.summary == "patched"
    assert result.sandbox_seconds > 0
    # The task text reached the harness and never lands in the patch.
    assert "TASK.md" not in result.diff
    assert not (workspace / "TASK.md").exists()


def test_a_harness_that_does_nothing_reports_no_success(workspace: Path) -> None:
    adapter = ContainerHarnessAdapter(
        HarnessSpec(name="scripted", version="1", image=IMAGE, command=("sh", "-c", "true"))
    )

    result = asyncio.run(adapter.run(request_for(workspace)))

    assert result.diff == ""
    assert result.declared_success is False


def test_a_failing_harness_says_why(workspace: Path) -> None:
    adapter = ContainerHarnessAdapter(
        HarnessSpec(
            name="scripted",
            version="1",
            image=IMAGE,
            command=("sh", "-c", "printf 'VALUE = 2\\n' > src/module.py; exit 3"),
        )
    )

    result = asyncio.run(adapter.run(request_for(workspace)))

    assert result.declared_success is False
    assert result.unable_reason == "harness exited 3"
    assert "VALUE = 2" in result.diff, "a patch is still captured when the harness fails"


def test_the_harness_sees_the_proxy_and_nothing_on_the_allowlist(
    workspace: Path,
) -> None:
    """The proxy environment reaches the container; the allowlist is not the target."""

    adapter = ContainerHarnessAdapter(
        HarnessSpec(
            name="scripted",
            version="1",
            image=IMAGE,
            command=("sh", "-c", "echo $HTTPS_PROXY > proxy.txt"),
        )
    )

    result = asyncio.run(adapter.run(request_for(workspace)))

    assert result.declared_success is True
    assert "http://" in (workspace / "proxy.txt").read_text(encoding="utf-8")


def test_a_config_file_and_a_trajectory_stay_out_of_the_patch(workspace: Path) -> None:
    """What the platform writes for the harness is not the harness's change.

    This is the mini-swe-agent contract without mini: the config carries the
    endpoint (and therefore the key) and the trajectory carries the tokens. Both
    are read, both are removed, and neither may appear in the diff that a reviewer
    sees.
    """

    trajectory = (
        '{"trajectory_format": "mini-swe-agent-1.1",'
        ' "info": {"exit_status": "Submitted", "model_stats": {"api_calls": 3}},'
        ' "messages": [{"extra": {"response": {"usage":'
        ' {"prompt_tokens": 7, "completion_tokens": 4}}}}]}'
    )
    adapter = ContainerHarnessAdapter(
        HarnessSpec(
            name="scripted",
            version="1",
            image=IMAGE,
            command=(
                "sh",
                "-c",
                "grep -q 'step_limit: 7' fixjury-mini.yaml"
                " && printf 'VALUE = 2\\n' > src/module.py"
                f" && printf '%s' '{trajectory}' > fixjury-trajectory.json",
            ),
            config_file="fixjury-mini.yaml",
            config_text=lambda llm, max_steps: f"step_limit: {max_steps}\n",
            trajectory_file="fixjury-trajectory.json",
            trajectory_success_status="Submitted",
        )
    )
    request = replace(request_for(workspace), max_steps=7)

    result = asyncio.run(adapter.run(request))

    assert result.tokens_in == 7
    assert result.tokens_out == 4
    assert result.steps == 3
    assert result.declared_success is True
    assert "VALUE = 2" in result.diff
    for name in ("TASK.md", "fixjury-mini.yaml", "fixjury-trajectory.json"):
        assert name not in result.diff
        assert not (workspace / name).exists()


def test_a_harness_that_stopped_at_its_limit_did_not_declare_success(workspace: Path) -> None:
    """Exit code 0 with a diff is not a submission: the trajectory says which it was."""

    trajectory = (
        '{"trajectory_format": "mini-swe-agent-1.1", "info": {"exit_status": "LimitsExceeded"}}'
    )
    adapter = ContainerHarnessAdapter(
        HarnessSpec(
            name="scripted",
            version="1",
            image=IMAGE,
            command=(
                "sh",
                "-c",
                f"printf 'VALUE = 2\\n' > src/module.py"
                f" && printf '%s' '{trajectory}' > fixjury-trajectory.json",
            ),
            trajectory_file="fixjury-trajectory.json",
            trajectory_success_status="Submitted",
        )
    )

    result = asyncio.run(adapter.run(request_for(workspace)))

    assert "VALUE = 2" in result.diff
    assert result.declared_success is False
    assert result.unable_reason == "harness stopped with status LimitsExceeded"


def test_platform_file_names_can_already_exist_in_the_repository(workspace: Path) -> None:
    """A repository may track `TASK.md/` or a file named like the trajectory.

    The platform stashes them, runs, restores, and the patch shows only what the
    agent changed — no deletions invented by FixJury.
    """

    (workspace / "TASK.md").mkdir()
    (workspace / "TASK.md" / "notes.txt").write_text("repository content\n", encoding="utf-8")
    (workspace / "fixjury-trajectory.json").write_text('{"tracked": true}\n', encoding="utf-8")
    git(workspace, "add", ".")
    git(workspace, "commit", "--quiet", "-m", "tracked names collide with platform files")
    adapter = ContainerHarnessAdapter(
        HarnessSpec(
            name="scripted",
            version="1",
            image=IMAGE,
            command=("sh", "-c", "printf 'VALUE = 2\\n' > src/module.py"),
            trajectory_file="fixjury-trajectory.json",
        )
    )

    result = asyncio.run(adapter.run(request_for(workspace)))

    assert "VALUE = 2" in result.diff
    assert "TASK.md" not in result.diff
    assert "fixjury-trajectory" not in result.diff
    assert (workspace / "TASK.md" / "notes.txt").read_text(
        encoding="utf-8"
    ) == "repository content\n"
    assert (workspace / "fixjury-trajectory.json").read_text(
        encoding="utf-8"
    ) == '{"tracked": true}\n'


def test_an_unconfigured_harness_is_refused(workspace: Path) -> None:
    from fixjury.agents.container_harness import HarnessError

    adapter = ContainerHarnessAdapter(HarnessSpec(name="mini", version="", image="", command=()))

    with pytest.raises(HarnessError, match="no pinned image"):
        asyncio.run(adapter.run(request_for(workspace)))
