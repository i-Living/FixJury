from __future__ import annotations

from pathlib import Path

import pytest

from fixjury.tasks.heldout import patch_created_files, read_heldout, validate_heldout
from fixjury.tasks.loader import TaskFileError

NEW_FILE_PATCH = """\
diff --git a/tests/test_regression_1234.py b/tests/test_regression_1234.py
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/tests/test_regression_1234.py
@@ -0,0 +1,4 @@
+def test_regression_1234() -> None:
+    assert True
+
+def test_second() -> None:
+    assert True
"""

MODIFIED_FILE_PATCH = """\
diff --git a/src/pkg/module.py b/src/pkg/module.py
index 1111111..2222222 100644
--- a/src/pkg/module.py
+++ b/src/pkg/module.py
@@ -1 +1 @@
-old
+new
"""


def write_bundle(directory: Path, *, patch: str = NEW_FILE_PATCH) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "heldout.patch").write_text(patch, encoding="utf-8")
    (directory / "heldout.yaml").write_text(
        "command: pytest tests/test_regression_1234.py\n"
        "files:\n"
        "  - tests/test_regression_1234.py\n",
        encoding="utf-8",
    )


def test_patch_created_files_lists_only_new_files() -> None:
    assert patch_created_files(NEW_FILE_PATCH) == ("tests/test_regression_1234.py",)
    assert patch_created_files(MODIFIED_FILE_PATCH) == ()
    assert patch_created_files(NEW_FILE_PATCH + MODIFIED_FILE_PATCH) == (
        "tests/test_regression_1234.py",
    )


def test_read_heldout_parses_the_bundle(tmp_path: Path) -> None:
    write_bundle(tmp_path)

    spec = read_heldout(tmp_path)

    assert spec.command == "pytest tests/test_regression_1234.py"
    assert spec.files == ("tests/test_regression_1234.py",)


def test_read_heldout_rejects_non_pytest_command(tmp_path: Path) -> None:
    write_bundle(tmp_path)
    (tmp_path / "heldout.yaml").write_text(
        "command: ruff check tests\nfiles: [tests/test_regression_1234.py]\n", encoding="utf-8"
    )

    with pytest.raises(TaskFileError, match="pytest"):
        read_heldout(tmp_path)


def test_validate_heldout_accepts_a_consistent_bundle(tmp_path: Path) -> None:
    write_bundle(tmp_path)

    assert validate_heldout(tmp_path) == []


def test_validate_heldout_reports_a_missing_patch(tmp_path: Path) -> None:
    write_bundle(tmp_path)
    (tmp_path / "heldout.patch").unlink()

    problems = validate_heldout(tmp_path)

    assert any("cannot read" in problem for problem in problems)


def test_validate_heldout_reports_files_the_patch_does_not_create(tmp_path: Path) -> None:
    write_bundle(tmp_path)
    (tmp_path / "heldout.yaml").write_text(
        "command: pytest tests/test_regression_1234.py\n"
        "files:\n"
        "  - tests/test_regression_1234.py\n"
        "  - tests/test_other.py\n",
        encoding="utf-8",
    )

    problems = validate_heldout(tmp_path)

    assert any("does not create" in problem for problem in problems)


def test_validate_heldout_reports_a_command_running_another_file(tmp_path: Path) -> None:
    write_bundle(tmp_path)
    (tmp_path / "heldout.yaml").write_text(
        "command: pytest tests/test_other.py\nfiles: [tests/test_regression_1234.py]\n",
        encoding="utf-8",
    )

    problems = validate_heldout(tmp_path)

    assert any("does not create" in problem for problem in problems)


def test_validate_heldout_rejects_binary_patches(tmp_path: Path) -> None:
    write_bundle(tmp_path, patch="diff --git a/x.py b/x.py\nGIT binary patch\nliteral 0\n")

    problems = validate_heldout(tmp_path)

    assert any("textual" in problem for problem in problems)
