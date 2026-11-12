from __future__ import annotations

import asyncio
import io
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from fixjury.config import settings
from fixjury.sandbox.container import SandboxHandle, SandboxSpec
from fixjury.sandbox.tools import (
    TRUNCATION_MARKER,
    ToolError,
    exec_command,
    list_dir,
    read_file,
    resolve_path,
    write_file,
)

WORKSPACE = Path("/workspace")


@dataclass
class FakeContainer:
    output: bytes = b""
    exit_code: int = 0
    calls: list[tuple[list[str], str | None]] = field(default_factory=list)
    archives: list[tuple[str, bytes]] = field(default_factory=list)

    def exec_run(self, cmd: list[str], workdir: str | None = None) -> SimpleNamespace:
        self.calls.append((list(cmd), workdir))
        return SimpleNamespace(output=self.output, exit_code=self.exit_code)

    def put_archive(self, path: str, data: bytes) -> bool:
        self.archives.append((path, data))
        return True


def handle(container: FakeContainer) -> SandboxHandle:
    spec = SandboxSpec(image="python:3.12-slim", workspace=WORKSPACE)
    return SandboxHandle(container=container, spec=spec)


def run(coroutine: object) -> object:
    return asyncio.run(coroutine)  # type: ignore[arg-type]


def test_relative_path_resolves_inside_the_workspace() -> None:
    assert resolve_path(handle(FakeContainer()), "pkg/module.py") == "/workspace/pkg/module.py"


def test_absolute_path_inside_the_workspace_is_allowed() -> None:
    assert resolve_path(handle(FakeContainer()), "/workspace/a.py") == "/workspace/a.py"


@pytest.mark.parametrize("path", ["../etc/passwd", "/etc/passwd", "a/../../b", ".."])
def test_paths_escaping_the_workspace_are_refused(path: str) -> None:
    with pytest.raises(ToolError, match="escapes the workspace"):
        resolve_path(handle(FakeContainer()), path)


def test_empty_path_is_refused() -> None:
    with pytest.raises(ToolError, match="empty"):
        resolve_path(handle(FakeContainer()), "")


def test_exec_passes_an_argv_list_untouched() -> None:
    container = FakeContainer(output=b"ok\n")
    result = run(exec_command(handle(container), ["echo", "a b; rm -rf /"]))

    assert container.calls == [(["echo", "a b; rm -rf /"], "/workspace")]
    assert result.output == "ok\n"  # type: ignore[attr-defined]
    assert result.ok is True  # type: ignore[attr-defined]


def test_exec_honours_an_explicit_workdir() -> None:
    container = FakeContainer()

    run(exec_command(handle(container), ["pytest"], workdir="tests"))

    assert container.calls[0][1] == "/workspace/tests"


def test_exec_refuses_an_empty_argv() -> None:
    with pytest.raises(ToolError, match="argv is empty"):
        run(exec_command(handle(FakeContainer()), []))


def test_exec_refuses_a_nul_byte() -> None:
    with pytest.raises(ToolError, match="NUL"):
        run(exec_command(handle(FakeContainer()), ["cat", "a\x00b"]))


def test_output_is_capped_with_an_explicit_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sandbox_output_limit", 10)
    container = FakeContainer(output=b"0123456789abcdef")

    result = run(exec_command(handle(container), ["cat", "big.txt"]))

    assert result.output == "0123456789" + TRUNCATION_MARKER  # type: ignore[attr-defined]
    assert result.truncated is True  # type: ignore[attr-defined]


def test_exec_reports_a_non_zero_exit_code() -> None:
    container = FakeContainer(output=b"boom\n", exit_code=2)

    result = run(exec_command(handle(container), ["false"]))

    assert result.exit_code == 2  # type: ignore[attr-defined]
    assert result.ok is False  # type: ignore[attr-defined]


def test_read_file_reports_a_failure() -> None:
    container = FakeContainer(output=b"cat: missing: No such file", exit_code=1)

    with pytest.raises(ToolError, match="cannot read"):
        run(read_file(handle(container), "missing.txt"))


def test_read_file_returns_the_content() -> None:
    container = FakeContainer(output=b"VALUE = 1\n")

    assert run(read_file(handle(container), "module.py")) == "VALUE = 1\n"


def test_write_file_sends_a_tar_without_a_shell() -> None:
    container = FakeContainer()

    run(write_file(handle(container), "pkg/new.py", "X = 1\n"))

    assert container.calls == []
    directory, payload = container.archives[0]
    assert directory == "/workspace/pkg"
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r") as archive:
        member = archive.getmembers()[0]
        assert member.name == "new.py"
        extracted = archive.extractfile(member)
        assert extracted is not None
        assert extracted.read() == b"X = 1\n"


def test_write_file_refuses_to_escape_the_workspace() -> None:
    with pytest.raises(ToolError, match="escapes the workspace"):
        run(write_file(handle(FakeContainer()), "../../etc/passwd", "root"))


def test_list_dir_hides_dot_entries() -> None:
    container = FakeContainer(output=b".\n..\nmodule.py\ntests\n")

    assert run(list_dir(handle(container), ".")) == ("module.py", "tests")
