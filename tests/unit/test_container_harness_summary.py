from __future__ import annotations

from fixjury.agents.container_harness import _last_meaningful_line
from fixjury.sandbox.tools import RESUME_MARKER, TRUNCATION_MARKER


def test_the_last_line_is_the_one_a_reader_can_use() -> None:
    assert _last_meaningful_line("starting\nconnection refused\n") == "connection refused"


def test_the_truncation_markers_are_not_a_diagnosis() -> None:
    """A capped output ends with bookkeeping; the summary must skip exactly that."""

    output = (
        f"error: the first failure\n{TRUNCATION_MARKER}{RESUME_MARKER}\n"
        f"error: the last failure\n{TRUNCATION_MARKER}{RESUME_MARKER}\n"
    )

    assert _last_meaningful_line(output) == "error: the last failure"


def test_a_diagnostic_that_starts_with_an_ellipsis_survives() -> None:
    """`… connection refused` is the failure, not a marker the platform inserted."""

    assert _last_meaningful_line("starting\n… connection refused") == "… connection refused"


def test_an_empty_output_has_no_summary() -> None:
    assert _last_meaningful_line("   \n\n") == ""
