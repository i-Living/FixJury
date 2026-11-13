"""Reaping rules for run networks: what counts as abandoned."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fixjury.sandbox.network import STALE_AFTER_S, age_seconds, is_stale


def _attrs(age: timedelta) -> dict[str, object]:
    stamp = datetime.now(UTC) - age
    return {"Created": stamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ")}


def test_a_network_created_now_is_not_stale() -> None:
    assert is_stale(_attrs(timedelta(seconds=1))) is False


def test_a_network_older_than_the_cutoff_is_stale() -> None:
    assert is_stale(_attrs(timedelta(seconds=STALE_AFTER_S + 60))) is True


def test_the_cutoff_is_strict_enough_to_leave_a_starting_run_alone() -> None:
    """A worker starting next to another one must not remove its fresh network."""

    assert is_stale(_attrs(timedelta(seconds=5))) is False
    assert is_stale(_attrs(timedelta(hours=2))) is True


def test_a_missing_timestamp_is_treated_as_fresh() -> None:
    """No timestamp means no evidence of abandonment, so nothing is removed."""

    assert age_seconds({}) == 0.0
    assert is_stale({}) is False


def test_an_unparseable_timestamp_is_treated_as_fresh() -> None:
    assert age_seconds({"Created": "not a date"}) == 0.0
    assert is_stale({"Created": "not a date"}) is False
