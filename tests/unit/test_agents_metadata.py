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


def test_a_pinned_harness_declares_its_image(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pinned here rather than read from the environment: CI has no image configured, and a
    # test that inherited that would assert nothing about the declared path.
    monkeypatch.setattr(settings, "mini_image", "fixjury-mini:9.9.9")
    monkeypatch.setattr(settings, "mini_version", "9.9.9")

    metadata = describe("mini")

    assert metadata.kind == "container-harness"
    assert metadata.image == "fixjury-mini:9.9.9"
    assert metadata.version == "9.9.9"
    assert metadata.known is True


def test_an_unpinned_harness_declares_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mini_image", "")
    monkeypatch.setattr(settings, "mini_version", "")

    metadata = describe("mini")

    # "unpinned" is the honest version string here: nobody chose one, which is why the
    # attribution is unknown rather than declared.
    assert metadata.version == "unpinned"
    assert metadata.image is None
    assert metadata.attribution == UNKNOWN


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


def test_an_image_without_a_version_is_not_a_declared_attribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tag alone says which container ran, not which agent: the version is still unknown."""

    monkeypatch.setattr(settings, "mini_image", "fixjury-mini:2.4.6")
    monkeypatch.setattr(settings, "mini_version", "")

    metadata = describe("mini")

    assert metadata.image == "fixjury-mini:2.4.6"
    assert metadata.version == "unpinned"
    assert metadata.attribution == UNKNOWN
