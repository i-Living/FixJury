"""A per-run network with no route out except the platform (PLAN invariant 7).

An allowlisting proxy is only a route if the container has no other one. On the
default bridge it does: the proxy arrives as ``HTTP_PROXY``, a command can unset
that variable, and the container reaches the internet directly.

So the sandbox gets its own bridge created with ``internal=True``. Docker gives
that network no NAT and no default route, which leaves the host's own address on
the bridge as the only reachable peer — where the egress proxy listens. Removing
the network with the run keeps the platform from accumulating bridges.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import uuid4

import docker
from docker.errors import DockerException, NotFound

LOGGER = logging.getLogger(__name__)

NETWORK_LABEL = "fixjury.network"
PREFIX = "fixjury-run-"
DEFAULT_TIMEOUT_S = 60


class NetworkError(RuntimeError):
    """The run network cannot be created or has no usable gateway."""


@dataclass(frozen=True)
class RunNetwork:
    """A network, and the address a container uses to reach the host on it."""

    name: str
    gateway: str


def _create(client: docker.DockerClient, name: str) -> RunNetwork:
    network = client.networks.create(
        name,
        driver="bridge",
        internal=True,
        labels={NETWORK_LABEL: "1"},
        check_duplicate=True,
    )
    config = (network.attrs.get("IPAM") or {}).get("Config") or []
    gateway = config[0].get("Gateway") if config else None
    if not gateway:
        network.remove()
        raise NetworkError(f"network {name} came up without a gateway address")
    return RunNetwork(name=name, gateway=str(gateway))


def _remove(client: docker.DockerClient, name: str) -> None:
    with contextlib.suppress(NotFound, DockerException):
        client.networks.get(name).remove()


@contextlib.asynccontextmanager
async def open_run_network(prefix: str = PREFIX) -> AsyncIterator[RunNetwork]:
    """Create the network for one run and always remove it afterwards."""

    name = f"{prefix}{uuid4().hex[:12]}"
    client = await asyncio.to_thread(docker.from_env)
    try:
        network = await asyncio.wait_for(
            asyncio.to_thread(_create, client, name), timeout=DEFAULT_TIMEOUT_S
        )
    except (DockerException, TimeoutError) as exc:
        await asyncio.to_thread(client.close)
        raise NetworkError(f"cannot create the run network: {exc}") from exc

    LOGGER.info("run network %s, host reachable at %s", network.name, network.gateway)
    try:
        yield network
    finally:
        await asyncio.to_thread(_remove, client, name)
        await asyncio.to_thread(client.close)


async def reap_networks() -> int:
    """Remove networks left behind by a crashed process; returns the count."""

    def reap() -> int:
        client = docker.from_env()
        try:
            leftovers = client.networks.list(filters={"label": NETWORK_LABEL})
            for network in leftovers:
                with contextlib.suppress(NotFound, DockerException):
                    network.remove()
            return len(leftovers)
        finally:
            client.close()

    return await asyncio.to_thread(reap)


__all__ = [
    "NETWORK_LABEL",
    "NetworkError",
    "RunNetwork",
    "open_run_network",
    "reap_networks",
]
