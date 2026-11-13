"""The run network is a route, not a suggestion (PLAN invariant 7).

These tests drive real Docker, because the claim under test is a network fact that
no unit test can establish. Nothing here leaves the machine: the "outside world" is
a local HTTP server on the default bridge gateway, reachable from a container on
that bridge and from nowhere else.
"""

from __future__ import annotations

import asyncio
import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import docker
import pytest

from fixjury.sandbox.container import SandboxSpec, open_sandbox
from fixjury.sandbox.network import open_run_network
from fixjury.sandbox.proxy import AllowlistProxy
from fixjury.sandbox.tools import exec_command

pytestmark = pytest.mark.integration

IMAGE = "python:3.12-slim"
BRIDGE = "bridge"


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"hello from the model endpoint"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


def bridge_gateway(network: str = BRIDGE) -> str:
    client = docker.from_env()
    try:
        config = (client.networks.get(network).attrs.get("IPAM") or {}).get("Config") or []
    finally:
        client.close()
    assert config and config[0].get("Gateway"), f"network {network} has no gateway"
    return str(config[0]["Gateway"])


@pytest.fixture
def outside() -> Iterator[str]:
    """An HTTP server on the bridge gateway: the control target for reachability."""

    server = http.server.ThreadingHTTPServer((bridge_gateway(), 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"{bridge_gateway()}:{int(server.server_address[1])}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def upstream() -> Iterator[str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"127.0.0.1:{int(server.server_address[1])}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root


def fetch_script(target: str, proxy_url: str | None = None) -> str:
    lines = ["import urllib.request", f"target = {target!r}"]
    if proxy_url is None:
        lines.append("print(urllib.request.urlopen(target, timeout=10).read().decode())")
    else:
        lines += [
            f"proxy = {proxy_url!r}",
            "opener = urllib.request.build_opener(",
            "    urllib.request.ProxyHandler({'http': proxy, 'https': proxy})",
            ")",
            "print(opener.open(target, timeout=10).read().decode())",
        ]
    return "\n".join(lines) + "\n"


def test_the_control_can_reach_the_endpoint_and_the_run_network_cannot(
    workspace: Path, outside: str
) -> None:
    """Proof by contrast: same target, same command, one network reaches it.

    A test that only asserted "the sandbox failed" would also pass on a broken
    host, a wrong address or a server that never started.
    """

    async def scenario() -> tuple[int, int]:
        control_spec = SandboxSpec(image=IMAGE, workspace=workspace, network=True)
        async with open_sandbox(control_spec) as control:
            reachable = await exec_command(
                control, ["python3", "-c", fetch_script(f"http://{outside}/")], timeout_s=60
            )
        async with open_run_network() as network:
            sandbox_spec = SandboxSpec(image=IMAGE, workspace=workspace, network_name=network.name)
            async with open_sandbox(sandbox_spec) as sandbox:
                blocked = await exec_command(
                    sandbox, ["python3", "-c", fetch_script(f"http://{outside}/")], timeout_s=60
                )
        return reachable.exit_code, blocked.exit_code

    control_exit, sandbox_exit = asyncio.run(scenario())

    assert control_exit == 0, "the endpoint has to be reachable from a normal bridge"
    assert sandbox_exit != 0, "the run network must not route to another network"


def test_the_proxy_on_the_gateway_forwards_only_what_it_allows(
    workspace: Path, upstream: str, outside: str
) -> None:
    """The gateway is the only reachable host, so the proxy decides what leaves."""

    async def scenario() -> tuple[int, str]:
        async with open_run_network() as network:
            proxy = AllowlistProxy(allowed={upstream}, host=network.gateway)
            await proxy.start()
            try:
                spec = SandboxSpec(image=IMAGE, workspace=workspace, network_name=network.name)
                environment = {"HTTP_PROXY": proxy.url, "http_proxy": proxy.url}
                async with open_sandbox(spec) as sandbox:
                    allowed = await exec_command(
                        sandbox,
                        ["python3", "-c", fetch_script(f"http://{upstream}/v1/models", proxy.url)],
                        timeout_s=60,
                        env=environment,
                    )
                    refused = await exec_command(
                        sandbox,
                        ["python3", "-c", fetch_script(f"http://{outside}/", proxy.url)],
                        timeout_s=60,
                        env=environment,
                    )
            finally:
                await proxy.stop()
        return refused.exit_code, allowed.output

    refused_code, allowed_output = asyncio.run(scenario())

    assert "hello from the model endpoint" in allowed_output
    assert refused_code != 0, "a target off the allowlist must not be forwarded"
