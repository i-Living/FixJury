"""The tool API an agent gets inside its sandbox (PLAN §7, task 2.2).

Three rules, each of them load-bearing:

* commands are passed as an argv list to ``exec_run``, never as a string, so a
  path with a space, a quote or a semicolon cannot become a second command;
* output is capped with an explicit marker, because a tool result that quietly
  loses the failing assertion is worse than a truncated one;
* every path is resolved inside the workspace and refused if it escapes, so a
  tool call cannot read the host or the platform repository.
"""

from __future__ import annotations

import asyncio
import io
import posixpath
import tarfile
from collections.abc import Sequence
from dataclasses import dataclass

from docker.errors import DockerException

from fixjury.config import settings
from fixjury.sandbox.container import SandboxHandle

TRUNCATION_MARKER = "\n… [output truncated]"


class ToolError(RuntimeError):
    """A tool call cannot be performed as asked."""


@dataclass(frozen=True)
class ExecResult:
    """One command inside the sandbox."""

    exit_code: int
    output: str
    truncated: bool

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def resolve_path(handle: SandboxHandle, path: str) -> str:
    """Resolve ``path`` inside the workspace, refusing anything that escapes it."""

    if not path:
        raise ToolError("path is empty")
    root = handle.spec.workdir
    joined = path if posixpath.isabs(path) else posixpath.join(root, path)
    resolved = posixpath.normpath(joined)
    if resolved != root and not resolved.startswith(root + "/"):
        raise ToolError(f"path {path!r} escapes the workspace")
    return resolved


def _cap(output: str, limit: int) -> tuple[str, bool]:
    if len(output) <= limit:
        return output, False
    return output[:limit] + TRUNCATION_MARKER, True


async def exec_command(
    handle: SandboxHandle,
    argv: Sequence[str],
    *,
    timeout_s: float = 120.0,
    workdir: str | None = None,
) -> ExecResult:
    """Run ``argv`` inside the workspace. No shell, no string interpolation."""

    if not argv:
        raise ToolError("argv is empty")
    if any("\x00" in part for part in argv):
        raise ToolError("argv contains a NUL byte")
    target = resolve_path(handle, workdir) if workdir else handle.spec.workdir
    limit = settings.sandbox_output_limit

    def run() -> ExecResult:
        try:
            result = handle.container.exec_run(list(argv), workdir=target)
        except DockerException as exc:
            raise ToolError(f"exec failed: {exc}") from exc
        raw = result.output or b""
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        capped, truncated = _cap(text, limit)
        return ExecResult(exit_code=int(result.exit_code or 0), output=capped, truncated=truncated)

    try:
        return await asyncio.wait_for(asyncio.to_thread(run), timeout=timeout_s)
    except TimeoutError as exc:
        raise ToolError(f"command timed out after {timeout_s:g}s") from exc


async def read_file(handle: SandboxHandle, path: str, *, max_bytes: int | None = None) -> str:
    """Read a file inside the workspace."""

    target = resolve_path(handle, path)
    result = await exec_command(handle, ["cat", "--", target])
    if not result.ok:
        raise ToolError(f"cannot read {path!r}: {result.output.strip()}")
    limit = settings.sandbox_output_limit if max_bytes is None else max_bytes
    capped, _ = _cap(result.output, limit)
    return capped


async def write_file(handle: SandboxHandle, path: str, content: str) -> None:
    """Write a file inside the workspace without a shell or a temp file."""

    target = resolve_path(handle, path)
    directory = posixpath.dirname(target) or handle.spec.workdir
    name = posixpath.basename(target)
    payload = content.encode("utf-8")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo(name=name)
        info.size = len(payload)
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(payload))
    buffer.seek(0)

    def put() -> None:
        try:
            handle.container.put_archive(directory, buffer.getvalue())
        except DockerException as exc:
            raise ToolError(f"cannot write {path!r}: {exc}") from exc

    await asyncio.to_thread(put)


async def list_dir(handle: SandboxHandle, path: str = ".") -> tuple[str, ...]:
    """List a directory inside the workspace."""

    target = resolve_path(handle, path)
    result = await exec_command(handle, ["ls", "-1a", "--", target])
    if not result.ok:
        raise ToolError(f"cannot list {path!r}: {result.output.strip()}")
    return tuple(line for line in result.output.splitlines() if line not in {"", ".", ".."})
