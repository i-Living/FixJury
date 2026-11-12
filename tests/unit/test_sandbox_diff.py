from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from fixjury.sandbox.diff import DiffError, UnreadableDiff, capture_diff, changed_files


def git(workdir: Path, *argv: str) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed argv, test repository
        ["git", *argv],  # noqa: S607 - git comes from PATH in tests
        cwd=workdir,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "--quiet", "--initial-branch=main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(tmp_path, "add", "module.py")
    git(tmp_path, "commit", "--quiet", "-m", "initial")
    return tmp_path


def test_untracked_file_reaches_the_diff(repo: Path) -> None:
    (repo / "new_module.py").write_text("def added() -> int:\n    return 1\n", encoding="utf-8")

    captured = capture_diff(repo)

    assert "new_module.py" in captured.diff
    assert "def added()" in captured.diff
    assert "new_module.py" in captured.files
    assert not captured.is_empty


def test_modified_file_reaches_the_diff(repo: Path) -> None:
    (repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")

    captured = capture_diff(repo)

    assert "-VALUE = 1" in captured.diff
    assert "+VALUE = 2" in captured.diff


def test_clean_workspace_produces_an_empty_diff(repo: Path) -> None:
    captured = capture_diff(repo)

    assert captured.is_empty
    assert captured.files == ()
    assert captured.sha256


def test_sha256_depends_on_the_content(repo: Path) -> None:
    (repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    first = capture_diff(repo)
    (repo / "module.py").write_text("VALUE = 3\n", encoding="utf-8")
    second = capture_diff(repo)

    assert first.sha256 != second.sha256
    assert len(first.sha256) == 64


def test_binary_content_is_refused(repo: Path) -> None:
    (repo / "blob.bin").write_bytes(bytes(range(256)) * 8)

    with pytest.raises(UnreadableDiff, match="binary"):
        capture_diff(repo)


def test_binary_content_can_be_captured_when_explicitly_allowed(repo: Path) -> None:
    (repo / "blob.bin").write_bytes(bytes(range(256)) * 8)

    captured = capture_diff(repo, allow_binary=True)

    assert captured.sha256
    assert "blob.bin" in captured.files


def test_changed_files_lists_new_and_modified_paths(repo: Path) -> None:
    (repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    (repo / "added.py").write_text("X = 1\n", encoding="utf-8")

    assert changed_files(repo) == ("added.py", "module.py")


def test_missing_workspace_is_reported(tmp_path: Path) -> None:
    with pytest.raises(DiffError, match="not a directory"):
        capture_diff(tmp_path / "absent")


def test_git_is_required(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)

    with pytest.raises(DiffError, match="git is not installed"):
        capture_diff(repo)
