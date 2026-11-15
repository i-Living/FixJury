from __future__ import annotations

import hashlib
import json
import uuid

import pytest

from fixjury.evaluation.contracts import (
    CheckResult,
    CheckStatus,
    EvaluationConfiguration,
    EvaluationContext,
    EvaluationRequest,
)
from fixjury.judge.evaluator import JudgeError, assess, render_context
from fixjury.judge.prompts import EVALUATE_PROMPT, load_prompt
from fixjury.judge.provider import ProviderResponse
from fixjury.judge.schema import JudgeResponseError
from fixjury.models import Criterion
from fixjury.tasks.schema import CheckTool

DIFF = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-broken\n+fixed\n"


class FakeProvider:
    """A provider that answers from a script and never opens a socket."""

    name = "fake"

    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.calls: list[dict[str, object]] = []

    async def evaluate(
        self, *, model: str, system: str, user: str, timeout_s: float
    ) -> ProviderResponse:
        self.calls.append({"model": model, "system": system, "user": user, "timeout_s": timeout_s})
        return ProviderResponse(
            text=self.replies.pop(0), model=model, tokens_in=100, tokens_out=50, latency_ms=1200
        )


def reply(*, verdict: str = "ACCEPTED", confidence: float = 0.9, score: float = 90.0) -> str:
    return json.dumps(
        {
            "verdict": verdict,
            "confidence": confidence,
            "criteria": [
                {"criterion": criterion.value, "score": score, "rationale": "checked"}
                for criterion in Criterion
            ],
            "findings": [],
            "reasoning_summary": "the diff replaces the failing call",
            "limitations": [],
            "escalation_required": False,
        }
    )


def context(**overrides: object) -> EvaluationContext:
    request = EvaluationRequest(
        subject_ref="T-001",
        run_id=uuid.uuid4(),
        base_commit="a" * 40,
        diff=DIFF,
        diff_sha256=hashlib.sha256(DIFF.encode("utf-8")).hexdigest(),
        spec_text="# Task T-001\n\nFix the value.\n",
        checks=(
            CheckResult(
                name="pytest tests", tool=CheckTool.pytest, status=CheckStatus.PASSED, exit_code=0
            ),
        ),
        config=EvaluationConfiguration(),
    )
    return EvaluationContext.from_request(
        request,
        changed_paths=("app.py",),
        heldout_passed=True,
        limitations=("the coverage check was skipped",),
    )


def test_the_rendered_context_is_deterministic() -> None:
    first = render_context(context())
    second = render_context(context())

    assert first == second


def test_the_rendered_context_carries_the_task_and_the_evidence() -> None:
    rendered = render_context(context())

    assert "Fix the value." in rendered
    assert "app.py" in rendered
    assert "pytest tests: PASSED (exit 0)" in rendered
    assert "held-out suite" in rendered.lower()
    assert "the coverage check was skipped" in rendered


def test_the_rendered_context_never_names_an_agent() -> None:
    rendered = render_context(context()).lower()

    for forbidden in ("agent", "model", "baseline", "openhands"):
        assert forbidden not in rendered


def test_an_empty_diff_says_so_instead_of_rendering_nothing() -> None:
    rendered = render_context(context())

    assert "```diff" in rendered


@pytest.mark.asyncio
async def test_an_assessment_carries_the_prompt_version_and_its_usage() -> None:
    provider = FakeProvider([reply()])
    prompt = load_prompt(EVALUATE_PROMPT)

    assessment = await assess(
        provider,
        judge_id="primary",
        model="a-stronger-model",
        context=context(),
        prompt=prompt,
        timeout_s=30.0,
    )

    assert assessment.response.confidence == 0.9
    assert assessment.prompt_version == prompt.version
    assert assessment.tokens_in == 100
    assert assessment.summary().prompt_version == prompt.version
    assert provider.calls[0]["model"] == "a-stronger-model"
    assert provider.calls[0]["system"] == prompt.text


@pytest.mark.asyncio
async def test_a_judge_without_a_model_is_refused_before_the_call() -> None:
    provider = FakeProvider([reply()])

    with pytest.raises(JudgeError, match="no model configured"):
        await assess(
            provider,
            judge_id="primary",
            model="",
            context=context(),
            prompt=load_prompt(EVALUATE_PROMPT),
            timeout_s=30.0,
        )

    assert provider.calls == []


@pytest.mark.asyncio
async def test_a_malformed_reply_is_a_failed_assessment_not_a_verdict() -> None:
    provider = FakeProvider(["I could not decide."])

    with pytest.raises(JudgeResponseError):
        await assess(
            provider,
            judge_id="primary",
            model="a-stronger-model",
            context=context(),
            prompt=load_prompt(EVALUATE_PROMPT),
            timeout_s=30.0,
        )
