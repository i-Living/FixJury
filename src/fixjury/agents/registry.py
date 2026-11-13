"""The adapter registry: a name in the CLI becomes an adapter object."""

from __future__ import annotations

import shlex
from collections.abc import Callable

from fixjury.agents import mini
from fixjury.agents.base import AgentAdapter
from fixjury.agents.baseline import BaselineAdapter
from fixjury.agents.container_harness import ContainerHarnessAdapter, HarnessSpec
from fixjury.config import settings


class UnknownAgentError(ValueError):
    """No adapter is registered under that name."""


ADAPTERS: dict[str, Callable[[], AgentAdapter]] = {
    "baseline": BaselineAdapter,
}

# Third-party harnesses run inside a container. Their image, command and version
# are configuration: an empty image means "not pinned yet", and the adapter says
# so instead of pulling a tag nobody chose.
HARNESSES = (mini.HARNESS, "openhands")


def harness_spec(name: str) -> HarnessSpec:
    if name == mini.HARNESS:
        image, command, version = (
            settings.mini_image,
            settings.mini_command,
            settings.mini_version,
        )
        return HarnessSpec(
            name=name,
            version=version or "unpinned",
            image=image,
            command=tuple(shlex.split(command)) if command else mini.COMMAND,
            env=mini.ENV,
            config_file=mini.CONFIG_FILENAME,
            config_text=mini.config_text,
            trajectory_file=mini.TRAJECTORY_FILENAME,
            trajectory_success_status=mini.SUCCESS_STATUS,
        )
    if name == "openhands":
        image, command, version = (
            settings.openhands_image,
            settings.openhands_command,
            settings.openhands_version,
        )
    else:
        raise UnknownAgentError(f"no harness named {name!r}")
    return HarnessSpec(
        name=name,
        version=version or "unpinned",
        image=image,
        command=tuple(shlex.split(command)) if command else (),
    )


def build_adapter(name: str) -> AgentAdapter:
    factory = ADAPTERS.get(name)
    if factory is not None:
        return factory()
    if name in HARNESSES:
        return ContainerHarnessAdapter(harness_spec(name))
    known = ", ".join(sorted([*ADAPTERS, *HARNESSES]))
    raise UnknownAgentError(f"unknown agent {name!r}; known agents: {known}")


def known_agents() -> tuple[str, ...]:
    return tuple(sorted([*ADAPTERS, *HARNESSES]))
