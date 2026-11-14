from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

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


def prepare(monkeypatch: pytest.MonkeyPatch, workspace: Path, *, base_broken: bool) -> list[str]:
    """Stub the environment: install fails whenever ``breaks-install`` is in the tree."""

    calls: list[str] = []

    async def no_checkout(repo: str, commit: str, dest: Path) -> None:
        return None

    async def install(spec: object, *, extra_env: dict[str, str] | None = None) -> None:
        if base_broken or (workspace / "breaks-install").exists():
            raise VerificationError("pip install -e . failed: no matching distribution")

    async def no_checks(*args: object) -> tuple[tuple[object, ...], bool | None, dict[str, str]]:
        return (), None, {}

    def apply(workspace_path: Path, diff: str) -> None:
        (workspace_path / "breaks-install").write_text("added by the diff\n")

    def restore(workspace_path: Path, commit: str) -> None:
        calls.append("restore")
        (workspace_path / "breaks-install").unlink(missing_ok=True)

    monkeypatch.setattr(runner, "checkout_workspace", no_checkout)
    monkeypatch.setattr(runner, "_install", install)
    monkeypatch.setattr(runner, "_run_checks", no_checks)
    monkeypatch.setattr(runner, "apply_patch", apply)
    monkeypatch.setattr(runner, "restore_base", restore)
    return calls


def verify(workspace: Path) -> VerificationResult:
    return asyncio.run(
        verify_workspace(DOCUMENT, PATCH, workspace=workspace, image="python:3.12-slim")
    )


def test_an_install_the_diff_breaks_is_a_failed_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The base builds; the diff does not. That is a result, not a missing observation."""

    calls = prepare(monkeypatch, tmp_path, base_broken=False)

    result = verify(tmp_path)

    assert "BUILD_BREAK" in {violation.code.name for violation in result.rejections}
    assert result.note is not None
    assert "breaks an install" in result.note
    assert calls == ["restore"]


def test_an_install_that_fails_on_the_base_commit_is_an_environment_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing could have been verified, so the pair must leave the metrics with a reason."""

    prepare(monkeypatch, tmp_path, base_broken=True)

    with pytest.raises(VerificationError, match="base commit too"):
        verify(tmp_path)
