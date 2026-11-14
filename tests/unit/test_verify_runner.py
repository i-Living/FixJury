from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from fixjury.tasks.schema import CheckTool
from fixjury.verify.runner import (
    VerificationError,
    apply_patch,
    check_argv,
    overlay_heldout,
    scm_version_env,
)


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
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "--quiet", "--initial-branch=main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "--quiet", "-m", "initial")
    return tmp_path


MODULE_PATCH = (
    "diff --git a/module.py b/module.py\n"
    "--- a/module.py\n"
    "+++ b/module.py\n"
    "@@ -1 +1 @@\n"
    "-VALUE = 1\n"
    "+VALUE = 2\n"
)


def test_a_patch_is_applied_to_the_checkout(repo: Path) -> None:
    apply_patch(repo, MODULE_PATCH)

    assert (repo / "module.py").read_text(encoding="utf-8") == "VALUE = 2\n"


def test_a_patch_that_does_not_apply_is_reported(repo: Path) -> None:
    with pytest.raises(VerificationError, match="does not apply"):
        apply_patch(repo, MODULE_PATCH.replace("-VALUE = 1", "-VALUE = 99"))


def test_the_nearest_tag_becomes_the_version_the_sandbox_cannot_read(repo: Path) -> None:
    """The prepare image has no git, so a tag-derived version must be handed in."""

    git(repo, "tag", "v1.4.2")

    assert scm_version_env(repo) == {"SETUPTOOLS_SCM_PRETEND_VERSION": "1.4.2"}


@pytest.mark.parametrize(
    ("tag", "version"),
    [
        ("v1.4.2", "1.4.2"),
        ("1.4.2", "1.4.2"),
        ("demo-v1.4.2", "1.4.2"),
        ("9.0.0a3", "9.0.0a3"),
        ("v2.0.0-rc.1", "2.0.0"),
    ],
)
def test_a_prefixed_tag_hands_in_the_version_not_the_whole_tag(
    repo: Path, tag: str, version: str
) -> None:
    """``SETUPTOOLS_SCM_PRETEND_VERSION`` is a version: a project prefix would be refused."""

    git(repo, "tag", tag)

    assert scm_version_env(repo) == {"SETUPTOOLS_SCM_PRETEND_VERSION": version}


def test_a_tag_without_a_version_hands_in_nothing(repo: Path) -> None:
    git(repo, "tag", "nightly")

    assert scm_version_env(repo) == {}


def test_a_checkout_without_tags_hands_in_no_version_of_its_own(tmp_path: Path) -> None:
    git(tmp_path, "init", "--quiet", "--initial-branch=main")

    assert scm_version_env(tmp_path) == {}


def test_a_directory_that_is_not_a_repository_hands_in_no_version(tmp_path: Path) -> None:
    assert scm_version_env(tmp_path / "absent") == {}


def test_a_hung_git_lookup_gives_up_instead_of_holding_the_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lookup reads a local checkout; a hang is a bug, and the install still matters."""

    def hang(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired("git", 30.0)

    monkeypatch.setattr("fixjury.verify.runner.subprocess.run", hang)

    assert scm_version_env(tmp_path) == {}


def test_a_new_file_is_created(repo: Path) -> None:
    diff = (
        "diff --git a/tests/test_new.py b/tests/test_new.py\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "--- /dev/null\n"
        "+++ b/tests/test_new.py\n"
        "@@ -0,0 +1 @@\n"
        "+X = 1\n"
    )

    apply_patch(repo, diff)

    assert (repo / "tests" / "test_new.py").read_text(encoding="utf-8") == "X = 1\n"


def test_the_heldout_overlay_replaces_the_test_file(repo: Path) -> None:
    (repo / "tests").mkdir()
    (repo / "tests" / "test_api.py").write_text("def test_a(): pass\n", encoding="utf-8")
    bundle = repo.parent / "bundle"
    (bundle / "files" / "tests").mkdir(parents=True)
    (bundle / "files" / "tests" / "test_api.py").write_text(
        "def test_a(): assert True\n", encoding="utf-8"
    )
    (bundle / "heldout.yaml").write_text(
        "command: pytest tests/test_api.py\nfiles:\n  - tests/test_api.py\n", encoding="utf-8"
    )

    files = overlay_heldout(repo, bundle)

    assert files == ("tests/test_api.py",)
    assert "assert True" in (repo / "tests" / "test_api.py").read_text(encoding="utf-8")


def test_a_bundle_missing_its_file_is_refused(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "files").mkdir(parents=True)
    (bundle / "heldout.yaml").write_text(
        "command: pytest tests/test_api.py\nfiles:\n  - tests/test_api.py\n", encoding="utf-8"
    )

    with pytest.raises(VerificationError, match="missing"):
        overlay_heldout(tmp_path, bundle)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("pytest tests/test_api.py", ["pytest", "tests/test_api.py"]),
        ("ruff check src tests", ["ruff", "check", "src", "tests"]),
        ("mypy src", ["mypy", "src"]),
    ],
)
def test_check_commands_become_argv(command: str, expected: list[str]) -> None:
    assert check_argv(command) == expected


def test_a_check_command_outside_the_whitelist_is_refused() -> None:
    with pytest.raises(ValueError, match="shell syntax"):
        check_argv("bash -c 'rm -rf /'")
    with pytest.raises(ValueError, match="must start with one of"):
        check_argv("bash -lc ls")


def test_the_heldout_command_is_pytest_only() -> None:
    from fixjury.tasks.schema import parse_check

    assert parse_check("pytest tests/test_api.py").tool is CheckTool.pytest
