"""The adapter SDK (PLAN_V1.1 §3.2, SPEC §6.2).

SPEC asks every coding agent for five operations: ``prepare``, ``execute``, ``collect``,
``terminate`` and ``get_usage``. The adapters this platform already has run all of that
inside one ``run()`` call — they create their own container, drive the harness, capture the
diff and report usage — so the SDK does not replace them.

Two contracts live here, and they are not the same one:

- an **integration implements** :class:`~fixjury.agents.base.AgentAdapter`: ``name``,
  ``version`` and ``run(request)``. That is the whole interface a new agent satisfies;
- a **runner calls** the five functions below, which wrap that one method in a lifecycle it
  can drive without knowing which harness is behind it.

Two rules from AGENTS.md hold here as much as in the adapters:

- the SDK is stateless between runs. A session carries one run's request and its result and
  nothing else; there is no database, no global setting and no shared object;
- terminating is always safe. ``terminate`` closes the session and cancels an execution that
  is still in flight, so a caller can put it in ``finally``; the adapter's container cleanup
  stays the adapter's job, and it runs when its own ``run()`` returns or is cancelled.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal

from fixjury.agents.base import AgentAdapter, AgentResult, RunRequest

#: How long a base commit is, checked here so a typo fails before a container starts.
COMMIT_LENGTH = 40


class SdkError(RuntimeError):
    """The lifecycle was used out of order, or the request it was given cannot run."""


@dataclass(frozen=True)
class Usage:
    """What one run consumed, in the units an adapter can honestly report.

    Money is deliberately absent: an adapter knows tokens and sandbox seconds, and the
    price of those is the platform's pinned pricing, not the harness's opinion.
    """

    tokens_in: int
    tokens_out: int
    sandbox_seconds: Decimal
    steps: int


@dataclass
class AdapterSession:
    """One prepared run: the adapter, what it was asked to do, and what it produced."""

    adapter: AgentAdapter
    request: RunRequest
    result: AgentResult | None = field(default=None)
    #: Set before the adapter is awaited: a retry is a new run, and a second call into the
    #: same session is refused whether the first one succeeded, failed or is still running.
    started: bool = False
    terminated: bool = False
    running: asyncio.Task[AgentResult] | None = field(default=None)


def _validate(request: RunRequest) -> None:
    """Refuse a request the adapter cannot make sense of, before anything is started."""

    if not request.run_id.strip():
        raise SdkError("a run needs an id: the adapter records it in its trajectory")
    if not request.repo_url.strip():
        raise SdkError("a run needs a repository url")
    if len(request.base_commit) != COMMIT_LENGTH:
        raise SdkError(
            f"base commit must be a full {COMMIT_LENGTH}-character sha: {request.base_commit!r}"
        )
    if not request.spec.strip():
        raise SdkError("a run needs a repair specification")
    if not request.image.strip():
        raise SdkError("a container harness needs a pinned image")
    if request.max_steps < 1:
        raise SdkError(f"max_steps must be at least 1, got {request.max_steps}")
    if request.timeout_s < 1:
        raise SdkError(f"timeout_s must be at least 1, got {request.timeout_s}")


def prepare(adapter: AgentAdapter, request: RunRequest) -> AdapterSession:
    """Start the lifecycle: validate the request and bind it to the adapter.

    Nothing is created here on purpose. An adapter's ``prepare`` step is the container it
    starts inside ``run()``, and doing that twice — once here, once there — would be a
    second preparation nobody would notice until two containers disagreed.
    """

    _validate(request)
    if not adapter.name.strip():
        raise SdkError("an adapter must have a name to be recorded against a run")
    return AdapterSession(adapter=adapter, request=request)


async def execute(session: AdapterSession) -> AgentResult:
    """Run the agent once and keep what it returned."""

    if session.terminated:
        raise SdkError("this session is terminated; prepare a new one")
    if session.started:
        # A second execution is a second run, and it would overwrite the first one's diff —
        # whether that first one finished, failed or is still running.
        raise SdkError("this session already executed; a retry is a new run, not a new call")
    session.started = True
    task = asyncio.ensure_future(session.adapter.run(session.request))
    session.running = task
    try:
        result = await task
    except asyncio.CancelledError:
        if not session.terminated:
            raise
        raise SdkError(
            "this session was terminated while executing; the result is discarded"
        ) from None
    finally:
        session.running = None
    if session.terminated:
        raise SdkError("this session was terminated while executing; the result is discarded")
    session.result = result
    return result


def collect(session: AdapterSession) -> AgentResult:
    """The result of the execution, refusing to invent one for a run that did not happen."""

    if session.result is None:
        raise SdkError("nothing to collect: execute the session first")
    return session.result


def terminate(session: AdapterSession) -> None:
    """Close the session and cancel an execution still in flight. Idempotent.

    The adapter's container cleanup happens in its own ``run()``, which is exactly why this
    cancels instead of waiting: the caller asked for the run to stop, not to be kept alive
    until a sandbox finishes tearing itself down. A second call returns immediately — asking
    again would forward a new cancellation into the adapter's own cleanup, which is the one
    thing that must be allowed to finish.
    """

    if session.terminated:
        return
    session.terminated = True
    if session.running is not None and not session.running.done():
        session.running.cancel()


def get_usage(session: AdapterSession) -> Usage:
    """Tokens, sandbox time and steps of the finished execution."""

    result = collect(session)
    return Usage(
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        sandbox_seconds=result.sandbox_seconds,
        steps=result.steps,
    )


__all__ = [
    "COMMIT_LENGTH",
    "AdapterSession",
    "SdkError",
    "Usage",
    "collect",
    "execute",
    "get_usage",
    "prepare",
    "terminate",
]
