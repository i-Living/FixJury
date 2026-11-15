"""Agent attribution (PLAN_V1.1 §5.3): declared, or honestly unknown."""

from __future__ import annotations

import pytest

from fixjury.agents.metadata import UNKNOWN, AgentMetadata, describe, describe_all
from fixjury.agents.registry import UnknownAgentError, known_agents
from fixjury.config import settings


def test_the_baseline_declares_itself() -> None:
    metadata = describe("baseline")

    assert metadata.kind == "baseline"
    assert metadata.version
    assert metadata.attribution == "declared"
    assert metadata.known is True


def test_a_pinned_harness_declares_its_image() -> None:
    metadata = describe("mini")

    assert metadata.kind == "container-harness"
    assert metadata.image == settings.mini_image
    assert metadata.version == settings.mini_version
    assert metadata.known is True


def test_a_harness_without_an_image_is_explicitly_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A harness nobody pinned cannot be attributed: guessing the tag would be a lie."""

    monkeypatch.setattr(settings, "openhands_image", "")

    metadata = describe("openhands")

    assert metadata.image is None
    assert metadata.attribution == UNKNOWN
    assert metadata.known is False


def test_an_unregistered_name_is_refused_and_kept_as_unknown() -> None:
    with pytest.raises(UnknownAgentError, match="unknown"):
        describe("nobody")

    described = describe_all((*known_agents(), "nobody"))

    unknown = next(item for item in described if item.name == "nobody")
    assert unknown == AgentMetadata(
        name="nobody", version=UNKNOWN, kind=UNKNOWN, image=None, attribution=UNKNOWN
    )


def test_every_registered_agent_can_be_described() -> None:
    described = describe_all(known_agents())

    assert {item.name for item in described} == set(known_agents())
    assert all(item.attribution in {"declared", UNKNOWN} for item in described)
