from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from fixjury.export import file_sha256, record_line, write_jsonl


def test_a_record_is_one_sorted_line_without_whitespace() -> None:
    line = record_line({"b": 1, "a": "x"})

    assert line == '{"a":"x","b":1}'


def test_decimals_timestamps_and_uuids_keep_their_text_form() -> None:
    moment = dt.datetime(2026, 11, 14, 12, 0, tzinfo=dt.UTC)

    line = record_line({"cost": Decimal("0.1250"), "at": moment})

    record = json.loads(line)
    assert record == {"at": "2026-11-14T12:00:00+00:00", "cost": "0.1250"}


def test_an_unsupported_type_is_refused_rather_than_stringified() -> None:
    with pytest.raises(TypeError, match="not serialisable"):
        record_line({"value": object()})


def test_the_digest_is_the_digest_of_the_file(tmp_path: Path) -> None:
    path = tmp_path / "runs.jsonl"

    rows, digest = write_jsonl(path, [{"id": 1}, {"id": 2}])

    assert rows == 2
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    assert file_sha256(path) == digest
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_an_empty_table_exports_an_empty_file_not_a_missing_one(tmp_path: Path) -> None:
    path = tmp_path / "rejections.jsonl"

    rows, digest = write_jsonl(path, [])

    assert rows == 0
    assert path.is_file()
    assert digest == hashlib.sha256(b"").hexdigest()
