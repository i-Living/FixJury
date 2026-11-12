"""Container lifecycle for one run (PLAN §7, task 2.1).

One container per run, no network, read-only root filesystem, a tmpfs for
scratch space and a read-write bind mount for the workspace. The docker SDK is
synchronous, so every call goes through ``asyncio.to_thread``.

The lifecycle is an async context manager rather than a class: nothing here keeps
state between runs, and ``finally`` must remove the container even when the body
raised, timed out or was cancelled.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import docker
from docker.errors import DockerException, ImageNotFound
from docker.models.containers import Container

from fixjury.config import settings

SANDBOX_LABEL = "fixjury.sandbox"
KEEPALIVE_COMMAND = ["sleep", "infinity"]


class SandboxError(RuntimeError):
    """The sandbox cannot be created or used."""


@dataclass(frozen=True)
class SandboxSpec:
    """Everything a sandbox needs; adapters never read global settings."""

    image: str
    workspace: Path
    workdir: str = "/workspace"
    cpu: float = 2.0
    memory_mb: int = 2048
    pids_limit: int = 256
    tmpfs_mb: int = 512
    timeout_s: int = 1200
    # Running as the invoking user keeps the workspace bind writable while the
    # root filesystem stays read-only; capabilities are dropped, so root inside
    # could not write to a home directory owned by another uid anyway.
    user: str = f"{os.getuid()}:{os.getgid()}"

    @classmethod
    def from_settings(cls, workspace: Path) -> SandboxSpec:
        return cls(
            image=settings.sandbox_image,
            workspace=workspace,
            cpu=settings.sandbox_cpu,
            memory_mb=settings.sandbox_memory_mb,
            timeout_s=settings.sandbox_timeout_s,
        )


@dataclass(frozen=True)
class SandboxHandle:
    """A running container and the spec it was built from."""

    container: Container
    spec: SandboxSpec

    @property
    def id(self) -> str:
        return str(self.container.id)


def _ensure_image(client: docker.DockerClient, image: str) -> None:
    try:
        client.images.get(image)
    except ImageNotFound:
        client.images.pull(image)


def _create_container(client: docker.DockerClient, spec: SandboxSpec) -> Container:
    _ensure_image(client, spec.image)
    return client.containers.create(
        image=spec.image,
        command=KEEPALIVE_COMMAND,
        working_dir=spec.workdir,
        user=spec.user,
        network_mode="none",
        read_only=True,
        tmpfs={"/tmp": f"rw,noexec,nosuid,size={spec.tmpfs_mb}m"},  # noqa: S108 - inside the container
        volumes={str(spec.workspace): {"bind": spec.workdir, "mode": "rw"}},
        mem_limit=f"{spec.memory_mb}m",
        nano_cpus=int(spec.cpu * 1_000_000_000),
        pids_limit=spec.pids_limit,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges"],
        labels={SANDBOX_LABEL: "1"},
        detach=True,
    )


def _remove_quietly(container: Container) -> None:
    with contextlib.suppress(DockerException):
        container.remove(force=True)


async def _kill_after(container: Container, seconds: float) -> None:
    """Run cap: the container dies even if the Python side is stuck."""

    await asyncio.sleep(seconds)
    with contextlib.suppress(DockerException):
        await asyncio.to_thread(container.kill)


@contextlib.asynccontextmanager
async def open_sandbox(spec: SandboxSpec) -> AsyncIterator[SandboxHandle]:
    """Create, start and always remove a sandbox for one run."""

    if not spec.workspace.is_dir():
        raise SandboxError(f"workspace {spec.workspace} is not a directory")
    client = await asyncio.to_thread(docker.from_env)
    try:
        container = await asyncio.to_thread(_create_container, client, spec)
    except DockerException as exc:
        await asyncio.to_thread(client.close)
        raise SandboxError(f"cannot create the sandbox: {exc}") from exc

    watchdog = asyncio.create_task(_kill_after(container, spec.timeout_s))
    try:
        await asyncio.to_thread(container.start)
        yield SandboxHandle(container=container, spec=spec)
    finally:
        watchdog.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watchdog
        await asyncio.to_thread(_remove_quietly, container)
        await asyncio.to_thread(client.close)


async def reap_sandboxes() -> int:
    """Remove containers left behind by a crashed process; returns the count."""

    def reap() -> int:
        client = docker.from_env()
        try:
            leftovers = client.containers.list(all=True, filters={"label": SANDBOX_LABEL})
            for container in leftovers:
                _remove_quietly(container)
            return len(leftovers)
        finally:
            client.close()

    return await asyncio.to_thread(reap)
