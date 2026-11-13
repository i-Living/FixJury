"""Reaping rules for run networks: what counts as abandoned."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fixjury.sandbox.network import STALE_AFTER_S, age_seconds, is_stale

# One pinned instant for both the timestamp and the comparison, so the assertions
# do not depend on the wall clock moving between the two reads.
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def _attrs(age: timedelta) -> dict[str, object]:
    return {"Created": (NOW - age).strftime("%Y-%m-%dT%H:%M:%S.%fZ")}


def test_a_network_created_now_is_not_stale() -> None:
    assert is_stale(_attrs(timedelta(seconds=1)), now=NOW) is False


def test_a_network_older_than_the_cutoff_is_stale() -> None:
    assert is_stale(_attrs(timedelta(seconds=STALE_AFTER_S + 60)), now=NOW) is True


def test_the_cutoff_is_strict_enough_to_leave_a_starting_run_alone() -> None:
    """A worker starting next to another one must not remove its fresh network."""

    assert is_stale(_attrs(timedelta(seconds=5)), now=NOW) is False
    assert is_stale(_attrs(timedelta(hours=2)), now=NOW) is True


def test_a_missing_timestamp_is_treated_as_fresh() -> None:
    """No timestamp means no evidence of abandonment, so nothing is removed."""

    assert age_seconds({}, now=NOW) == 0.0
    assert is_stale({}, now=NOW) is False


def test_an_unparseable_timestamp_is_treated_as_fresh() -> None:
    assert age_seconds({"Created": "not a date"}, now=NOW) == 0.0
    assert is_stale({"Created": "not a date"}, now=NOW) is False
