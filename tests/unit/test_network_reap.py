"""Reaping rules: which run networks may be removed, and on what evidence."""

from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fixjury.sandbox.network import (
    OWNER_LABEL,
    STALE_AFTER_S,
    age_seconds,
    is_abandoned,
    is_stale,
    owner_state,
    owner_value,
)

# One pinned instant for both the timestamp and the comparison, so the assertions
# do not depend on the wall clock moving between the two reads.
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def _attrs(age: timedelta, owner: str | None = None) -> dict[str, object]:
    attrs: dict[str, object] = {"Created": (NOW - age).strftime("%Y-%m-%dT%H:%M:%S.%fZ")}
    if owner is not None:
        attrs["Labels"] = {OWNER_LABEL: owner}
    return attrs


def _dead_owner() -> str:
    """An owner on this host whose pid cannot exist."""

    return f"{socket.gethostname()}:{2**22 + 7}:12345"


def _unreadable(pid: int) -> tuple[bool, str]:
    """A process table we are not allowed to read, like a `hidepid` mount."""

    return False, ""


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


def _label(pid: int = 4242, started: str = "777") -> str:
    return f"{socket.gethostname()}:{pid}:{started}"


def test_a_live_process_with_the_recorded_start_is_alive() -> None:
    assert owner_state(_label(), read_start=lambda pid: (True, "777")) is True


def test_a_dead_pid_is_not_alive() -> None:
    assert owner_state(_dead_owner(), read_start=lambda pid: (True, "")) is False


def test_a_reused_pid_with_a_different_start_time_is_not_alive() -> None:
    """The start time is stored precisely so a recycled pid cannot look alive."""

    assert owner_state(_label(started="777"), read_start=lambda pid: (True, "888")) is False


def test_another_host_is_unknown_rather_than_dead() -> None:
    assert owner_state("some-other-host:4242:777") is None


def test_a_label_without_a_start_falls_back_to_existence() -> None:
    assert owner_state(_label(started=""), read_start=lambda pid: (True, "777")) is True


def test_an_unreadable_process_table_is_unknown_rather_than_dead() -> None:
    """`hidepid` must not turn a live run into a reaped one."""

    assert owner_state(_label(), read_start=_unreadable) is None

    fresh = _attrs(timedelta(seconds=5), owner=_label())
    old = _attrs(timedelta(hours=5), owner=_label())

    assert is_abandoned(fresh, now=NOW, read_start=_unreadable) is False
    assert is_abandoned(old, now=NOW, read_start=_unreadable) is True


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="this host has no procfs")
def test_the_real_reader_sees_this_process_as_alive() -> None:
    """The one test that touches the real process table, kept apart from the rules."""

    assert owner_state(owner_value()) is True


def test_a_live_owner_keeps_its_network_however_old_it_is() -> None:
    """An image pull can delay the container for minutes; age is not evidence."""

    alive = _attrs(timedelta(hours=5), owner=_label())
    assert is_abandoned(alive, now=NOW, read_start=lambda pid: (True, "777")) is False


def test_a_dead_owner_on_this_host_is_reaped_without_waiting_for_age() -> None:
    """A dead process cannot attach a container later, so there is nothing to wait for."""

    assert is_abandoned(_attrs(timedelta(seconds=5), owner=_dead_owner()), now=NOW) is True


def test_an_unlabelled_network_falls_back_to_age() -> None:
    assert is_abandoned(_attrs(timedelta(hours=5)), now=NOW) is True
    assert is_abandoned(_attrs(timedelta(seconds=5)), now=NOW) is False


def test_the_callers_threshold_is_the_one_used_for_the_fallback() -> None:
    """`reap_networks(older_than_s=86400)` must not reap at the one-hour default."""

    two_hours = _attrs(timedelta(hours=2))

    assert is_abandoned(two_hours, now=NOW, older_than_s=3600) is True
    assert is_abandoned(two_hours, now=NOW, older_than_s=86400) is False
