from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from fixjury.spec.render import render_task
from fixjury.tasks.loader import (
    CorpusIndex,
    discover_task_paths,
    read_index,
    read_task,
    task_path,
    write_index,
    write_task,
)
from fixjury.tasks.schema import TaskDocument


def make_document(**overrides: object) -> TaskDocument:
    payload: dict[str, object] = {
        "id": "T-002",
        "repo": "https://github.com/more-itertools/more-itertools",
        "license": "MIT",
        "base_commit": "a" * 40,
        "fix_commit": "b" * 40,
        "issue": {
            "number": 812,
            "url": "https://github.com/more-itertools/more-itertools/issues/812",
            "created_at": "2025-08-01",
            "fix_merged_at": "2025-08-11",
            "body": "first line\n\nsecond paragraph\n",
        },
        "title": "windowed yields a trailing partial window",
        "acceptance": ["windowed does not yield a short final window"],
        "checks": ["pytest tests/test_more.py"],
        "allowed_paths": ["more_itertools/more.py", "tests/"],
        "difficulty": "easy",
        "heldout_ref": "benchmarks/heldout/T-002",
        "frozen_at": "2026-11-12T12:30:00+00:00",
        "frozen_by": "owner",
    }
    payload.update(overrides)
    return TaskDocument.model_validate(payload)


def test_render_is_stable_and_ordered() -> None:
    document = make_document()

    text = render_task(document)

    assert text.startswith("id: T-002\nrepo: https://github.com/more-itertools/more-itertools\n")
    assert "license: MIT" in text
    assert text.endswith("\n")
    assert render_task(document) == text


def test_render_round_trips_through_the_loader(tmp_path: Path) -> None:
    document = make_document()
    path = task_path(tmp_path, document.id)

    write_task(path, document)
    loaded = read_task(path)

    assert loaded == document
    assert loaded.issue.body == "first line\n\nsecond paragraph\n"


def test_write_task_creates_the_task_directory(tmp_path: Path) -> None:
    document = make_document()

    write_task(task_path(tmp_path, document.id), document)

    assert discover_task_paths(tmp_path) == [task_path(tmp_path, document.id)]


def test_index_round_trips(tmp_path: Path) -> None:
    index = CorpusIndex(
        corpus_version="corpus-1",
        frozen_at=dt.date(2026, 11, 12),
        tasks=("T-001", "T-002"),
    )

    write_index(tmp_path, index)

    assert read_index(tmp_path) == index
    raw = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert raw["tasks"] == ["T-001", "T-002"]
    assert raw["frozen_at"] == "2026-11-12"


def test_index_rejects_duplicate_ids() -> None:
    try:
        CorpusIndex(
            corpus_version="corpus-1",
            frozen_at=dt.date(2026, 11, 12),
            tasks=("T-001", "T-001"),
        )
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:  # pragma: no cover - the assertion above is the test
        raise AssertionError("duplicate ids were accepted")
