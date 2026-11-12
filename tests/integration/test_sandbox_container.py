from __future__ import annotations

import asyncio
from pathlib import Path

import docker
import pytest
from docker.errors import NotFound

from fixjury.sandbox import SandboxSpec, exec_command, open_sandbox

pytestmark = pytest.mark.integration

IMAGE = "python:3.12-slim"


def spec_for(workspace: Path, *, timeout_s: int = 120) -> SandboxSpec:
    return SandboxSpec(image=IMAGE, workspace=workspace, timeout_s=timeout_s)


def test_a_command_runs_inside_the_sandbox(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("from the host\n", encoding="utf-8")

    async def scenario() -> str:
        async with open_sandbox(spec_for(tmp_path)) as sandbox:
            result = await exec_command(sandbox, ["cat", "hello.txt"])
            assert result.ok, result.output
            return result.output

    assert asyncio.run(scenario()) == "from the host\n"


def test_the_agent_can_write_and_the_host_sees_it(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with open_sandbox(spec_for(tmp_path)) as sandbox:
            result = await exec_command(
                sandbox, ["python", "-c", "open('made.py','w').write('X = 1\\n')"]
            )
            assert result.ok, result.output

    asyncio.run(scenario())

    assert (tmp_path / "made.py").read_text(encoding="utf-8") == "X = 1\n"


def test_the_sandbox_has_no_network(tmp_path: Path) -> None:
    script = (
        "import socket, sys\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 53), 2)\n"
        "except OSError:\n"
        "    sys.exit(7)\n"
        "sys.exit(0)\n"
    )

    async def scenario() -> int:
        async with open_sandbox(spec_for(tmp_path)) as sandbox:
            result = await exec_command(sandbox, ["python", "-c", script], timeout_s=30)
            return result.exit_code

    assert asyncio.run(scenario()) == 7


def test_the_container_is_removed_after_the_run(tmp_path: Path) -> None:
    async def scenario() -> str:
        async with open_sandbox(spec_for(tmp_path)) as sandbox:
            identifier = sandbox.id
            assert await exec_command(sandbox, ["true"]) is not None
            return identifier

    container_id = asyncio.run(scenario())

    client = docker.from_env()
    try:
        with pytest.raises(NotFound):
            client.containers.get(container_id)
    finally:
        client.close()


def test_the_run_cap_kills_the_container(tmp_path: Path) -> None:
    async def scenario() -> str:
        async with open_sandbox(spec_for(tmp_path, timeout_s=1)) as sandbox:
            identifier = sandbox.id
            await asyncio.sleep(2.5)
            sandbox.container.reload()
            assert sandbox.container.status in {"exited", "dead", "created"}
            return identifier

    asyncio.run(scenario())
