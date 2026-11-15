"""The judge provider boundary (SPEC §4.1, PLAN_V1.1 §2.1).

One protocol, one implementation, no model name in the evaluation logic. The provider
this platform actually has is an OpenAI-compatible endpoint on the owner's subscription,
reached through :func:`fixjury.llm.complete`, which already knows the session header the
endpoint requires.

Independence is model, session and context separation — not provider separation. That is a
documented limitation, not an accident: the judge must be a different and stronger model
than the agent under test, and its context carries no agent identity.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from fixjury.llm import complete


@dataclass(frozen=True)
class ProviderResponse:
    """One model reply with the numbers needed to audit it."""

    text: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int


class JudgeProvider(Protocol):
    """What the judge needs from a model endpoint."""

    @property
    def name(self) -> str:
        """The provider's name, recorded with every assessment."""
        ...

    async def evaluate(
        self, *, model: str, system: str, user: str, timeout_s: float
    ) -> ProviderResponse: ...


@dataclass(frozen=True)
class ChatCompletionsProvider:
    """An OpenAI-compatible chat endpoint; the only provider this release ships."""

    name: str
    base_url: str
    api_key: str = ""
    session: str = ""
    #: Injected by tests so a unit test never opens a socket.
    transport: httpx.AsyncBaseTransport | None = None

    async def evaluate(
        self, *, model: str, system: str, user: str, timeout_s: float
    ) -> ProviderResponse:
        started = time.monotonic()
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s), transport=self.transport
        ) as client:
            text, tokens_in, tokens_out = await complete(
                client=client,
                base_url=self.base_url,
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                api_key=self.api_key,
                session=self.session,
            )
        return ProviderResponse(
            text=text,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=int((time.monotonic() - started) * 1000),
        )


__all__ = ["ChatCompletionsProvider", "JudgeProvider", "ProviderResponse"]
