from __future__ import annotations

import asyncio
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
    JudgeMode,
)
from fixjury.judge.escalation import should_escalate, triggers_for
from fixjury.judge.evaluator import Assessment, JudgeError, assess
from fixjury.judge.modes import JudgeSetup, run
from fixjury.judge.prompts import EVALUATE_PROMPT, load_prompt
from fixjury.judge.provider import ProviderResponse
from fixjury.models import Criterion, EvaluationVerdict, FindingSeverity
from fixjury.tasks.schema import CheckTool

DIFF = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-broken\n+fixed\n"


class FakeProvider:
    """Answers from a script; records every call so a test can count the judges."""

    name = "fake"

    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.models: list[str] = []

    async def evaluate(
        self, *, model: str, system: str, user: str, timeout_s: float
    ) -> ProviderResponse:
        self.models.append(model)
        return ProviderResponse(
            text=self.replies.pop(0), model=model, tokens_in=100, tokens_out=40, latency_ms=900
        )


def reply(*, verdict: str = "ACCEPTED", confidence: float = 0.9, escalation: bool = False) -> str:
    return json.dumps(
        {
            "verdict": verdict,
            "confidence": confidence,
            "criteria": [
                {"criterion": criterion.value, "score": 85, "rationale": "checked"}
                for criterion in Criterion
            ],
            "findings": [],
            "reasoning_summary": "…",
            "limitations": [],
            "escalation_required": escalation,
        }
    )


def context(checks: tuple[CheckResult, ...] = ()) -> EvaluationContext:
    request = EvaluationRequest(
        subject_ref="T-001",
        run_id=uuid.uuid4(),
        base_commit="a" * 40,
        diff=DIFF,
        diff_sha256=hashlib.sha256(DIFF.encode("utf-8")).hexdigest(),
        spec_text="# Task T-001\n",
        checks=checks,
        config=EvaluationConfiguration(),
    )
    return EvaluationContext.from_request(request, changed_paths=("app.py",))


def build_assessment(provider: FakeProvider) -> Assessment:
    """One synchronous assessment, for the tests that only need to inspect it."""

    return asyncio.run(
        assess(
            provider,
            judge_id="primary",
            model="strong-model",
            context=context(),
            prompt=load_prompt(EVALUATE_PROMPT),
            timeout_s=10.0,
        )
    )


def passing_check() -> CheckResult:
    return CheckResult(
        name="pytest tests", tool=CheckTool.pytest, status=CheckStatus.PASSED, exit_code=0
    )


def setup(provider: FakeProvider, *, escalation_model: str = "") -> JudgeSetup:
    return JudgeSetup(
        provider=provider,
        primary_model="strong-model",
        escalation_model=escalation_model,
    )


@pytest.mark.asyncio
async def test_single_mode_asks_one_judge_and_records_no_escalation() -> None:
    provider = FakeProvider([reply(confidence=0.2)])
    config = EvaluationConfiguration(mode=JudgeMode.single)

    result = await run(setup(provider), context=context((passing_check(),)), config=config)

    assert len(result.assessments) == 1
    assert result.escalations == ()
    assert result.aggregation.verdict is EvaluationVerdict.ACCEPTED


@pytest.mark.asyncio
async def test_adaptive_mode_stops_when_nothing_fires() -> None:
    provider = FakeProvider([reply(confidence=0.9)])
    config = EvaluationConfiguration(mode=JudgeMode.adaptive)

    result = await run(setup(provider), context=context((passing_check(),)), config=config)

    assert len(result.assessments) == 1
    assert result.escalations[0].escalate is False
    assert result.escalations[0].reason == "no trigger fired"


@pytest.mark.asyncio
async def test_adaptive_mode_escalates_on_low_confidence() -> None:
    provider = FakeProvider([reply(confidence=0.2), reply(confidence=0.9)])
    config = EvaluationConfiguration(mode=JudgeMode.adaptive)

    result = await run(
        setup(provider, escalation_model="second-model"), context=context(), config=config
    )

    assert len(result.assessments) == 2
    assert result.assessments[1].escalated is True
    assert "confidence" in result.escalations[0].reason
    assert provider.models == ["strong-model", "second-model"]


@pytest.mark.asyncio
async def test_a_second_rejection_turns_the_result_into_a_rejection() -> None:
    provider = FakeProvider([reply(confidence=0.3), reply(verdict="REJECTED", confidence=0.9)])
    config = EvaluationConfiguration(mode=JudgeMode.adaptive)

    result = await run(setup(provider), context=context(), config=config)

    assert result.aggregation.verdict is EvaluationVerdict.REJECTED
    assert result.aggregation.disagreement is True


