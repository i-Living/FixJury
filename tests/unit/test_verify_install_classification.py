from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from fixjury.sandbox.container import SandboxSpec
from fixjury.verify import runner
from fixjury.verify.runner import VerificationError, verify_workspace
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
    """The sandbox, scripted: ``installs`` says which patched attempts fail, in order."""

    def __init__(self, workspace: Path, installs: list[bool], *, base_broken: bool = False) -> None:
        self.workspace = workspace
        self.installs = installs
        self.base_broken = base_broken
        self.attempts = 0
        self.checkouts: list[Path] = []
        self.installed: list[Path] = []
        self.checks_run = False

    @property
    def base_paths(self) -> list[Path]:
        return [path for path in self.installed if path != self.workspace]


def prepare(monkeypatch: pytest.MonkeyPatch, env: Environment) -> Environment:
    async def checkout(repo: str, commit: str, dest: Path) -> None:
        env.checkouts.append(dest)
        if dest != env.workspace:
            await asyncio.to_thread(dest.mkdir, parents=True, exist_ok=True)
            (dest / "checked-out").write_text("the base commit\n")

    async def install(spec: SandboxSpec, *, extra_env: dict[str, str] | None = None) -> None:
        space = spec.workspace
        env.installed.append(space)
        if space != env.workspace:
            broken = env.base_broken
        else:
            broken = env.installs[env.attempts] if env.attempts < len(env.installs) else False
            env.attempts += 1
        if broken:
            raise VerificationError("pip install -e . failed: no matching distribution")

    async def no_checks(*args: object) -> tuple[tuple[object, ...], bool | None, dict[str, str]]:
        env.checks_run = True
        return (), None, {}

    def apply(workspace: Path, diff: str) -> None:
        (workspace / "pyproject.toml").write_text('[project]\nname = "demo"\n')

    monkeypatch.setattr(runner, "checkout_workspace", checkout)
    monkeypatch.setattr(runner, "_install", install)
    monkeypatch.setattr(runner, "_run_checks", no_checks)
    monkeypatch.setattr(runner, "apply_patch", apply)
    return env


def verify(workspace: Path) -> VerificationResult:
    return asyncio.run(
        verify_workspace(DOCUMENT, PATCH, workspace=workspace, image="python:3.12-slim")
    )


def test_an_install_the_diff_breaks_is_a_failed_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The base builds; the diff does not. That is a result, not a missing observation."""

    env = prepare(monkeypatch, Environment(tmp_path, installs=[True, True]))

    result = verify(tmp_path)

    assert "BUILD_BREAK" in {violation.code.name for violation in result.rejections}
    assert result.note is not None
    assert "breaks an install" in result.note
    assert env.checks_run is False


def test_the_base_comparison_never_touches_the_workspace_the_installer_saw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The patched install runs code from the diff, so ``.git`` there is not trustworthy."""

    env = prepare(monkeypatch, Environment(tmp_path, installs=[True, True]))

    verify(tmp_path)

    assert env.base_paths == [tmp_path.with_name(f"{tmp_path.name}-base")]
    assert env.checkouts == [tmp_path, tmp_path.with_name(f"{tmp_path.name}-base")]
    # A checkout of its own, and gone again once it has answered the question.
    assert not env.base_paths[0].exists()


def test_a_single_failed_install_is_retried_before_the_diff_is_blamed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resolver that times out once must not become a BUILD_BREAK verdict."""

    env = prepare(monkeypatch, Environment(tmp_path, installs=[True, False]))

    result = verify(tmp_path)

    assert "BUILD_BREAK" not in {violation.code.name for violation in result.rejections}
    assert env.checks_run is True
    assert env.base_paths == []


def test_an_install_that_fails_on_the_base_commit_is_an_environment_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing could have been verified, so the pair leaves the metrics with a reason."""

    prepare(monkeypatch, Environment(tmp_path, installs=[True, True], base_broken=True))

    with pytest.raises(VerificationError, match="base commit too"):
        verify(tmp_path)
