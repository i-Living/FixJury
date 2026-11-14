from __future__ import annotations

from sqlalchemy.exc import DBAPIError

from fixjury.worker import _is_lock_conflict


class LockTaken(Exception):
    """Stands in for the driver error; only its SQLSTATE matters here."""

    sqlstate = "55P03"


class OtherProblem(Exception):
    sqlstate = "42P01"


def test_a_nowait_lock_conflict_is_recognised() -> None:
    """Two deliveries of one run id must not both execute it."""

    assert _is_lock_conflict(DBAPIError("SELECT 1", {}, LockTaken("could not obtain lock")))


def test_another_database_error_is_not_a_lock_conflict() -> None:
    assert not _is_lock_conflict(DBAPIError("SELECT 1", {}, OtherProblem("undefined table")))
