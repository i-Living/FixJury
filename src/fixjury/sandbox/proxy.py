"""An allowlisting egress proxy (PLAN invariant 7).

A third-party agent runs inside its own container, so it needs a route to exactly
one endpoint: the model provider. Everything else stays unreachable. This proxy
is that route — it accepts ``CONNECT host:port`` and absolute-URI requests, and
forwards only targets on the allowlist.

It runs in the platform process, bound to the docker bridge address, so no
platform file is ever mounted into an agent container.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field

import docker

LOGGER = logging.getLogger(__name__)
FORBIDDEN = b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
ESTABLISHED = b"HTTP/1.1 200 Connection Established\r\n\r\n"
MAX_HEADER_BYTES = 65536


async def docker_gateway(network: str = "bridge") -> str:
    """The address a container uses to reach the host, where the proxy listens."""

    def lookup() -> str:
        client = docker.from_env()
        try:
            details = client.networks.get(network).attrs
            config = details.get("IPAM", {}).get("Config") or []
            if config and config[0].get("Gateway"):
                return str(config[0]["Gateway"])
            return "172.17.0.1"
        finally:
            client.close()

    return await asyncio.to_thread(lookup)


def normalise(host: str, port: int | None) -> str:
    return f"{host.lower()}:{port or 80}"


@dataclass
class AllowlistProxy:
    """A minimal forward proxy restricted to ``allowed`` targets."""

    allowed: set[str] = field(default_factory=set)
    # Bound to loopback by default; the adapter moves it to the docker bridge
    # address so a sandboxed container can reach it and nothing else can.
    host: str = "127.0.0.1"
    port: int = 0
    _server: asyncio.AbstractServer | None = None

    def permits(self, host: str, port: int | None) -> bool:
        target = normalise(host, port)
        if target in self.allowed:
            return True
        # A bare host entry means "that host on any port".
        return f"{host.lower()}:*" in self.allowed

    async def start(self) -> AllowlistProxy:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        LOGGER.info("egress proxy on %s:%s allowing %s", self.host, self.port, self.allowed)
        return self

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            writer.close()
            return
        if len(head) > MAX_HEADER_BYTES:
            writer.write(FORBIDDEN)
            await writer.drain()
            writer.close()
            return

        request_line = head.split(b"\r\n", 1)[0].decode("latin-1")
        parts = request_line.split()
        if len(parts) < 3:
            writer.write(FORBIDDEN)
            await writer.drain()
            writer.close()
            return
        method, target, _version = parts[0], parts[1], parts[2]

        if method.upper() == "CONNECT":
            await self._tunnel(target, reader, writer)
            return
        await self._forward(method, target, head, reader, writer)

    async def _tunnel(
        self,
        target: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        host, _, raw_port = target.rpartition(":")
        port = int(raw_port) if raw_port.isdigit() else 443
        if not self.permits(host, port):
            LOGGER.info("proxy refused CONNECT %s", target)
            writer.write(FORBIDDEN)
            await writer.drain()
            writer.close()
            return
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(host, port)
        except OSError:
            writer.close()
            return
        writer.write(ESTABLISHED)
        await writer.drain()
        await asyncio.gather(
            self._pipe(reader, upstream_writer),
            self._pipe(upstream_reader, writer),
            return_exceptions=True,
        )
        upstream_writer.close()
        writer.close()

    async def _forward(
        self,
        method: str,
        target: str,
        head: bytes,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        from urllib.parse import urlsplit

        split = urlsplit(target)
        if not split.hostname:
            writer.write(FORBIDDEN)
            await writer.drain()
            writer.close()
            return
        port = split.port or (443 if split.scheme == "https" else 80)
        if not self.permits(split.hostname, port):
            LOGGER.info("proxy refused %s %s", method, target)
            writer.write(FORBIDDEN)
            await writer.drain()
            writer.close()
            return
        path = split.path or "/"
        if split.query:
            path = f"{path}?{split.query}"
        _, _, remainder = head.partition(b"\r\n")
        rewritten = f"{method} {path} HTTP/1.1\r\n".encode("latin-1") + remainder
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(split.hostname, port)
        except OSError:
            writer.close()
            return
        upstream_writer.write(rewritten)
        await upstream_writer.drain()
        await asyncio.gather(
            self._pipe(reader, upstream_writer),
            self._pipe(upstream_reader, writer),
            return_exceptions=True,
        )
        upstream_writer.close()
        writer.close()

    @staticmethod
    async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(Exception):
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
        with contextlib.suppress(Exception):
            writer.close()
