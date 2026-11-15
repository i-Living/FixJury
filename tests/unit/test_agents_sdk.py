"""The five-operation adapter contract (PLAN_V1.1 §3.2, SPEC §6.2)."""

from __future__ import annotations

import ast
import asyncio
from decimal import Decimal
from pathlib import Path

import pytest

from fixjury.agents.base import AgentResult, RunRequest
from fixjury.agents.sdk import (
    SdkError,
    collect,
    execute,
    get_usage,
    prepare,
    terminate,
)

SDK_PATH = Path(__file__).resolve().parents[2] / "src" / "fixjury" / "agents" / "sdk.py"


def request(**kwargs: object) -> RunRequest:
    defaults: dict[str, object] = {
        "run_id": "6f1c2b4d-8a1e-4c3b-9d2f-5e6a7b8c9d0e",
        "repo_url": "https://github.com/Delgan/loguru",
        "base_commit": "a" * 40,
        "spec": "# Task T-001",
        "workspace": Path("workspace"),
        "image": "fixjury-mini:2.4.6",
        "max_steps": 30,
        "timeout_s": 600,
    }
    defaults.update(kwargs)
    return RunRequest(**defaults)  # type: ignore[arg-type]


def result(**kwargs: object) -> AgentResult:
    defaults: dict[str, object] = {
        "diff": "diff --git a/x b/x\n",
        "diff_sha256": "b" * 64,
        "declared_success": True,
        "unable_reason": None,
        "tokens_in": 1200,
        "tokens_out": 300,
        "steps": 7,
        "sandbox_seconds": Decimal("42.50"),
        "summary": "patched",
    }
    defaults.update(kwargs)
    return AgentResult(**defaults)  # type: ignore[arg-type]


class FakeAdapter:
    """A third-party harness reduced to the one method an adapter has to implement."""

    name = "fake"
    version = "1.0.0"

    def __init__(self, *, answer: AgentResult | None = None, explode: bool = False) -> None:
        self.answer = answer or result()
        self.explode = explode
        self.calls: list[RunRequest] = []

    async def run(self, request: RunRequest) -> AgentResult:
        self.calls.append(request)
        if self.explode:
            raise RuntimeError("the harness died")
        return self.answer


def test_the_lifecycle_yields_the_diff_and_the_usage() -> None:
    adapter = FakeAdapter()

    session = prepare(adapter, request())
    answer = asyncio.run(execute(session))
    usage = get_usage(session)
    terminate(session)

    assert answer.diff.startswith("diff --git")
    assert collect(session) is answer
    assert usage.tokens_in == 1200
    assert usage.tokens_out == 300
    assert usage.sandbox_seconds == Decimal("42.50")
    assert usage.steps == 7
    assert adapter.calls == [session.request], "the adapter sees the request it was prepared with"


def test_prepare_does_not_run_the_agent() -> None:
    """A prepared session has done nothing yet, so a failed preparation costs no container."""

    adapter = FakeAdapter()

    session = prepare(adapter, request())

    assert adapter.calls == []
    assert session.result is None
    with pytest.raises(SdkError, match="nothing to collect"):
        collect(session)


def test_a_second_execution_is_refused() -> None:
    """A retry is a new run: overwriting the first diff would lose the evidence."""

    session = prepare(FakeAdapter(), request())
    asyncio.run(execute(session))

    with pytest.raises(SdkError, match="already executed"):
        asyncio.run(execute(session))


def test_a_terminated_session_cannot_execute() -> None:
    session = prepare(FakeAdapter(), request())
    terminate(session)

    with pytest.raises(SdkError, match="terminated"):
        asyncio.run(execute(session))


def test_terminate_is_idempotent_and_never_raises() -> None:
    session = prepare(FakeAdapter(), request())

    terminate(session)
    terminate(session)

    assert session.terminated is True


def test_a_failed_execution_leaves_nothing_to_collect() -> None:
    session = prepare(FakeAdapter(explode=True), request())

    with pytest.raises(RuntimeError, match="harness died"):
        asyncio.run(execute(session))

    # The platform still terminates: one crashed run is not a reason to leak a session.
    terminate(session)
    with pytest.raises(SdkError, match="nothing to collect"):
        collect(session)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("run_id", " ", "run needs an id"),
        ("repo_url", "", "repository url"),
        ("base_commit", "abc123", "40-character sha"),
        ("spec", "  ", "repair specification"),
        ("image", "", "pinned image"),
        ("max_steps", 0, "max_steps"),
        ("timeout_s", 0, "timeout_s"),
    ],
)
def test_an_unrunnable_request_is_refused_before_a_container_starts(
    field: str, value: object, message: str
) -> None:
    with pytest.raises(SdkError, match=message):
        prepare(FakeAdapter(), request(**{field: value}))


def test_an_adapter_without_a_name_is_refused() -> None:
    adapter = FakeAdapter()
    adapter.name = " "

    with pytest.raises(SdkError, match="name"):
        prepare(adapter, request())


def test_the_sdk_touches_no_database_and_no_global_settings() -> None:
    """AGENTS.md invariant 6: an adapter layer that reads the platform is not an adapter."""

    tree = ast.parse(SDK_PATH.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    # `__future__` is a compiler directive, not a dependency.
    imported.discard("__future__")
    assert imported == {"dataclasses", "decimal", "fixjury.agents.base"}
