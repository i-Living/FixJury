"""What a judge is allowed to answer, and the parser that refuses the rest (SPEC §4.5).

A model reply is untrusted input. It arrives as text, it may be wrapped in a code fence,
it may omit a criterion, it may try to mark a finding as confirmed, and it may invent a
verdict. Everything below is checked before a single number reaches the scoring engine.

``source`` and ``confirmed`` are not the model's to set: a judge opinion is never a
confirmed failure, and the parser stamps both fields itself.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fixjury.evaluation.contracts import EvaluationFinding
from fixjury.models import Criterion, EvaluationVerdict, FindingSource


class JudgeResponseError(RuntimeError):
    """The reply cannot be read as a judge assessment."""


class CriterionAssessment(BaseModel):
    """One criterion's score, with the sentence that justifies it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    criterion: Criterion
    score: float = Field(ge=0.0, le=100.0)
    rationale: str = Field(default="", max_length=2000)


class JudgeResponse(BaseModel):
    """The validated answer of one judge."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: EvaluationVerdict
    confidence: float = Field(ge=0.0, le=1.0)
    criteria: tuple[CriterionAssessment, ...]
    findings: tuple[EvaluationFinding, ...] = ()
    reasoning_summary: str = Field(default="", max_length=8000)
    limitations: tuple[str, ...] = ()
    escalation_required: bool = False

    @model_validator(mode="before")
    @classmethod
    def _findings_are_opinions(cls, data: Any) -> Any:
        """Stamp every finding as this judge's opinion, whatever the reply claimed."""

        if not isinstance(data, dict):
            return data
        raw = data.get("findings")
        if not isinstance(raw, list):
            return data
        findings: list[object] = []
        for item in raw:
            if not isinstance(item, dict):
                findings.append(item)
                continue
            findings.append({**item, "source": FindingSource.judge.value, "confirmed": False})
        return {**data, "findings": findings}

    @model_validator(mode="after")
    def _every_criterion_is_answered_once(self) -> JudgeResponse:
        answered = [item.criterion for item in self.criteria]
        missing = sorted(criterion.value for criterion in set(Criterion) - set(answered))
        if missing:
            raise ValueError(f"no score for: {', '.join(missing)}")
        if len(set(answered)) != len(answered):
            raise ValueError("a criterion was scored twice")
        return self


def _payload(text: str) -> dict[str, object]:
    """The JSON object inside a model reply, with its code fence removed if it has one."""

    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
        body = body.rsplit("```", 1)[0].strip()
    start = body.find("{")
    end = body.rfind("}")
    if start < 0 or end <= start:
        raise JudgeResponseError("the reply contains no JSON object")
    try:
        payload = json.loads(body[start : end + 1])
    except json.JSONDecodeError as exc:
        raise JudgeResponseError(f"the reply is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise JudgeResponseError("the reply is not a JSON object")
    return payload


def parse_response(text: str) -> JudgeResponse:
    """Validate a model reply, or refuse it with the reason.

    A malformed reply is not a verdict. Refusing it here keeps a truncated answer from
    becoming an `INCONCLUSIVE` result nobody can tell from an honest one.
    """

    try:
        return JudgeResponse.model_validate(_payload(text))
    except ValueError as exc:
        raise JudgeResponseError(f"the reply does not match the judge schema: {exc}") from exc


__all__ = [
    "CriterionAssessment",
    "JudgeResponse",
    "JudgeResponseError",
    "parse_response",
]
