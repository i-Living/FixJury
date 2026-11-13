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
import os
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import docker
from docker.errors import DockerException, NotFound

from fixjury.sandbox.container import SANDBOX_LABEL

LOGGER = logging.getLogger(__name__)

NETWORK_LABEL = "fixjury.network"
# Who created the network, so a reaper can tell an abandoned one from a live run
# that has simply not attached its container yet.
OWNER_LABEL = "fixjury.owner"
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
        labels={NETWORK_LABEL: "1", OWNER_LABEL: owner_value()},
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


def _process_start_time(pid: int) -> str:
    """Field 22 of /proc/<pid>/stat: the process start, which survives pid reuse."""

    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return ""
    fields = raw.rsplit(")", 1)[-1].split()
    # After the command name, field 3 is state; start time is field 22 overall.
    return fields[19] if len(fields) > 19 else ""


def owner_value(pid: int | None = None) -> str:
    """``host:pid:start`` for the process creating the network."""

    process = os.getpid() if pid is None else pid
    return f"{socket.gethostname()}:{process}:{_process_start_time(process)}"


def is_owner_alive(value: str) -> bool:
    """Is the process named in an owner label still running, and the same one?

    A reused pid gives a different start time, which is the whole point of storing
    it: without that, a crash followed by an unrelated process taking the pid would
    make a dead owner look alive forever.
    """

    host, _, rest = value.partition(":")
    if host != socket.gethostname():
        return False
    raw_pid, _, started = rest.partition(":")
    if not raw_pid.isdigit():
        return False
    if not started:
        return Path(f"/proc/{raw_pid}").exists()
    return _process_start_time(int(raw_pid)) == started


def is_abandoned(
    attrs: dict[str, object], now: datetime | None = None, older_than_s: int = STALE_AFTER_S
) -> bool:
    """Can this network be removed without pulling it from under a live run?

    Liveness beats age. An owner on this host that is still running means the run is
    in progress, however long the network has existed — an image pull can delay the
    container for minutes. Without a live owner, a foreign or missing label falls
    back to age, because that is the only evidence left.
    """

    labels = attrs.get("Labels")
    owner = labels.get(OWNER_LABEL) if isinstance(labels, dict) else None
    if isinstance(owner, str) and owner.partition(":")[0] == socket.gethostname():
        # A dead process on this host is proof: it cannot attach a container later,
        # so age does not have to be waited out.
        return not is_owner_alive(owner)
    return is_stale(attrs, older_than_s, now)


async def reap_networks(older_than_s: int = STALE_AFTER_S) -> int:
    """Remove run networks left behind by a crashed process; returns the count.

    Only networks whose creator is gone are removed; a live owner keeps its network
    however old it is, which is what makes this safe to call at startup next to
    another worker.
    """

    now = datetime.now(UTC)

    def reap() -> int:
        client = docker.from_env()
        try:
            removed = 0
            for network in client.networks.list(filters={"label": NETWORK_LABEL}):
                if not is_abandoned(network.attrs, now):
                    continue
                name = network.name or ""
                if not name:
                    continue
                # Docker refuses to remove a network that still has containers on
                # it, so an abandoned container has to go first — and only ours:
                # the label keeps somebody else's container out of this.
                for container in client.containers.list(
                    all=True, filters={"network": name, "label": SANDBOX_LABEL}
                ):
                    with contextlib.suppress(NotFound, DockerException):
                        container.remove(force=True)
                with contextlib.suppress(NotFound, DockerException):
                    network.remove()
                    removed += 1
            return removed
        finally:
            client.close()

    return await asyncio.to_thread(reap)


__all__ = [
    "NETWORK_LABEL",
    "OWNER_LABEL",
    "STALE_AFTER_S",
    "NetworkError",
    "RunNetwork",
    "age_seconds",
    "is_abandoned",
    "is_owner_alive",
    "is_stale",
    "open_run_network",
    "owner_value",
    "reap_networks",
]
