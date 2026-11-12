from __future__ import annotations

from pathlib import Path

import pytest

from fixjury.tasks.heldout import heldout_files, read_heldout, validate_heldout
from fixjury.tasks.loader import TaskFileError

TEST_FILE = "tests/test_regression_1234.py"


def write_bundle(
    directory: Path, *, content: str = "def test_one() -> None:\n    assert True\n"
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    overlay = directory / "files" / TEST_FILE
    overlay.parent.mkdir(parents=True, exist_ok=True)
    overlay.write_text(content, encoding="utf-8")
    (directory / "heldout.yaml").write_text(
        f"command: pytest {TEST_FILE}\nfiles:\n  - {TEST_FILE}\n", encoding="utf-8"
    )


def test_read_heldout_parses_the_bundle(tmp_path: Path) -> None:
    write_bundle(tmp_path)

    spec = read_heldout(tmp_path)

    assert spec.command == f"pytest {TEST_FILE}"
    assert spec.files == (TEST_FILE,)
    assert heldout_files(tmp_path, spec) == (tmp_path / "files" / TEST_FILE,)


def test_read_heldout_rejects_non_pytest_command(tmp_path: Path) -> None:
    write_bundle(tmp_path)
    (tmp_path / "heldout.yaml").write_text(
        f"command: ruff check tests\nfiles: [{TEST_FILE}]\n", encoding="utf-8"
    )

    with pytest.raises(TaskFileError, match="pytest"):
        read_heldout(tmp_path)


def test_read_heldout_rejects_paths_outside_the_repository(tmp_path: Path) -> None:
    write_bundle(tmp_path)
    (tmp_path / "heldout.yaml").write_text(
        "command: pytest tests/x.py\nfiles: [../outside.py]\n", encoding="utf-8"
    )

    with pytest.raises(TaskFileError, match="relative path"):
        read_heldout(tmp_path)


def test_read_heldout_rejects_benchmarks_paths(tmp_path: Path) -> None:
    write_bundle(tmp_path)
    (tmp_path / "heldout.yaml").write_text(
        "command: pytest benchmarks/heldout/T-001/x.py\nfiles: [benchmarks/heldout/T-001/x.py]\n",
        encoding="utf-8",
    )

    with pytest.raises(TaskFileError, match="repository path"):
        read_heldout(tmp_path)


def test_validate_heldout_accepts_a_consistent_bundle(tmp_path: Path) -> None:
    write_bundle(tmp_path)

    assert validate_heldout(tmp_path) == []


def test_validate_heldout_reports_a_missing_overlay_file(tmp_path: Path) -> None:
    write_bundle(tmp_path)
    (tmp_path / "files" / TEST_FILE).unlink()

    problems = validate_heldout(tmp_path)

    assert any("has no files/" in problem for problem in problems)


def test_validate_heldout_reports_an_empty_overlay_file(tmp_path: Path) -> None:
    write_bundle(tmp_path, content="\n\n")

    problems = validate_heldout(tmp_path)

    assert any("is empty" in problem for problem in problems)


def test_validate_heldout_reports_a_command_running_another_file(tmp_path: Path) -> None:
    write_bundle(tmp_path)
    (tmp_path / "heldout.yaml").write_text(
        f"command: pytest tests/test_other.py\nfiles: [{TEST_FILE}]\n", encoding="utf-8"
    )

    problems = validate_heldout(tmp_path)

    assert any("does not provide" in problem for problem in problems)


def test_validate_heldout_reports_a_missing_bundle(tmp_path: Path) -> None:
    problems = validate_heldout(tmp_path / "nope")

    assert any("does not exist" in problem for problem in problems)
