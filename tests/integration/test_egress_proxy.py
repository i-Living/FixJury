from __future__ import annotations

import asyncio
import http.server
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from fixjury.agents.base import LlmConfig
from fixjury.agents.container_harness import (
    HarnessError,
    HarnessSpec,
    llm_target,
    proxy_environment,
)
from fixjury.sandbox.proxy import AllowlistProxy

pytestmark = pytest.mark.integration


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"hello from the model endpoint"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def upstream() -> Iterator[str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = str(server.server_address[0])
    port = int(server.server_address[1])
    try:
        yield f"{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()


def fetch(url: str, proxy_url: str) -> str:
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    )
    with opener.open(url, timeout=10) as response:
        body: bytes = response.read()
    return body.decode()


def test_the_proxy_forwards_an_allowed_target(upstream: str) -> None:
    proxy = AllowlistProxy(allowed={upstream})

    async def scenario() -> tuple[str, str]:
        await proxy.start()
        try:
            # The proxy serves on this event loop, so the blocking client call
            # must not run on it.
            return await asyncio.to_thread(
                fetch, f"http://{upstream}/v1/models", proxy.url
            ), proxy.url
        finally:
            await proxy.stop()

    body, _ = asyncio.run(scenario())

    assert body == "hello from the model endpoint"


def test_the_proxy_refuses_a_target_off_the_allowlist(upstream: str) -> None:
    proxy = AllowlistProxy(allowed={"example.invalid:80"})

    async def scenario() -> int:
        await proxy.start()
        try:
            with pytest.raises(urllib.error.HTTPError) as failure:
                await asyncio.to_thread(fetch, f"http://{upstream}/v1/models", proxy.url)
            return failure.value.code
        finally:
            await proxy.stop()

    assert asyncio.run(scenario()) == 403


def test_the_proxy_refuses_a_host_without_a_port_on_the_allowlist() -> None:
    proxy = AllowlistProxy(allowed={"api.example.com:443"})

    assert proxy.permits("api.example.com", 443) is True
    assert proxy.permits("api.example.com", 80) is False
    assert proxy.permits("other.example.com", 443) is False


def test_a_wildcard_entry_allows_any_port() -> None:
    proxy = AllowlistProxy(allowed={"api.example.com:*"})

    assert proxy.permits("api.example.com", 8080) is True


def test_the_llm_target_comes_from_the_base_url() -> None:
    llm = LlmConfig(base_url="https://api.example.com/v1", model="m")

    assert llm_target(llm) == "api.example.com:443"


def test_a_plain_http_endpoint_uses_port_80() -> None:
    assert llm_target(LlmConfig(base_url="http://localhost/v1")) == "localhost:80"


def test_no_endpoint_means_no_allowed_target() -> None:
    assert llm_target(None) is None
    assert llm_target(LlmConfig()) is None


def test_the_proxy_environment_points_every_variable_at_the_proxy() -> None:
    environment = proxy_environment("http://172.17.0.1:1234")

    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        assert environment[key] == "http://172.17.0.1:1234"
    assert "localhost" in environment["NO_PROXY"]


def test_a_harness_without_an_image_is_refused() -> None:
    spec = HarnessSpec(name="mini", version="1", image="", command=("mini",))

    with pytest.raises(HarnessError, match="no pinned image"):
        spec.validate()


def test_a_harness_without_a_command_is_refused() -> None:
    spec = HarnessSpec(name="mini", version="1", image="ghcr.io/x/y:1", command=())

    with pytest.raises(HarnessError, match="no command"):
        spec.validate()


def test_the_workspace_helper_creates_a_directory(tmp_path: Path) -> None:
    from fixjury.agents.container_harness import workspace_for_run

    created = workspace_for_run()

    assert created.is_dir()
    created.rmdir()
