"""CI guard: an agent container is handed the workspace and nothing else.

PLAN §5 asks for a guard that fails if the container an agent runs in gets a
mount from ``benchmarks/``. The guarantee is structural — ``_create_container``
builds one bind for the workspace — and this test is the tripwire on it: a
second mount, or a mount whose source is the corpus, fails here before a
published run could silently hand an agent the held-out suite.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import docker

from fixjury.sandbox.container import SandboxSpec, _create_container


class _Images:
    def get(self, image: str) -> object:
        return object()


class _Containers:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    def create(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        return object()


class _Client:
    def __init__(self) -> None:
        self.images = _Images()
        self.containers = _Containers()


def created_spec(workspace: Path) -> dict[str, object]:
    client = _Client()
    spec = SandboxSpec(image="python:3.12-slim", workspace=workspace)
    _create_container(cast(docker.DockerClient, client), spec)
    return client.containers.kwargs


def test_the_agent_container_gets_one_bind_and_it_is_the_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "checkout"
    workspace.mkdir()

    kwargs = created_spec(workspace)

    assert kwargs["volumes"] == {str(workspace): {"bind": "/workspace", "mode": "rw"}}


def test_the_agent_container_cannot_see_the_corpus(tmp_path: Path) -> None:
    workspace = tmp_path / "checkout"
    workspace.mkdir()

    volumes = created_spec(workspace)["volumes"]
    assert isinstance(volumes, dict)

    for source in volumes:
        path = Path(str(source)).resolve()
        assert "benchmarks" not in path.parts, f"the agent container mounts {path}"
        assert path == workspace.resolve()


def test_the_agent_container_has_no_route_out_and_a_read_only_root(tmp_path: Path) -> None:
    workspace = tmp_path / "checkout"
    workspace.mkdir()

    kwargs = created_spec(workspace)

    assert kwargs["network_mode"] == "none"
    assert kwargs["read_only"] is True
