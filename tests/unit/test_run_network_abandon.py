"""A create that outlives its caller removes its own network.

The thread cannot be cancelled, so the only reliable cleanup is the code that runs
right after the create, holding a flag the caller set when it gave up.
"""

from __future__ import annotations

import threading

import pytest

from fixjury.sandbox.network import AbandonedError, NetworkError, _create, _create_and_release


class _FakeNetwork:
    def __init__(self, attrs: dict[str, object]) -> None:
        self.attrs = attrs
        self.removed = False

    def remove(self) -> None:
        self.removed = True


class _FakeNetworks:
    def __init__(self, attrs: dict[str, object], on_create=None) -> None:
        self.attrs = attrs
        self.on_create = on_create
        self.created: list[str] = []

    def create(self, name: str, **kwargs: object) -> _FakeNetwork:
        self.created.append(name)
        if self.on_create is not None:
            self.on_create()
        return _FakeNetwork(self.attrs)


class _FakeClient:
    def __init__(self, attrs: dict[str, object], on_create=None) -> None:
        self.networks = _FakeNetworks(attrs, on_create)
        self.closed = False

    def close(self) -> None:
        self.closed = True


IPAM: dict[str, object] = {"IPAM": {"Config": [{"Gateway": "172.30.0.1"}]}}


def test_a_normal_create_returns_the_gateway() -> None:
    client = _FakeClient(IPAM)

    network = _create(client, "fixjury-run-1", threading.Event())  # type: ignore[arg-type]

    assert network.gateway == "172.30.0.1"
    assert network.name == "fixjury-run-1"


def test_an_abandoned_create_removes_what_it_made_and_raises() -> None:
    client = _FakeClient(IPAM)
    abandoned = threading.Event()
    abandoned.set()

    with pytest.raises(AbandonedError):
        _create(client, "fixjury-run-2", abandoned)  # type: ignore[arg-type]

    assert client.networks.created == ["fixjury-run-2"]


def test_the_flag_is_read_after_the_create_not_before() -> None:
    """The caller gives up while the thread is still creating, which is the real case."""

    abandoned = threading.Event()
    client = _FakeClient(IPAM, on_create=abandoned.set)

    with pytest.raises(AbandonedError):
        _create(client, "fixjury-run-3", abandoned)  # type: ignore[arg-type]


def test_a_network_without_a_gateway_is_removed_and_reported() -> None:
    client = _FakeClient({"IPAM": {"Config": []}})  # type: ignore[arg-type]

    with pytest.raises(NetworkError, match="without a gateway"):
        _create(client, "fixjury-run-4", threading.Event())  # type: ignore[arg-type]


def test_an_abandoned_creation_closes_its_client() -> None:
    """The caller is gone, so nobody else can release the Docker client."""

    client = _FakeClient(IPAM)
    abandoned = threading.Event()
    abandoned.set()

    with pytest.raises(AbandonedError):
        _create_and_release(client, "fixjury-run-5", abandoned)  # type: ignore[arg-type]

    assert client.closed is True
