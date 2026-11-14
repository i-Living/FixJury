from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from sqlalchemy.exc import DBAPIError

from fixjury import worker
from fixjury.worker import WorkerError, _is_lock_conflict


class LockTaken(Exception):
    """Stands in for the driver error; only its SQLSTATE matters here."""

    sqlstate = "55P03"


class OtherProblem(Exception):
    sqlstate = "42P01"


class LockedSession:
    """A session whose row is in another transaction: exactly what ``NOWAIT`` returns."""

    async def get(self, *args: object, **kwargs: object) -> object:
        raise DBAPIError("SELECT 1", {}, LockTaken("could not obtain lock"))


class RecordingQueue:
    def __init__(self) -> None:
        self.removed: list[str] = []

    async def lrem(self, key: str, count: int, value: str) -> int:
        self.removed.append(value)
        return 1


def test_a_nowait_lock_conflict_is_recognised() -> None:
    """Two deliveries of one run id must not both execute it."""

    assert _is_lock_conflict(DBAPIError("SELECT 1", {}, LockTaken("could not obtain lock")))


def test_another_database_error_is_not_a_lock_conflict() -> None:
    assert not _is_lock_conflict(DBAPIError("SELECT 1", {}, OtherProblem("undefined table")))


def test_a_second_delivery_leaves_the_id_for_the_worker_that_holds_it() -> None:
    """Dropping it would orphan the run if the holder then died: resume scans processing."""

    run_id = uuid.uuid4()
    client = RecordingQueue()

    with pytest.raises(WorkerError, match="already claimed"):
        asyncio.run(
            worker.process_run(
                client,  # type: ignore[arg-type]
                LockedSession(),  # type: ignore[arg-type]
                run_id,
                corpus_root=Path("/nonexistent"),
            )
        )

    # Nothing was removed: the id is still in processing for ``resume`` to find.
    assert client.removed == []
