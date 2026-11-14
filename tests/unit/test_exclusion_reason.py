from __future__ import annotations

import uuid

import pytest

from fixjury.models import UNABLE_REASON_LENGTH, Run, RunStatus
from fixjury.run import bounded_reason
from fixjury.verify.service import _exclude


class FakeSession:
    """Only what ``_exclude`` touches: it stores two fields and flushes."""

    def __init__(self) -> None:
        self.flushed = False

    async def flush(self) -> None:
        self.flushed = True


def test_a_reason_that_fits_is_stored_unchanged() -> None:
    assert bounded_reason("harness exited 1") == "harness exited 1"


def test_a_long_reason_is_clipped_to_the_column_and_keeps_its_tail() -> None:
    """PostgreSQL would reject the overflow at flush, losing the run and its usage."""

    reason = "diff capture failed: " + "fatal: unable to read tree " + "a" * 400

    bounded = bounded_reason(reason)

    assert bounded is not None
    assert len(bounded) == UNABLE_REASON_LENGTH
    assert bounded.startswith("…")
    assert bounded.endswith("a" * 20)


def test_an_empty_reason_is_not_a_reason() -> None:
    assert bounded_reason("   ") is None
    assert bounded_reason(None) is None


@pytest.mark.asyncio
async def test_an_unbuildable_environment_excludes_the_run_with_a_reason() -> None:
    """A pair nobody could verify must leave the metrics as infra_failed, not silently."""

    run = Run(task_id="T-010", agent="baseline", attempt=1)
    run.id = uuid.uuid4()
    session = FakeSession()

    await _exclude(session, run, reason="pip install -e . timed out")  # type: ignore[arg-type]

    assert run.status is RunStatus.infra_failed
    assert run.unable_reason == "verification failed: pip install -e . timed out"
    assert session.flushed is True