@pytest.mark.asyncio
async def test_the_judge_budget_stops_the_escalation() -> None:
    provider = FakeProvider([reply(confidence=0.1)])
    config = EvaluationConfiguration(mode=JudgeMode.adaptive, max_judges=1)

    result = await run(setup(provider), context=context(), config=config)

    assert len(result.assessments) == 1
    assert result.escalations[0].escalate is False
    assert "judge budget" in result.escalations[0].reason
    # The judge wanted a second opinion and could not have one: an acceptance on a
    # question the judge itself asked again is not an acceptance.
    assert result.aggregation.verdict is EvaluationVerdict.INCONCLUSIVE


@pytest.mark.asyncio
async def test_a_zero_token_budget_makes_no_model_call_in_any_mode() -> None:
    for mode in (JudgeMode.single, JudgeMode.adaptive, JudgeMode.multi):
        provider = FakeProvider([reply(), reply()])
        config = EvaluationConfiguration(mode=mode, token_budget=0)

        with pytest.raises(JudgeError, match="token budget is zero"):
            await run(
                setup(provider, escalation_model="second-model"),
                context=context(),
                config=config,
            )

        assert provider.models == [], f"mode {mode.value} called the provider on a zero budget"


@pytest.mark.asyncio
async def test_multi_mode_with_too_few_selected_judges_is_refused() -> None:
    provider = FakeProvider([reply()])
    config = EvaluationConfiguration(mode=JudgeMode.multi, max_judges=1)

    with pytest.raises(ValueError, match="at least two judges"):
        await run(
            setup(provider, escalation_model="second-model"), context=context(), config=config
        )


@pytest.mark.asyncio
async def test_the_round_budget_stops_the_escalation() -> None:
    provider = FakeProvider([reply(confidence=0.1)])
    config = EvaluationConfiguration(mode=JudgeMode.adaptive, max_escalation_rounds=0)

    result = await run(setup(provider), context=context(), config=config)

    assert len(result.assessments) == 1
    assert "round budget" in result.escalations[0].reason


@pytest.mark.asyncio
async def test_the_token_budget_stops_the_escalation() -> None:
    provider = FakeProvider([reply(confidence=0.1)])
    config = EvaluationConfiguration(mode=JudgeMode.adaptive, token_budget=10)

    result = await run(setup(provider), context=context(), config=config)

    assert len(result.assessments) == 1
    assert "token budget" in result.escalations[0].reason


@pytest.mark.asyncio
async def test_multi_mode_asks_every_configured_judge() -> None:
    provider = FakeProvider([reply(), reply(verdict="REJECTED")])
    config = EvaluationConfiguration(mode=JudgeMode.multi)

    result = await run(
        setup(provider, escalation_model="second-model"), context=context(), config=config
    )

    assert len(result.assessments) == 2
    assert result.aggregation.verdict is EvaluationVerdict.REJECTED
    assert provider.models == ["strong-model", "second-model"]


@pytest.mark.asyncio
async def test_multi_mode_without_a_second_model_is_refused() -> None:
    provider = FakeProvider([reply()])
    config = EvaluationConfiguration(mode=JudgeMode.multi)

    with pytest.raises(ValueError, match="at least two judges"):
        await run(setup(provider), context=context(), config=config)


@pytest.mark.asyncio
async def test_a_second_assessment_uses_the_escalation_prompt() -> None:
    provider = FakeProvider([reply(confidence=0.1), reply(confidence=0.9)])
    config = EvaluationConfiguration(mode=JudgeMode.adaptive)

    result = await run(setup(provider), context=context(), config=config)

    primary = load_prompt(EVALUATE_PROMPT)
    assert result.assessments[0].prompt_version == primary.version
    assert result.assessments[1].prompt_version != primary.version


def test_an_error_check_triggers_escalation() -> None:
    assessment = build_assessment(FakeProvider([reply()]))
    checks = (
        CheckResult(
            name="pytest tests", tool=CheckTool.pytest, status=CheckStatus.ERROR, exit_code=3
        ),
    )

    triggers = triggers_for(assessment=assessment, checks=checks)

    assert any("could not run" in trigger for trigger in triggers)


def test_a_low_confidence_triggers_escalation_but_a_budget_can_refuse_it() -> None:
    assessment = build_assessment(FakeProvider([reply(confidence=0.2)]))
    decision = should_escalate(
        assessment=assessment,
        checks=(),
        confidence_threshold=0.6,
        max_judges=3,
        max_rounds=2,
        judges_used=1,
        rounds_used=0,
        tokens_used=0,
        token_budget=1000,
    )

    assert decision.escalate is True
    assert any("confidence" in trigger for trigger in decision.triggers)


def test_a_critical_security_finding_triggers_escalation() -> None:
    body = json.loads(reply())
    body["findings"] = [
        {
            "severity": FindingSeverity.critical.value,
            "category": Criterion.security.value,
            "description": "the id is interpolated into the query",
            "evidence_ref": "diff:app/db.py:31",
            "impact": "any caller can read another tenant's rows",
        }
    ]
    assessment = build_assessment(FakeProvider([json.dumps(body)]))

    triggers = triggers_for(assessment=assessment, checks=())

    assert any("security" in trigger for trigger in triggers)
