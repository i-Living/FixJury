"""A per-run network with no route out except the platform (PLAN invariant 7).

An allowlisting proxy is only a route if the container has no other one. On the
default bridge it does: the proxy arrives as ``HTTP_PROXY``, a command can unset
that variable, and the container reaches the internet directly.

So the sandbox gets its own bridge created with ``internal=True``. Docker gives
that network no NAT and no default route, which leaves the host's own address on
the bridge as the only reachable peer — where the egress proxy listens. Removing
the network with the run keeps the platform from accumulating bridges.

Two limits are deliberately not hidden here:

* the gateway is a host address, so a platform service bound to ``0.0.0.0`` would
  be reachable from the sandbox. The proxy binds the gateway itself; every other
  platform service belongs on loopback.
* Docker Engine before 26.0.0 (also 25.0.5, 23.0.11) forwarded DNS queries from
  internal networks to a host resolver, which a container could encode data in.
  The engine version is therefore a deployment requirement, named in `AGENTS.md`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import docker
from docker.errors import DockerException, NotFound

LOGGER = logging.getLogger(__name__)

NETWORK_LABEL = "fixjury.network"
PREFIX = "fixjury-run-"
DEFAULT_TIMEOUT_S = 60
# A network older than this cannot belong to a run that is still starting, so a
# startup reap will not pull it from under a concurrent worker.
STALE_AFTER_S = 3600
# Cleanups whose caller gave up; referenced so the loop does not collect them.
_LINGERING: set[asyncio.Task[None]] = set()


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


async def _abandon(
    creation: asyncio.Task[RunNetwork], client: docker.DockerClient, name: str
) -> None:
    """Remove a network whose creation outlived the caller that gave up on it.

    A thread cannot be cancelled, so the only correct cleanup waits for the create
    to land and then removes what it made: a removal attempted before the thread
    finishes finds nothing and leaks the network.
    """

    with contextlib.suppress(BaseException):
        await creation
    await asyncio.to_thread(_remove, client, name)
    await asyncio.to_thread(client.close)


@contextlib.asynccontextmanager
async def open_run_network(prefix: str = PREFIX) -> AsyncIterator[RunNetwork]:
    """Create the network for one run and always remove it afterwards."""

    name = f"{prefix}{uuid4().hex[:12]}"
    client = await asyncio.to_thread(docker.from_env)
    creation = asyncio.create_task(asyncio.to_thread(_create, client, name))
    try:
        network = await asyncio.wait_for(asyncio.shield(creation), timeout=DEFAULT_TIMEOUT_S)
    except asyncio.CancelledError:
        lingering = asyncio.create_task(_abandon(creation, client, name))
        _LINGERING.add(lingering)
        lingering.add_done_callback(_LINGERING.discard)
        raise
    except (DockerException, TimeoutError) as exc:
        await _abandon(creation, client, name)
        raise NetworkError(f"cannot create the run network: {exc}") from exc

    LOGGER.info("run network %s, host reachable at %s", network.name, network.gateway)
    try:
        yield network
    finally:
        await asyncio.to_thread(_remove, client, name)
        await asyncio.to_thread(client.close)


def age_seconds(attrs: dict[str, object], now: datetime | None = None) -> float:
    """How long ago Docker says this object was created; ``0`` when it does not say.

    ``now`` exists so a test can pin the reference instant instead of reading the
    wall clock twice, which a clock correction between reads would make flaky.
    """

    created = attrs.get("Created")
    if not isinstance(created, str):
        return 0.0
    with contextlib.suppress(ValueError):
        stamp = datetime.fromisoformat(created.replace("Z", "+00:00"))
        return ((now or datetime.now(UTC)) - stamp).total_seconds()
    return 0.0


def is_stale(
    attrs: dict[str, object], older_than_s: int = STALE_AFTER_S, now: datetime | None = None
) -> bool:
    """Is this network old enough that no live run can still be starting on it?"""

    return age_seconds(attrs, now) >= older_than_s


async def reap_networks(older_than_s: int = STALE_AFTER_S) -> int:
    """Remove run networks left behind by a crashed process; returns the count.

    Age-gated on purpose: a worker that starts while another one has just created a
    network must not remove it before a container is attached.
    """

    def reap() -> int:
        client = docker.from_env()
        try:
            removed = 0
            for network in client.networks.list(filters={"label": NETWORK_LABEL}):
                if not is_stale(network.attrs, older_than_s):
                    continue
                with contextlib.suppress(NotFound, DockerException):
                    network.remove()
                    removed += 1
            return removed
        finally:
            client.close()

    return await asyncio.to_thread(reap)


__all__ = [
    "NETWORK_LABEL",
    "STALE_AFTER_S",
    "NetworkError",
    "RunNetwork",
    "age_seconds",
    "is_stale",
    "open_run_network",
    "reap_networks",
]
