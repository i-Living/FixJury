"""Reaping rules: which run networks may be removed, and on what evidence."""

from __future__ import annotations

import os
import socket
from datetime import UTC, datetime, timedelta

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


def test_this_process_is_alive_by_its_own_label() -> None:
    assert owner_state(owner_value()) is True


def test_a_dead_pid_is_not_alive() -> None:
    assert owner_state(_dead_owner()) is False


def test_a_reused_pid_with_a_different_start_time_is_not_alive() -> None:
    """The start time is stored precisely so a recycled pid cannot look alive."""

    assert owner_state(f"{socket.gethostname()}:{os.getpid()}:0") is False


def test_another_host_is_unknown_rather_than_dead() -> None:
    assert owner_state(f"some-other-host:{os.getpid()}:1") is None


def test_a_label_without_a_start_falls_back_to_existence() -> None:
    assert owner_state(f"{socket.gethostname()}:{os.getpid()}:") is True


def test_an_unreadable_process_table_is_unknown_rather_than_dead() -> None:
    """`hidepid` must not turn a live run into a reaped one."""

    unknown = f"{socket.gethostname()}:{os.getpid()}:1"

    assert owner_state(unknown, read_start=_unreadable) is None

    fresh = _attrs(timedelta(seconds=5), owner=unknown)
    old = _attrs(timedelta(hours=5), owner=unknown)

    assert is_abandoned(fresh, now=NOW, read_start=_unreadable) is False
    assert is_abandoned(old, now=NOW, read_start=_unreadable) is True


def test_a_live_owner_keeps_its_network_however_old_it_is() -> None:
    """An image pull can delay the container for minutes; age is not evidence."""

    assert is_abandoned(_attrs(timedelta(hours=5), owner=owner_value()), now=NOW) is False


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
