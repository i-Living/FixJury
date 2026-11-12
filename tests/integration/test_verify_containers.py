from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from fixjury import verify as verify_module
from fixjury.sandbox.container import SandboxSpec
from fixjury.tasks.schema import TaskDocument
from fixjury.verify.policy import Violation
from fixjury.verify.runner import verify_workspace

pytestmark = pytest.mark.integration

IMAGE = "python:3.12-slim"

DOCUMENT = TaskDocument(
    id="T-900",
    repo="https://github.com/example/project",
    license="MIT",
    base_commit="a" * 40,
    issue={
        "number": 1,
        "url": "https://github.com/example/project/issues/1",
        "body": "the helper returns the wrong value",
    },
    title="helper returns the wrong value",
    acceptance=("the helper returns 2",),
    checks=("pytest tests/test_api.py", "ruff check src", "mypy src"),
    allowed_paths=(".",),
    difficulty="easy",
)

MODULE_PATCH = (
    "diff --git a/src/module.py b/src/module.py\n"
    "--- a/src/module.py\n"
    "+++ b/src/module.py\n"
    "@@ -1 +1 @@\n"
    "-VALUE = 1\n"
    "+VALUE = 2\n"
)


def fake_tool(directory: Path, name: str, exit_code: int = 0) -> None:
    """A stand-in for an installed tool, so no dependency install is needed."""

    path = directory / name
    path.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_api.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    tools = tmp_path / ".venv" / "bin"
    tools.mkdir(parents=True)
    for name in ("pytest", "ruff", "mypy"):
        fake_tool(tools, name)
    return tmp_path


def prepare(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify_module.runner, "checkout_base", lambda *args: None)

    async def no_install(spec: SandboxSpec) -> None:
        return None

    monkeypatch.setattr(verify_module.runner, "_install", no_install)


def test_a_good_patch_passes_every_check(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare(monkeypatch)

    result = asyncio.run(verify_workspace(DOCUMENT, MODULE_PATCH, workspace=workspace, image=IMAGE))

    assert result.tests_pass is True
    assert result.lint_pass is True
    assert result.type_pass is True
    assert result.policy_pass is True
    assert result.heldout_pass is None
    assert result.automated_pass is True
    assert result.rejections == ()
    assert result.tool_versions["python"].startswith("Python 3.12")


def test_a_failing_check_blocks_the_pass(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare(monkeypatch)
    fake_tool(workspace / ".venv" / "bin", "pytest", exit_code=1)

    result = asyncio.run(verify_workspace(DOCUMENT, MODULE_PATCH, workspace=workspace, image=IMAGE))

    assert result.tests_pass is False
    assert result.automated_pass is False
    assert [outcome.exit_code for outcome in result.outcomes if outcome.tool.value == "pytest"] == [
        1
    ]


def test_a_patch_outside_allowed_paths_fails_policy_without_a_container(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare(monkeypatch)
    document = DOCUMENT.model_copy(update={"allowed_paths": ("src/module.py",)})
    diff = MODULE_PATCH.replace("src/module.py", "tests/test_api.py")

    result = asyncio.run(verify_workspace(document, diff, workspace=workspace, image=IMAGE))

    assert result.policy_pass is False
    assert result.automated_pass is False
    assert any("outside allowed_paths" in violation.note for violation in result.rejections)


def test_a_patch_that_does_not_apply_is_a_build_break(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare(monkeypatch)
    broken = MODULE_PATCH.replace("-VALUE = 1", "-VALUE = 99")

    result = asyncio.run(verify_workspace(DOCUMENT, broken, workspace=workspace, image=IMAGE))

    assert result.automated_pass is False
    assert result.note == "the diff does not apply to the base commit"
    assert any(isinstance(item, Violation) for item in result.rejections)
