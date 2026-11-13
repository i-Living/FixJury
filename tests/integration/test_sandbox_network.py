"""The run network is a route, not a suggestion (PLAN invariant 7).

These tests drive real Docker: the point is that a container cannot reach the
internet with its proxy variables removed, which no unit test can establish.
"""

from __future__ import annotations

import asyncio
import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from fixjury.sandbox.container import SandboxSpec, open_sandbox
from fixjury.sandbox.network import open_run_network
from fixjury.sandbox.proxy import AllowlistProxy
from fixjury.sandbox.tools import exec_command

pytestmark = pytest.mark.integration

IMAGE = "python:3.12-slim"


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"hello from the model endpoint"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


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


def fetch_script(target: str, proxy_url: str) -> str:
    return (
        "import urllib.request\n"
        f"proxy = {proxy_url!r}\n"
        f"target = {target!r}\n"
        "opener = urllib.request.build_opener(\n"
        "    urllib.request.ProxyHandler({'http': proxy, 'https': proxy})\n"
        ")\n"
        "print(opener.open(target, timeout=10).read().decode())\n"
    )


DIAL_OUT = (
    "import urllib.request\n"
    "urllib.request.urlopen('https://example.com', timeout=8)\n"
    "print('reached the internet')\n"
)


def test_the_sandbox_cannot_reach_the_internet_without_its_proxy(workspace: Path) -> None:
    """Unsetting HTTP_PROXY was the bypass; on the run network there is nowhere to go."""

    async def scenario() -> int:
        async with open_run_network() as network:
            spec = SandboxSpec(image=IMAGE, workspace=workspace, network_name=network.name)
            async with open_sandbox(spec) as sandbox:
                result = await exec_command(sandbox, ["python3", "-c", DIAL_OUT], timeout_s=60)
        return result.exit_code

    assert asyncio.run(scenario()) != 0


def test_the_proxy_on_the_gateway_forwards_only_what_it_allows(
    workspace: Path, upstream: str
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
                        ["python3", "-c", fetch_script("http://example.com/", proxy.url)],
                        timeout_s=60,
                        env=environment,
                    )
            finally:
                await proxy.stop()
        return refused.exit_code, allowed.output

    refused_code, allowed_output = asyncio.run(scenario())

    assert "hello from the model endpoint" in allowed_output
    assert refused_code != 0, "a target off the allowlist must not be forwarded"
