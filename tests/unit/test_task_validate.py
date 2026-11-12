from __future__ import annotations

import datetime as dt
from pathlib import Path

from fixjury.tasks.loader import CorpusIndex, write_index, write_task
from fixjury.tasks.schema import TaskDocument
from fixjury.tasks.validate import validate_corpus

NEW_FILE_PATCH = """\
diff --git a/tests/test_regression.py b/tests/test_regression.py
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/tests/test_regression.py
@@ -0,0 +1,2 @@
+def test_regression() -> None:
+    assert True
"""


def build_corpus(tmp_path: Path) -> Path:
    root = tmp_path / "benchmarks"
    document = TaskDocument.model_validate(
        {
            "id": "T-001",
            "repo": "https://github.com/pallets/click",
            "license": "BSD-3-Clause",
            "base_commit": "a" * 40,
            "fix_commit": "b" * 40,
            "issue": {
                "number": 2712,
                "url": "https://github.com/pallets/click/issues/2712",
                "created_at": "2025-09-14",
                "fix_merged_at": "2025-10-02",
                "body": "Completions break.",
            },
            "title": "Completion breaks for dashed options",
            "acceptance": ["completion output matches the option name"],
            "checks": ["pytest tests/test_shell_completion.py"],
            "allowed_paths": ["src/click/shell_completion.py", "tests/"],
            "difficulty": "medium",
            "heldout_ref": "benchmarks/heldout/T-001",
            "frozen_at": "2026-11-12T10:00:00+00:00",
            "frozen_by": "owner",
        }
    )
    write_task(root / "tasks" / "T-001" / "task.yaml", document)

    bundle = root / "heldout" / "T-001"
    bundle.mkdir(parents=True)
    (bundle / "heldout.patch").write_text(NEW_FILE_PATCH, encoding="utf-8")
    (bundle / "heldout.yaml").write_text(
        "command: pytest tests/test_regression.py\nfiles: [tests/test_regression.py]\n",
        encoding="utf-8",
    )

    write_index(
        root,
        CorpusIndex(
            corpus_version="corpus-1",
            frozen_at=dt.date(2026, 11, 12),
            tasks=("T-001",),
        ),
    )
    return root


def test_valid_corpus_has_no_findings(tmp_path: Path) -> None:
    root = build_corpus(tmp_path)

    assert validate_corpus(root) == []
    assert validate_corpus(root, min_tasks=1) == []


def test_missing_index_is_reported(tmp_path: Path) -> None:
    root = build_corpus(tmp_path)
    (root / "index.json").unlink()

    findings = validate_corpus(root)

    assert any("index.json" in str(finding) for finding in findings)


def test_unfrozen_task_is_reported(tmp_path: Path) -> None:
    root = build_corpus(tmp_path)
    document = TaskDocument.model_validate(
        {
            "id": "T-001",
            "repo": "https://github.com/pallets/click",
            "issue": {
                "number": 2712,
                "url": "https://github.com/pallets/click/issues/2712",
            },
            "title": "Completion breaks for dashed options",
            "acceptance": ["completion output matches the option name"],
            "checks": ["pytest tests/test_shell_completion.py"],
            "allowed_paths": ["src/click/shell_completion.py"],
            "difficulty": "medium",
            "heldout_ref": "benchmarks/heldout/T-001",
        }
    )
    write_task(root / "tasks" / "T-001" / "task.yaml", document)

    findings = validate_corpus(root)

    assert any("not frozen" in finding.message for finding in findings)
    assert any("license is missing" in finding.message for finding in findings)
    assert any("base_commit is missing" in finding.message for finding in findings)


def test_pre_cutoff_issue_is_reported(tmp_path: Path) -> None:
    root = build_corpus(tmp_path)
    path = root / "tasks" / "T-001" / "task.yaml"
    text = path.read_text(encoding="utf-8").replace(
        "created_at: 2025-09-14", "created_at: 2025-03-10"
    )
    path.write_text(text, encoding="utf-8")

    findings = validate_corpus(root)

    assert any("contamination cut-off" in finding.message for finding in findings)


def test_missing_heldout_patch_is_reported(tmp_path: Path) -> None:
    root = build_corpus(tmp_path)
    (root / "heldout" / "T-001" / "heldout.patch").unlink()

    findings = validate_corpus(root)

    assert any("held-out bundle has no heldout.patch" in finding.message for finding in findings)


def test_check_pointing_into_benchmarks_is_reported(tmp_path: Path) -> None:
    root = build_corpus(tmp_path)
    path = root / "tasks" / "T-001" / "task.yaml"
    text = path.read_text(encoding="utf-8").replace(
        "checks:\n- pytest tests/test_shell_completion.py",
        "checks:\n- pytest benchmarks/heldout/T-001/test_regression.py",
    )
    path.write_text(text, encoding="utf-8")

    findings = validate_corpus(root)

    assert any("points into benchmarks/" in finding.message for finding in findings)


def test_index_must_list_every_task(tmp_path: Path) -> None:
    root = build_corpus(tmp_path)
    write_index(
        root,
        CorpusIndex(corpus_version="corpus-1", frozen_at=dt.date(2026, 11, 12), tasks=()),
    )

    findings = validate_corpus(root)

    assert any("does not list tasks on disk" in finding.message for finding in findings)


def test_min_tasks_is_enforced(tmp_path: Path) -> None:
    root = build_corpus(tmp_path)

    findings = validate_corpus(root, min_tasks=2)

    assert any("expected at least 2" in finding.message for finding in findings)
