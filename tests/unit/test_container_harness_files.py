"""Writing platform files into a workspace somebody else owns."""

from __future__ import annotations

from pathlib import Path

import pytest

from fixjury.agents.container_harness import (
    exposes_secret,
    restore_workspace_entry,
    stash_workspace_entry,
    write_workspace_file,
)


def test_a_symlink_committed_by_the_repository_is_moved_away_not_followed(tmp_path: Path) -> None:
    """Writing through one would put the API key wherever the repository pointed."""

    outside = tmp_path / "outside.txt"
    outside.write_text("untouched")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "fixjury-mini.yaml").symlink_to(outside)

    stash = stash_workspace_entry(workspace, "fixjury-mini.yaml")
    write_workspace_file(workspace, "fixjury-mini.yaml", "step_limit: 3\n")

    assert outside.read_text() == "untouched"
    assert (workspace / "fixjury-mini.yaml").read_text() == "step_limit: 3\n"

    restore_workspace_entry(workspace, "fixjury-mini.yaml", stash)

    assert (workspace / "fixjury-mini.yaml").is_symlink(), "the repository's symlink comes back"
    assert outside.read_text() == "untouched"


def test_a_tracked_directory_survives_and_its_files_are_restored(tmp_path: Path) -> None:
    """A repository may legitimately track `TASK.md/`: the platform must not delete it."""

    workspace = tmp_path / "workspace"
    tracked = workspace / "TASK.md"
    tracked.mkdir(parents=True)
    (tracked / "notes.txt").write_text("repository content")

    stash = stash_workspace_entry(workspace, "TASK.md")
    write_workspace_file(workspace, "TASK.md", "# Task\n")
    assert (workspace / "TASK.md").is_file()

    restore_workspace_entry(workspace, "TASK.md", stash)

    assert (workspace / "TASK.md").is_dir()
    assert (workspace / "TASK.md" / "notes.txt").read_text() == "repository content"


def test_a_broken_symlink_does_not_stop_the_write(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "TASK.md").symlink_to(tmp_path / "nothing-here")

    stash = stash_workspace_entry(workspace, "TASK.md")
    write_workspace_file(workspace, "TASK.md", "# Task\n")

    assert (workspace / "TASK.md").read_text() == "# Task\n"
    restore_workspace_entry(workspace, "TASK.md", stash)
    assert (workspace / "TASK.md").is_symlink()


def test_a_free_path_needs_no_stash(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert stash_workspace_entry(workspace, "TASK.md") == ""


def test_the_file_is_private_to_the_worker(tmp_path: Path) -> None:
    """The config carries the API key; other accounts on the host have no business reading it."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    write_workspace_file(workspace, "fixjury-mini.yaml", "api_key: secret\n")

    assert (workspace / "fixjury-mini.yaml").stat().st_mode & 0o777 == 0o600


def test_an_existing_regular_file_is_replaced_and_comes_back(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "TASK.md").write_text("tracked content")

    stash = stash_workspace_entry(workspace, "TASK.md")
    write_workspace_file(workspace, "TASK.md", "fresh")
    assert (workspace / "TASK.md").read_text() == "fresh"

    restore_workspace_entry(workspace, "TASK.md", stash)
    assert (workspace / "TASK.md").read_text() == "tracked content"


def test_a_patch_that_carries_the_key_is_detected() -> None:
    """The key is in the sandbox because the harness needs it; the patch is not a place for it."""

    assert exposes_secret('+OPENAI_API_KEY = "sk-live-123"\n', "sk-live-123") is True


def test_a_patch_without_the_key_is_kept() -> None:
    assert exposes_secret("--- a/calc.py\n+++ b/calc.py\n", "sk-live-123") is False


def test_no_key_means_nothing_to_leak() -> None:
    assert exposes_secret("anything at all", "") is False


def test_writing_refuses_a_path_that_is_already_taken(tmp_path: Path) -> None:
    """O_EXCL|O_NOFOLLOW: nothing sneaks a link in between the stash and the write."""

    outside = tmp_path / "outside.txt"
    outside.write_text("untouched")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "TASK.md").symlink_to(outside)

    with pytest.raises(FileExistsError):
        write_workspace_file(workspace, "TASK.md", "# Task\n")

    assert outside.read_text() == "untouched"
