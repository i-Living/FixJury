from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from fixjury.config import settings
from fixjury.run import checkout_base, mirror_path

GIT = shutil.which("git") or "git"


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [GIT, *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def repository(tmp_path: Path) -> tuple[Path, str]:
    """A source repository on disk stands in for GitHub: the code path is the same."""

    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "--quiet", "--initial-branch=main")
    git(origin, "config", "user.email", "bench@example.test")
    git(origin, "config", "user.name", "bench")
    (origin / "module.py").write_text("VALUE = 1\n")
    git(origin, "add", "module.py")
    git(origin, "commit", "--quiet", "-m", "initial")
    return origin, git(origin, "rev-parse", "HEAD")


@pytest.fixture(autouse=True)
def _cache_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "checkout_cache", str(tmp_path / "cache"))


def test_a_checkout_is_served_from_the_cached_mirror(tmp_path: Path) -> None:
    origin, commit = repository(tmp_path)

    checkout_base(str(origin), commit, tmp_path / "first")
    assert (tmp_path / "first" / "module.py").read_text() == "VALUE = 1\n"

    # The mirror exists after the first checkout, so the second cannot need the remote.
    assert (mirror_path(str(origin)) / "HEAD").is_file()
    checkout_base(str(origin), commit, tmp_path / "second")
    assert (tmp_path / "second" / "module.py").read_text() == "VALUE = 1\n"


def test_a_commit_that_landed_after_the_cache_is_still_checkoutable(tmp_path: Path) -> None:
    """The mirror may predate the task's commit; it is fetched once and retried."""

    origin, first = repository(tmp_path)
    checkout_base(str(origin), first, tmp_path / "before")

    (origin / "module.py").write_text("VALUE = 2\n")
    git(origin, "add", "module.py")
    git(origin, "commit", "--quiet", "-m", "second")
    second = git(origin, "rev-parse", "HEAD")

    checkout_base(str(origin), second, tmp_path / "after")
    assert (tmp_path / "after" / "module.py").read_text() == "VALUE = 2\n"


def test_a_commit_on_another_branch_reaches_the_cache(tmp_path: Path) -> None:
    """The tasks pin commits on the default branch, but the cache must not assume that.

    A cache cloned ``--bare`` records no fetch refspec, so a refresh used to leave every
    other branch behind and each checkout of such a commit fell back to the network.
    """

    origin, first = repository(tmp_path)
    checkout_base(str(origin), first, tmp_path / "before")

    git(origin, "checkout", "--quiet", "-b", "fix/branch")
    (origin / "module.py").write_text("VALUE = 3\n")
    git(origin, "add", "module.py")
    git(origin, "commit", "--quiet", "-m", "on a branch")
    on_branch = git(origin, "rev-parse", "HEAD")
    git(origin, "checkout", "--quiet", "main")

    checkout_base(str(origin), on_branch, tmp_path / "elsewhere")
    assert (tmp_path / "elsewhere" / "module.py").read_text() == "VALUE = 3\n"


def test_the_checkout_does_not_share_objects_with_the_cache(tmp_path: Path) -> None:
    """The sandbox mounts the workspace read-write; a shared inode would be its cache."""

    origin, commit = repository(tmp_path)
    checkout_base(str(origin), commit, tmp_path / "first")

    cache = {path.name: path for path in (mirror_path(str(origin)) / "objects").glob("??/*")}
    shared = [
        (path, cache[path.name])
        for path in (tmp_path / "first" / ".git" / "objects").glob("??/*")
        if path.name in cache
    ]

    # Without --no-hardlinks the same object is the same inode in both clones.
    assert shared
    assert all(checkout.stat().st_ino != cached.stat().st_ino for checkout, cached in shared)
