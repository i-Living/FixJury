"""The three evaluation modes (SPEC §4.2, PLAN_V1.1 §2.3).

- **single** — one judge, one verdict. The cheapest mode and the one that has to work when
  nothing else is configured.
- **adaptive** — one judge, then a second (and a third) only when an escalation trigger
  fires and the budget allows it. This is the default.
- **multi** — every configured judge answers every patch, and disagreements are recorded.

Escalated assessments use the escalation prompt and are labelled as such, so a reader can
tell a first opinion from a second.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fixjury.evaluation.contracts import EvaluationConfiguration, EvaluationContext
from fixjury.judge.aggregate import Aggregation, aggregate
from fixjury.judge.escalation import Escalation, should_escalate
from fixjury.judge.evaluator import Assessment, assess
from fixjury.judge.prompts import ESCALATE_PROMPT, EVALUATE_PROMPT, Prompt, load_prompt
from fixjury.judge.provider import JudgeProvider
from fixjury.metrics.cost import Pricing


@dataclass(frozen=True)
class JudgeSetup:
    """Who judges, with which model, through which provider."""

    provider: JudgeProvider
    primary_model: str
    escalation_model: str = ""
    prices: Pricing | None = None

    def models(self) -> tuple[str, ...]:
        if self.escalation_model and self.escalation_model != self.primary_model:
            return (self.primary_model, self.escalation_model)
        return (self.primary_model,)


@dataclass(frozen=True)
class JudgeRun:
    """Everything one evaluation of one patch produced."""

    assessments: tuple[Assessment, ...]
    escalations: tuple[Escalation, ...] = ()
    aggregation: Aggregation = field(
        default_factory=lambda: Aggregation(verdict=None, confidence=0.0)
    )


def _prompts() -> tuple[Prompt, Prompt]:
    return load_prompt(EVALUATE_PROMPT), load_prompt(ESCALATE_PROMPT)


async def run(
    setup: JudgeSetup,
    *,
    context: EvaluationContext,
    config: EvaluationConfiguration,
) -> JudgeRun:
    """Run the configured mode over one context."""

    primary_prompt, escalation_prompt = _prompts()
    if config.mode.value == "single":
        return await _single(setup, context=context, config=config, prompt=primary_prompt)
    if config.mode.value == "multi":
        return await _multi(
            setup,
            context=context,
            config=config,
            primary=primary_prompt,
            escalation=escalation_prompt,
        )
    return await _adaptive(
        setup, context=context, config=config, primary=primary_prompt, escalation=escalation_prompt
    )


async def _single(
    setup: JudgeSetup,
    *,
    context: EvaluationContext,
    config: EvaluationConfiguration,
    prompt: Prompt,
) -> JudgeRun:
    assessment = await assess(
        setup.provider,
        judge_id="primary",
        model=setup.primary_model,
        context=context,
        prompt=prompt,
        timeout_s=float(config.timeout_s),
        prices=setup.prices,
    )
    return JudgeRun(assessments=(assessment,), aggregation=aggregate((assessment,)))


async def _adaptive(
    setup: JudgeSetup,
    *,
    context: EvaluationContext,
    config: EvaluationConfiguration,
    primary: Prompt,
    escalation: Prompt,
) -> JudgeRun:
    """Primary first, then one more judge per round while a trigger fires and budget lasts."""

    first = await assess(
        setup.provider,
        judge_id="primary",
        model=setup.primary_model,
        context=context,
        prompt=primary,
        timeout_s=float(config.timeout_s),
        prices=setup.prices,
    )
    assessments: list[Assessment] = [first]
    escalations: list[Escalation] = []
    tokens = first.tokens_in + first.tokens_out
    rounds = 0
    while True:
        decision = should_escalate(
            assessment=assessments[-1],
            checks=context.checks,
            confidence_threshold=config.confidence_threshold,
            max_judges=config.max_judges,
            max_rounds=config.max_escalation_rounds,
            judges_used=len(assessments),
            rounds_used=rounds,
            tokens_used=tokens,
            token_budget=config.token_budget,
        )
        escalations.append(decision)
        if not decision.escalate:
            break
        rounds += 1
        model = setup.escalation_model or setup.primary_model
        second = await assess(
            setup.provider,
            judge_id=f"escalation-{rounds}",
            model=model,
            context=context,
            prompt=escalation,
            timeout_s=float(config.timeout_s),
            escalated=True,
            prices=setup.prices,
        )
        assessments.append(second)
        tokens += second.tokens_in + second.tokens_out

    return JudgeRun(
        assessments=tuple(assessments),
        escalations=tuple(escalations),
        aggregation=aggregate(tuple(assessments)),
    )


async def _multi(
    setup: JudgeSetup,
    *,
    context: EvaluationContext,
    config: EvaluationConfiguration,
    primary: Prompt,
    escalation: Prompt,
) -> JudgeRun:
    models = setup.models()
    if len(models) < 2:
        # One configured model cannot be "multi": recording it as a disagreement-free
        # multi run would claim an independence the platform does not have.
        raise ValueError("multi mode needs a second judge model")
    assessments: list[Assessment] = []
    for position, model in enumerate(models[: config.max_judges]):
        assessments.append(
            await assess(
                setup.provider,
                judge_id="primary" if position == 0 else f"judge-{position}",
                model=model,
                context=context,
                prompt=primary if position == 0 else escalation,
                timeout_s=float(config.timeout_s),
                escalated=position > 0,
                prices=setup.prices,
            )
        )
    return JudgeRun(assessments=tuple(assessments), aggregation=aggregate(tuple(assessments)))


__all__ = ["JudgeRun", "JudgeSetup", "run"]
