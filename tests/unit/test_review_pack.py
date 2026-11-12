from __future__ import annotations

import uuid

import pytest

from fixjury.review.pack import (
    BlindItem,
    batch_id_for,
    blind_item,
    normalise_diff,
    seed_for,
)

DIFF = """From 1234567890abcdef Mon Sep 17 00:00:00 2001
From: Some Agent <agent@example.com>
Subject: [PATCH] fix it

diff --git a/src/module.py b/src/module.py
index 1111111..2222222 100644
--- a/src/module.py
+++ b/src/module.py
@@ -1 +1 @@
-VALUE = 1
+VALUE = 2
"""


class FakeRun:
    id = uuid.uuid4()
    diff = DIFF


class FakeVerdict:
    tests_pass = True
    lint_pass = True
    type_pass = True
    policy_pass = True
    heldout_pass = True


def test_the_order_seed_is_deterministic_per_task() -> None:
    assert seed_for("T-001", 1, 0) == seed_for("T-001", 1, 0)
    assert seed_for("T-001", 1, 0) != seed_for("T-002", 1, 0)
    assert seed_for("T-001", 1, 0) != seed_for("T-001", 2, 0)
    assert seed_for("T-001", 1, 0) != seed_for("T-001", 1, 7)


def test_the_batch_id_carries_task_pass_and_seed() -> None:
    identifier = batch_id_for("T-001", 1, 42)

    assert identifier == "T-001-p1-00000000002a"


def test_the_diff_loses_the_mail_header_and_the_object_ids() -> None:
    cleaned = normalise_diff(DIFF)

    assert "From:" not in cleaned
    assert "Subject:" not in cleaned
    assert "index 1111111..2222222" not in cleaned
    assert "+VALUE = 2" in cleaned
    assert cleaned.endswith("\n")


def test_the_diff_keeps_the_patch_itself() -> None:
    cleaned = normalise_diff(DIFF)

    assert "diff --git a/src/module.py b/src/module.py" in cleaned
    assert "--- a/src/module.py" in cleaned
    assert "+++ b/src/module.py" in cleaned


def test_the_blind_item_carries_no_identity() -> None:
    item = blind_item(FakeRun(), FakeVerdict(), 0)  # type: ignore[arg-type]

    assert isinstance(item, BlindItem)
    assert set(BlindItem.__dataclass_fields__) == {"run_id", "position", "patch", "checks"}
    assert "agent" not in str(item.patch).lower()


def test_the_blind_item_reports_the_checks() -> None:
    item = blind_item(FakeRun(), FakeVerdict(), 3)  # type: ignore[arg-type]

    assert item.position == 3
    assert item.checks["tests"] is True
    assert item.checks["held-out"] is True


def test_a_run_without_a_verdict_shows_null_checks() -> None:
    item = blind_item(FakeRun(), None, 0)  # type: ignore[arg-type]

    assert item.checks["tests"] is None


def test_an_empty_diff_is_not_rendered_as_a_blank_page() -> None:
    class Empty:
        id = uuid.uuid4()
        diff = ""

    item = blind_item(Empty(), None, 0)  # type: ignore[arg-type]

    assert item.patch == "\n"


@pytest.mark.parametrize("pass_no", [1, 2])
def test_the_seed_changes_with_the_pass(pass_no: int) -> None:
    assert seed_for("T-001", pass_no, 0) > 0
