"""The adapter registry: a name in the CLI becomes an adapter object."""

from __future__ import annotations

from collections.abc import Callable

from fixjury.agents.base import AgentAdapter
from fixjury.agents.baseline import BaselineAdapter


class UnknownAgentError(ValueError):
    """No adapter is registered under that name."""


ADAPTERS: dict[str, Callable[[], AgentAdapter]] = {
    "baseline": BaselineAdapter,
}


def build_adapter(name: str) -> AgentAdapter:
    factory = ADAPTERS.get(name)
    if factory is None:
        known = ", ".join(sorted(ADAPTERS))
        raise UnknownAgentError(f"unknown agent {name!r}; known agents: {known}")
    return factory()


def known_agents() -> tuple[str, ...]:
    return tuple(sorted(ADAPTERS))
