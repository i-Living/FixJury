from __future__ import annotations

import pytest

from fixjury.models import RejectionCode
from fixjury.verify.policy import (
    check_policy,
    is_protected_path,
    is_test_path,
    parse_changed_files,
)

ALLOWED = ("loguru/_colorizer.py", "tests/")


def patch(path: str, *, added: str = "+VALUE = 2", removed: str = "-VALUE = 1") -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1 @@\n"
        f"{removed}\n"
        f"{added}\n"
    )


def new_file(path: str, body: str = "+X = 1\n") -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "--- /dev/null\n"
        f"+++ b/{path}\n"
        "@@ -0,0 +1 @@\n"
        f"{body}"
    )


def deleted_file(path: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        "deleted file mode 100644\n"
        "index 1111111..0000000\n"
        f"--- a/{path}\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-def test_something():\n"
    )


def test_a_clean_patch_inside_allowed_paths_passes() -> None:
    result = check_policy(patch("loguru/_colorizer.py"), allowed_paths=ALLOWED)

    assert result.passed is True
    assert result.violations == ()
    assert [changed.path for changed in result.files] == ["loguru/_colorizer.py"]
    assert result.files[0].added == 1
    assert result.files[0].deleted == 1


def test_a_change_outside_allowed_paths_is_extra_files() -> None:
    result = check_policy(patch("loguru/other.py"), allowed_paths=ALLOWED)

    assert result.passed is False
    assert RejectionCode.EXTRA_FILES in result.codes
    assert "outside allowed_paths" in result.violations[0].note


def test_a_new_test_file_under_an_allowed_directory_passes() -> None:
    result = check_policy(new_file("tests/test_regression.py"), allowed_paths=ALLOWED)

    assert result.passed is True
    assert result.files[0].is_new is True


def test_editing_ci_configuration_is_a_spec_violation() -> None:
    result = check_policy(
        patch(".github/workflows/ci.yml", added="+  x: 1", removed="-  x: 0"),
        allowed_paths=(".",),
    )

    assert RejectionCode.SPEC_VIOLATION in result.codes
    assert "CI, configuration or a lock file" in result.violations[0].note


def test_editing_a_lock_file_is_a_spec_violation() -> None:
    result = check_policy(patch("uv.lock"), allowed_paths=(".",))

    assert RejectionCode.SPEC_VIOLATION in result.codes


def test_deleting_a_test_file_is_a_spec_violation() -> None:
    result = check_policy(deleted_file("tests/test_colorizer.py"), allowed_paths=ALLOWED)

    assert RejectionCode.SPEC_VIOLATION in result.codes
    assert "was deleted" in result.violations[0].note


def test_an_empty_diff_is_an_incomplete_fix() -> None:
    result = check_policy("", allowed_paths=ALLOWED)

    assert result.passed is False
    assert result.codes == (RejectionCode.INCOMPLETE_FIX,)


def test_too_many_changed_files_is_extra_files() -> None:
    diff = "".join(patch(f"loguru/_module_{index}.py") for index in range(11))

    result = check_policy(diff, allowed_paths=(".",), max_files=10)

    assert RejectionCode.EXTRA_FILES in result.codes
    assert any("11 files changed" in violation.note for violation in result.violations)


def test_a_forbidden_path_is_a_spec_violation() -> None:
    result = check_policy(
        patch("tests/test_colorizer.py"),
        allowed_paths=ALLOWED,
        forbidden=("tests/test_colorizer.py",),
    )

    assert RejectionCode.SPEC_VIOLATION in result.codes
    assert "listed as forbidden" in result.violations[0].note


def test_moving_a_test_out_of_the_test_tree_is_refused() -> None:
    diff = (
        "diff --git a/tests/test_colorizer.py b/loguru/test_colorizer.py\n"
        "similarity index 100%\n"
        "rename from tests/test_colorizer.py\n"
        "rename to loguru/test_colorizer.py\n"
    )

    result = check_policy(diff, allowed_paths=ALLOWED)

    assert RejectionCode.SPEC_VIOLATION in result.codes
    notes = " ".join(violation.note for violation in result.violations)
    assert "moved out of the test tree" in notes


def test_a_binary_patch_is_still_parsed() -> None:
    diff = (
        "diff --git a/tests/fixture.png b/tests/fixture.png\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "GIT binary patch\n"
        "literal 4\n"
        "LcmZQzU|?Vb00RI3\n"
    )

    result = check_policy(diff, allowed_paths=ALLOWED)

    assert result.passed is True
    assert result.files[0].path == "tests/fixture.png"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_colorizer.py", True),
        ("src/tests/helpers.py", True),
        ("pkg/test_api.py", True),
        ("pkg/api_test.py", True),
        ("tests/conftest.py", True),
        ("loguru/_colorizer.py", False),
        ("docs/testing.md", False),
    ],
)
def test_test_paths_are_recognised(path: str, expected: bool) -> None:
    assert is_test_path(path) is expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (".github/workflows/ci.yml", True),
        ("uv.lock", True),
        ("pyproject.toml", True),
        ("loguru/_colorizer.py", False),
    ],
)
def test_protected_paths_are_recognised(path: str, expected: bool) -> None:
    assert is_protected_path(path) is expected


def test_parse_changed_files_returns_one_record_per_path() -> None:
    diff = patch("loguru/_colorizer.py") + new_file("tests/test_regression.py")

    files = parse_changed_files(diff)

    assert [changed.path for changed in files] == [
        "loguru/_colorizer.py",
        "tests/test_regression.py",
    ]
    assert files[1].is_new is True
