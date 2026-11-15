"""Agent attribution (PLAN_V1.1 §5.3).

Which agent produced a run, and which model, is recorded on every run already. What V1.1 adds
is a way to *describe* an integration without running it, and one rule for the cases it
cannot answer: unknown stays unknown.

The rule matters more than the description. An attribution invented from a container image
name or a model family would end up in a published table as a fact, and a benchmark that
guesses which model answered is worse than one that says it does not know. So a harness whose
version was never pinned, or an adapter nobody registered, reports ``unknown`` here — the
same word the snapshot uses when it has no number.
"""

from __future__ import annotations

from dataclasses import dataclass

from fixjury.agents.registry import HARNESSES, UnknownAgentError, build_adapter

#: What a run records when nobody can say which agent version or model produced it.
UNKNOWN = "unknown"


@dataclass(frozen=True)
class AgentMetadata:
    """One integration as the platform can describe it without running it."""

    name: str
    version: str
    #: How the agent executes: the baseline talks to a model from the platform process, a
    #: harness runs inside the sandbox.
    kind: str
    #: The image a container harness was pinned to, or ``None`` for the baseline.
    image: str | None
    #: ``declared`` when the adapter reports its own identity, ``unknown`` when it cannot.
    attribution: str

    @property
    def known(self) -> bool:
        return self.attribution == "declared"


def describe(name: str) -> AgentMetadata:
    """Describe one registered integration, refusing to guess what it does not know."""

    try:
        adapter = build_adapter(name)
    except UnknownAgentError as exc:
        raise UnknownAgentError(f"{exc}; attribution for it is unknown") from exc
    version = getattr(adapter, "version", "") or UNKNOWN
    if name in HARNESSES:
        # A container harness declares itself through its spec: an empty image means nobody
        # pinned one, and that is exactly the case where attribution is unknown.
        spec = getattr(adapter, "spec", None)
        image = getattr(spec, "image", "") or ""
        attribution = "declared" if version != UNKNOWN and image else UNKNOWN
        return AgentMetadata(
            name=name,
            version=version,
            kind="container-harness",
            image=image or None,
            attribution=attribution,
        )
    return AgentMetadata(
        name=name,
        version=version,
        kind="baseline",
        image=None,
        attribution="declared" if version != UNKNOWN else UNKNOWN,
    )


def describe_all(names: tuple[str, ...]) -> tuple[AgentMetadata, ...]:
    """Describe every integration, keeping an unregistered name instead of dropping it."""

    described: list[AgentMetadata] = []
    for name in names:
        try:
            described.append(describe(name))
        except UnknownAgentError:
            described.append(
                AgentMetadata(
                    name=name,
                    version=UNKNOWN,
                    kind=UNKNOWN,
                    image=None,
                    attribution=UNKNOWN,
                )
            )
    return tuple(described)


__all__ = ["UNKNOWN", "AgentMetadata", "describe", "describe_all"]
