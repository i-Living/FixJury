from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from fixjury.llm import LlmError, complete
from fixjury.spec.generator import (
    DraftError,
    DraftResult,
    IssueContext,
    build_messages,
    draft_from_text,
    extract_yaml,
    generate_draft,
)

ISSUE = IssueContext(
    number=2712,
    url="https://github.com/pallets/click/issues/2712",
    title="Dashed option names break shell completion",
    body="Completion output for --foo-bar emits --foo_bar.",
)
CANDIDATES = ("src/click/shell_completion.py", "tests/test_shell_completion.py")

DRAFT_YAML = """\
title: Dashed option names break shell completion
acceptance:
  - completion output keeps the dash in the option name
checks:
  - pytest tests/test_shell_completion.py
allowed_paths:
  - src/click/shell_completion.py
  - tests/test_shell_completion.py
forbidden:
  - changes to public signatures in src/click/core.py
difficulty: medium
"""


def fenced(text: str) -> str:
    return f"```yaml\n{text}```\n"


def client_answering(
    answers: list[str], *, seen: list[httpx.Request] | None = None
) -> httpx.AsyncClient:
    queue = list(answers)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        answer = queue.pop(0)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": answer}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def draft(text: str) -> Any:
    return draft_from_text(
        text,
        task_id="T-001",
        repo="https://github.com/pallets/click",
        issue=ISSUE,
        candidate_paths=CANDIDATES,
    )


def test_extract_yaml_removes_fences() -> None:
    assert extract_yaml(fenced("title: x\n")) == "title: x"
    assert extract_yaml("title: x\n") == "title: x"


def test_build_messages_grounds_the_model_in_candidate_paths() -> None:
    messages = build_messages(
        prompt="PROMPT",
        repo="https://github.com/pallets/click",
        issue=ISSUE,
        task_id="T-001",
        candidate_paths=CANDIDATES,
    )

    assert messages[0] == {"role": "system", "content": "PROMPT"}
    user = messages[1]["content"]
    assert "T-001" in user
    assert "src/click/shell_completion.py" in user
    assert "Completion output for --foo-bar" in user


def test_draft_from_text_forces_identity_fields() -> None:
    document = draft(fenced(DRAFT_YAML))

    assert document.id == "T-001"
    assert document.repo == "https://github.com/pallets/click"
    assert document.issue.number == 2712
    assert document.heldout_ref == "benchmarks/heldout/T-001"
    assert document.frozen is False
    assert document.difficulty.value == "medium"
    assert document.checks == ("pytest tests/test_shell_completion.py",)


def test_draft_rejects_an_invented_allowed_path() -> None:
    bad = DRAFT_YAML.replace("  - tests/test_shell_completion.py", "  - tests/test_other.py")

    with pytest.raises(DraftError, match="allowed path"):
        draft(bad)


def test_draft_rejects_an_invented_check_path() -> None:
    bad = DRAFT_YAML.replace("pytest tests/test_shell_completion.py", "pytest tests/test_other.py")

    with pytest.raises(DraftError, match="check path"):
        draft(bad)


def test_draft_rejects_non_pytest_checks() -> None:
    bad = DRAFT_YAML.replace("pytest tests/test_shell_completion.py", "ruff check src")

    with pytest.raises(DraftError, match="only pytest"):
        draft(bad)


def test_draft_accepts_a_directory_covering_a_candidate() -> None:
    text = DRAFT_YAML.replace(
        "allowed_paths:\n  - src/click/shell_completion.py\n  - tests/test_shell_completion.py",
        "allowed_paths:\n  - src/click\n  - tests",
    )

    document = draft(text)

    assert document.allowed_paths == ("src/click", "tests")


def test_draft_surfaces_a_model_refusal() -> None:
    with pytest.raises(DraftError, match="too vague"):
        draft("error: the issue is too vague\n")


@pytest.mark.asyncio
async def test_complete_sends_the_opencode_session_header() -> None:
    seen: list[httpx.Request] = []
    async with client_answering(["hello"], seen=seen) as client:
        text, tokens_in, tokens_out = await complete(
            client=client,
            base_url="https://opencode.ai/zen/go/v1",
            model="deepseek",
            messages=[{"role": "user", "content": "hi"}],
            api_key="secret",
            session="session-1",
        )

    assert (text, tokens_in, tokens_out) == ("hello", 11, 7)
    request = seen[0]
    assert request.url.path.endswith("/chat/completions")
    assert request.headers["x-opencode-session"] == "session-1"
    assert request.headers["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_complete_requires_a_model() -> None:
    async with client_answering(["hello"]) as client:
        with pytest.raises(LlmError, match="FIXJURY_LLM_MODEL"):
            await complete(client=client, base_url="https://example.test/v1", model="", messages=[])


@pytest.mark.asyncio
async def test_generate_draft_retries_once_with_the_error() -> None:
    bad = DRAFT_YAML.replace("  - tests/test_shell_completion.py", "  - tests/test_other.py")

    async with client_answering([fenced(bad), fenced(DRAFT_YAML)]) as client:
        result = await generate_draft(
            client=client,
            base_url="https://example.test/v1",
            model="model",
            api_key="",
            session="",
            repo="https://github.com/pallets/click",
            issue=ISSUE,
            task_id="T-001",
            candidate_paths=CANDIDATES,
        )

    assert isinstance(result, DraftResult)
    assert result.attempts == 2
    assert result.tokens_in == 22
    assert result.tokens_out == 14
    assert result.document.id == "T-001"


@pytest.mark.asyncio
async def test_generate_draft_gives_up_after_two_attempts() -> None:
    bad = "error: not enough information\n"

    async with client_answering([bad, bad]) as client:
        with pytest.raises(DraftError, match="no valid draft"):
            await generate_draft(
                client=client,
                base_url="https://example.test/v1",
                model="model",
                api_key="",
                session="",
                repo="https://github.com/pallets/click",
                issue=ISSUE,
                task_id="T-001",
                candidate_paths=CANDIDATES,
            )


def test_generated_draft_passes_the_freezing_contract() -> None:
    """A valid draft has everything freeze needs and nothing frozen yet."""

    document = draft(fenced(DRAFT_YAML))

    assert document.frozen_at is None
    assert document.license is None
    assert document.base_commit is None
    assert document.acceptance
    assert asyncio.iscoroutinefunction(generate_draft)
