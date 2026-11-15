"""AI Judge: an independent model assessment of a persisted run (PLAN_V1.1 §2).

The package is deliberately small and provider-agnostic: a provider boundary, a validated
response schema, versioned prompts, one evaluator, the three modes, the escalation rules and
the aggregation policy. Nothing here re-runs an agent or reads a container.
"""

from __future__ import annotations

from fixjury.judge.aggregate import Aggregation, aggregate
from fixjury.judge.escalation import Escalation, should_escalate, triggers_for
from fixjury.judge.evaluator import Assessment, JudgeError, assess, render_context
from fixjury.judge.modes import JudgeRun, JudgeSetup, run
from fixjury.judge.prompts import ESCALATE_PROMPT, EVALUATE_PROMPT, Prompt, PromptError, load_prompt
from fixjury.judge.provider import ChatCompletionsProvider, JudgeProvider, ProviderResponse
from fixjury.judge.schema import (
    CriterionAssessment,
    JudgeResponse,
    JudgeResponseError,
    parse_response,
)

__all__ = [
    "ESCALATE_PROMPT",
    "EVALUATE_PROMPT",
    "Aggregation",
    "Assessment",
    "ChatCompletionsProvider",
    "CriterionAssessment",
    "Escalation",
    "JudgeError",
    "JudgeProvider",
    "JudgeResponse",
    "JudgeResponseError",
    "JudgeRun",
    "JudgeSetup",
    "Prompt",
    "PromptError",
    "ProviderResponse",
    "aggregate",
    "assess",
    "load_prompt",
    "parse_response",
    "render_context",
    "run",
    "should_escalate",
    "triggers_for",
]
