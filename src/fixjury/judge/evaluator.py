"""One assessment: build the context text, ask the provider, validate the reply.

The context is rendered deterministically — the same request always produces the same
prompt — because an assessment has to be reproducible within the provider's limits, and a
prompt that reorders itself between runs cannot be compared with anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from fixjury.evaluation.contracts import (
    AssessmentSummary,
    EvaluationContext,
    EvaluationFinding,
)
from fixjury.evaluation.scoring import DEFAULT_WEIGHTS
from fixjury.judge.prompts import Prompt
from fixjury.judge.provider import JudgeProvider, ProviderResponse
from fixjury.judge.schema import JudgeResponse, parse_response
from fixjury.metrics.cost import Pricing, pricing


#: What a judge does when the provider fails or answers garbage: it is a failed
#: evaluation, not a verdict about the patch.
class JudgeError(RuntimeError):
    """The judge could not produce a usable assessment."""


@dataclass(frozen=True)
class Assessment:
    """One judge's answer, with everything needed to audit and store it."""

    judge_id: str
    provider: str
    model: str
    prompt_version: str
    response: JudgeResponse
    raw: str
    tokens_in: int
    tokens_out: int
    cost_list_usd: Decimal
    latency_ms: int
    escalated: bool = False

    def summary(self) -> AssessmentSummary:
        return AssessmentSummary(
            judge_id=self.judge_id,
            provider=self.provider,
            model=self.model,
            prompt_version=self.prompt_version,
            verdict=self.response.verdict,
            overall=self.self_reported_score,
            confidence=self.response.confidence,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            cost_list_usd=self.cost_list_usd,
            latency_ms=self.latency_ms,
            escalated=self.escalated,
        )

    @property
    def self_reported_score(self) -> float:
        """The judge's own weighted average of its criterion scores.

        Recorded per assessment so a calibration run can compare what the judge claimed
        with what the engine scored; it never becomes the stored score, which
        :mod:`fixjury.evaluation.scoring` computes from the checks and these criteria.
        """

        scores = {item.criterion: item.score for item in self.response.criteria}
        return round(
            sum(
                scores[criterion] * weight
                for criterion, weight in DEFAULT_WEIGHTS.items()
                if criterion in scores
            )
            / 100.0,
            2,
        )

    @property
    def findings(self) -> tuple[EvaluationFinding, ...]:
        return self.response.findings


def render_context(context: EvaluationContext) -> str:
    """The user message: the task, the patch and the evidence, in a fixed order."""

    lines = [
        f"# Task {context.subject_ref}",
        "",
        "## Task specification",
        "",
        context.spec_text.strip(),
        "",
        "## Base commit",
        "",
        context.base_commit,
        "",
        f"Diff digest: {context.diff_sha256}",
        "",
        "## Patch",
        "",
        "```diff",
        context.diff.rstrip() or "(the patch is empty)",
        "```",
        "",
        "## Changed paths",
        "",
    ]
    lines += [f"- {path}" for path in context.changed_paths] or ["- (none reported)"]
    lines += ["", "## Automated checks", ""]
    if context.checks:
        for check in context.checks:
            lines.append(f"- {check.name}: {check.status.value} (exit {check.exit_code})")
            if check.summary:
                lines.append(f"  {check.summary.strip()}")
    else:
        lines.append("- none recorded")
    lines += [
        "",
        "## Held-out suite",
        "",
        "passed"
        if context.heldout_passed
        else ("failed" if context.heldout_passed is False else "not measured"),
        "",
        "## Path policy",
        "",
        "the patch stays inside the allowed paths"
        if context.policy_pass
        else "the patch violates the task's path policy",
    ]
    if context.limitations:
        lines += ["", "## Known limitations of this evidence", ""]
        lines += [f"- {item}" for item in context.limitations]
    return "\n".join(lines).rstrip() + "\n"


def _cost(model: str, tokens_in: int, tokens_out: int, prices: Pricing | None) -> Decimal:
    table = prices or pricing()
    return table.list_price(
        model=model, tokens_in=tokens_in, tokens_out=tokens_out, sandbox_seconds=Decimal("0")
    )


async def assess(
    provider: JudgeProvider,
    *,
    judge_id: str,
    model: str,
    context: EvaluationContext,
    prompt: Prompt,
    timeout_s: float,
    escalated: bool = False,
    prices: Pricing | None = None,
) -> Assessment:
    """Ask one judge about one context and validate what comes back."""

    if not model:
        raise JudgeError(f"judge {judge_id!r} has no model configured")
    response: ProviderResponse = await provider.evaluate(
        model=model, system=prompt.text, user=render_context(context), timeout_s=timeout_s
    )
    parsed = parse_response(response.text)
    return Assessment(
        judge_id=judge_id,
        provider=provider.name,
        model=model,
        prompt_version=prompt.version,
        response=parsed,
        raw=response.text,
        tokens_in=response.tokens_in,
        tokens_out=response.tokens_out,
        cost_list_usd=_cost(model, response.tokens_in, response.tokens_out, prices),
        latency_ms=response.latency_ms,
        escalated=escalated,
    )


__all__ = ["Assessment", "JudgeError", "assess", "render_context"]
