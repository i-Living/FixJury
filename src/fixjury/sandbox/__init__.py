"""Sandboxed execution: one container per run, diff capture, in-container tools."""

from __future__ import annotations

from fixjury.sandbox.container import (
    SandboxError,
    SandboxHandle,
    SandboxSpec,
    open_sandbox,
    reap_sandboxes,
)
from fixjury.sandbox.diff import CapturedDiff, DiffError, UnreadableDiff, capture_diff
from fixjury.sandbox.network import NetworkError, RunNetwork, open_run_network, reap_networks
from fixjury.sandbox.tools import (
    ExecResult,
    ToolError,
    exec_command,
    list_dir,
    read_file,
    write_file,
)

__all__ = [
    "CapturedDiff",
    "DiffError",
    "ExecResult",
    "NetworkError",
    "RunNetwork",
    "SandboxError",
    "SandboxHandle",
    "SandboxSpec",
    "ToolError",
    "UnreadableDiff",
    "capture_diff",
    "exec_command",
    "list_dir",
    "open_run_network",
    "open_sandbox",
    "read_file",
    "reap_networks",
    "reap_sandboxes",
    "write_file",
]
