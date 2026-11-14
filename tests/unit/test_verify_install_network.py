from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from fixjury.verify import runner
from fixjury.verify.runner import INDEX_TARGETS, SandboxSpec, _install


@dataclass
class Seen:
    """What the install told docker to do."""

    specs: list[SandboxSpec] = field(default_factory=list)
    steps: list[list[str]] = field(default_factory=list)
    envs: list[dict[str, str]] = field(default_factory=list)
    allowed: list[set[str]] = field(default_factory=list)


def prepare(monkeypatch: pytest.MonkeyPatch, *, exit_code: int = 0) -> Seen:
    """Stub docker: the network, the proxy, the sandbox and the commands inside it."""

    seen = Seen()

    @asynccontextmanager
    async def network() -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(name="fixjury-run-test", gateway="172.19.0.1")

    class Proxy:
        def __init__(self, *, allowed: set[str]) -> None:
            seen.allowed.append(allowed)
            self.url = "http://172.19.0.1:3128"

        host = ""

        async def start(self) -> Proxy:
            return self

        async def stop(self) -> None:
            return None

    @asynccontextmanager
    async def sandbox(spec: SandboxSpec) -> AsyncIterator[object]:
        seen.specs.append(spec)
        yield object()

    async def command(
        handle: object, argv: list[str], *, timeout_s: float, workdir: str, env: dict[str, str]
    ) -> SimpleNamespace:
        seen.steps.append(argv)
        seen.envs.append(env)
        return SimpleNamespace(exit_code=exit_code, output="went wrong")

    monkeypatch.setattr(runner, "open_run_network", network)
    monkeypatch.setattr(runner, "AllowlistProxy", Proxy)
    monkeypatch.setattr(runner, "open_sandbox", sandbox)
    monkeypatch.setattr(runner, "exec_command", command)
    return seen


def test_the_install_reaches_the_index_and_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A diff can put code in the build backend; that code gets a package index, not a network."""

    seen = prepare(monkeypatch)
    spec = runner.SandboxSpec(image="python:3.12-slim", workspace=tmp_path, network=True)

    asyncio.run(_install(spec, extra_env={"SETUPTOOLS_SCM_PRETEND_VERSION": "1.0.0"}))

    installed = seen.specs[0]
    # The container is attached to the run's internal network instead of the open one.
    assert installed.network is False
    assert installed.network_name == "fixjury-run-test"
    assert seen.allowed == [set(INDEX_TARGETS)]
    for env in seen.envs:
        assert env["HTTPS_PROXY"] == "http://172.19.0.1:3128"
        assert env["NO_PROXY"] == ""
        assert env["SETUPTOOLS_SCM_PRETEND_VERSION"] == "1.0.0"


def test_a_failed_install_still_reports_the_command_and_the_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare(monkeypatch, exit_code=1)
    spec = runner.SandboxSpec(image="python:3.12-slim", workspace=tmp_path, network=True)

    with pytest.raises(runner.VerificationError, match="went wrong"):
        asyncio.run(_install(spec))
