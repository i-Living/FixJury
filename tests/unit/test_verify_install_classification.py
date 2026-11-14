from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from fixjury.verify import runner
from fixjury.verify.runner import VerificationError, restore_base, verify_workspace
from fixjury.verify.verdict import VerificationResult

PATCH = """diff --git a/pyproject.toml b/pyproject.toml
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -1,2 +1,3 @@
 [project]
 name = "demo"
+requires-python = "<=3.0"
"""

DOCUMENT = SimpleNamespace(
    id="T-010",
    repo="https://example.test/repo",
    base_commit="0" * 40,
    allowed_paths=("pyproject.toml",),
    forbidden=(),
    checks=(),
)


class Environment:
    """The sandbox, scripted: ``installs`` says which attempts fail, in order."""

    def __init__(self, workspace: Path, installs: list[bool]) -> None:
        self.workspace = workspace
        self.installs = installs
        self.attempts = 0
        self.restored = 0
        self.checks_run = False


def prepare(monkeypatch: pytest.MonkeyPatch, env: Environment) -> Environment:
    async def no_checkout(repo: str, commit: str, dest: Path) -> None:
        return None

    async def install(spec: object, *, extra_env: dict[str, str] | None = None) -> None:
        broken = env.installs[env.attempts] if env.attempts < len(env.installs) else False
        env.attempts += 1
        if broken:
            raise VerificationError("pip install -e . failed: no matching distribution")

    async def no_checks(*args: object) -> tuple[tuple[object, ...], bool | None, dict[str, str]]:
        env.checks_run = True
        return (), None, {}

    def apply(workspace: Path, diff: str) -> None:
        (workspace / "pyproject.toml").write_text('[project]\nname = "demo"\n')

    def restore(workspace: Path, commit: str) -> None:
        env.restored += 1

    monkeypatch.setattr(runner, "checkout_workspace", no_checkout)
    monkeypatch.setattr(runner, "_install", install)
    monkeypatch.setattr(runner, "_run_checks", no_checks)
    monkeypatch.setattr(runner, "apply_patch", apply)
    monkeypatch.setattr(runner, "restore_base", restore)
    return env


def verify(workspace: Path) -> VerificationResult:
    return asyncio.run(
        verify_workspace(DOCUMENT, PATCH, workspace=workspace, image="python:3.12-slim")
    )


def test_an_install_the_diff_breaks_is_a_failed_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The base builds; the diff does not. That is a result, not a missing observation."""

    env = prepare(monkeypatch, Environment(tmp_path, installs=[True, True, False]))

    result = verify(tmp_path)

    assert "BUILD_BREAK" in {violation.code.name for violation in result.rejections}
    assert result.note is not None
    assert "breaks an install" in result.note
    assert env.restored == 1
    assert env.checks_run is False


def test_a_single_failed_install_is_retried_before_the_diff_is_blamed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resolver that times out once must not become a BUILD_BREAK verdict."""

    env = prepare(monkeypatch, Environment(tmp_path, installs=[True, False]))

    result = verify(tmp_path)

    assert "BUILD_BREAK" not in {violation.code.name for violation in result.rejections}
    assert env.checks_run is True
    assert env.restored == 0


def test_an_install_that_fails_on_the_base_commit_is_an_environment_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing could have been verified, so the pair leaves the metrics with a reason."""

    prepare(monkeypatch, Environment(tmp_path, installs=[True, True, True]))

    with pytest.raises(VerificationError, match="base commit too"):
        verify(tmp_path)


def test_restoring_the_base_removes_ignored_build_output(tmp_path: Path) -> None:
    """``.venv`` is gitignored, so a plain ``clean -fd`` would leave the patched one."""

    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".gitignore").write_text(".venv/\n")
    (workspace / "module.py").write_text("VALUE = 1\n")
    git = ["git", "-C", str(workspace)]
    subprocess.run([*git, "init", "--quiet"], check=True)  # noqa: S603
    subprocess.run([*git, "add", "."], check=True)  # noqa: S603
    subprocess.run(  # noqa: S603
        [*git, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "--quiet", "-m", "base"],
        check=True,
    )
    base = subprocess.run(  # noqa: S603
        [*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()

    (workspace / "module.py").write_text("VALUE = 2\n")
    (workspace / ".venv" / "bin").mkdir(parents=True)
    (workspace / ".venv" / "bin" / "python").write_text("# built by the diff\n")

    restore_base(workspace, base)

    assert (workspace / "module.py").read_text() == "VALUE = 1\n"
    assert not (workspace / ".venv").exists()
