"""The adapter contract (PLAN §6, invariant 6).

An adapter is stateless: everything it needs arrives in :class:`RunRequest`, and
it may not touch the database or read global settings. That is what makes two
runs of the same task comparable, and what lets an adapter live inside a
container later without rewriting it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class RunRequest:
    """Everything one run needs, resolved by the platform beforehand."""

    run_id: str
    repo_url: str
    base_commit: str
    spec: str
    workspace: Path
    image: str
    max_steps: int = 30
    timeout_s: int = 1200


@dataclass(frozen=True)
class AgentResult:
    """What an adapter reports back. The platform still verifies the diff."""

    diff: str
    diff_sha256: str
    declared_success: bool
    unable_reason: str | None
    tokens_in: int
    tokens_out: int
    steps: int
    sandbox_seconds: Decimal
    summary: str


type ModelCall = Callable[[list[dict[str, str]]], Awaitable[tuple[str, int, int]]]
"""Send the conversation, get ``(answer, tokens_in, tokens_out)``."""


class AgentAdapter(Protocol):
    """A coding agent the platform can run against a task."""

    name: str
    version: str

    async def run(self, request: RunRequest) -> AgentResult: ...
