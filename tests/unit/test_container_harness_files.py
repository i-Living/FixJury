"""Writing platform files into a workspace somebody else owns."""

from __future__ import annotations

from pathlib import Path

from fixjury.agents.container_harness import write_workspace_file


def test_a_symlink_committed_by_the_repository_is_replaced_not_followed(tmp_path: Path) -> None:
    """The workspace is a checkout at base_commit, so it can contain any symlink.

    Writing through one would put the API key wherever the repository pointed, with
    the worker's privileges.
    """

    outside = tmp_path / "outside.txt"
    outside.write_text("untouched")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "fixjury-mini.yaml").symlink_to(outside)

    write_workspace_file(workspace, "fixjury-mini.yaml", "step_limit: 3\n")

    assert outside.read_text() == "untouched"
    written = workspace / "fixjury-mini.yaml"
    assert not written.is_symlink()
    assert written.read_text() == "step_limit: 3\n"


def test_a_broken_symlink_does_not_stop_the_write(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "TASK.md").symlink_to(tmp_path / "nothing-here")

    write_workspace_file(workspace, "TASK.md", "# Task\n")

    assert (workspace / "TASK.md").read_text() == "# Task\n"


def test_a_directory_in_the_way_is_cleared(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    blocking = workspace / "TASK.md"
    blocking.mkdir(parents=True)
    (blocking / "keep.txt").write_text("gone with it")

    write_workspace_file(workspace, "TASK.md", "# Task\n")

    assert (workspace / "TASK.md").is_file()


def test_the_file_is_private_to_the_worker(tmp_path: Path) -> None:
    """The config carries the API key; other accounts on the host have no business reading it."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    write_workspace_file(workspace, "fixjury-mini.yaml", "api_key: secret\n")

    assert (workspace / "fixjury-mini.yaml").stat().st_mode & 0o777 == 0o600


def test_an_existing_regular_file_is_overwritten(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "TASK.md").write_text("stale")

    write_workspace_file(workspace, "TASK.md", "fresh")

    assert (workspace / "TASK.md").read_text() == "fresh"
