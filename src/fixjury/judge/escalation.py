"""When a second judge is worth the money (SPEC §4.4, PLAN_V1.1 §2.4).

Every trigger is a reason a single assessment should not be the last word, and every
trigger is recorded: an escalation nobody can explain afterwards is indistinguishable from
a bug. Budgets are enforced before the call, not after — exhausting one yields an
``INCONCLUSIVE`` result, never an acceptance.
"""

from __future__ import annotations

from dataclasses import dataclass

from fixjury.evaluation.contracts import CheckResult, CheckStatus
from fixjury.judge.evaluator import Assessment
from fixjury.models import Criterion, EvaluationVerdict, FindingSeverity


@dataclass(frozen=True)
class Escalation:
    """A decision, the triggers behind it and the sentence a reviewer will read."""

    escalate: bool
    triggers: tuple[str, ...] = ()
    reason: str = ""


def triggers_for(*, assessment: Assessment, checks: tuple[CheckResult, ...]) -> tuple[str, ...]:
    """Why this assessment alone is not enough."""

    triggers: list[str] = []
    response = assessment.response
    if response.escalation_required:
        triggers.append("the judge asked for a second opinion")
    if response.verdict is EvaluationVerdict.INCONCLUSIVE:
        triggers.append("the judge could not reach a verdict")
    if any(
        finding.severity is FindingSeverity.critical and finding.category is Criterion.security
        for finding in response.findings
    ):
        triggers.append("a critical security finding was raised")
    if response.limitations:
        triggers.append("the judge reported evidence it could not obtain")
    if any(check.status is CheckStatus.ERROR for check in checks):
        triggers.append("an automated check could not run")
    unmeasured = [check for check in checks if check.status is CheckStatus.SKIPPED]
    if unmeasured and response.verdict is EvaluationVerdict.ACCEPTED:
        triggers.append("the judge accepted a patch with skipped checks")
    return tuple(triggers)


def should_escalate(
    *,
    assessment: Assessment,
    checks: tuple[CheckResult, ...],
    confidence_threshold: float,
    max_judges: int,
    max_rounds: int,
    judges_used: int,
    rounds_used: int,
    tokens_used: int,
    token_budget: int,
) -> Escalation:
    """Escalate only when a trigger fires *and* the budget still allows it."""

    triggers = list(triggers_for(assessment=assessment, checks=checks))
    if assessment.response.confidence < confidence_threshold:
        triggers.append(
            f"confidence {assessment.response.confidence:.2f} is below the configured "
            f"{confidence_threshold:.2f}"
        )
    if not triggers:
        return Escalation(escalate=False, reason="no trigger fired")

    reason = "; ".join(triggers)
    if judges_used >= max_judges:
        return Escalation(
            escalate=False,
            triggers=tuple(triggers),
            reason=f"{reason}; not escalated: the judge budget of {max_judges} is spent",
        )
    if rounds_used >= max_rounds:
        return Escalation(
            escalate=False,
            triggers=tuple(triggers),
            reason=f"{reason}; not escalated: the round budget of {max_rounds} is spent",
        )
    if tokens_used >= token_budget:
        return Escalation(
            escalate=False,
            triggers=tuple(triggers),
            reason=f"{reason}; not escalated: the token budget of {token_budget} is spent",
        )
    return Escalation(escalate=True, triggers=tuple(triggers), reason=reason)


__all__ = ["Escalation", "should_escalate", "triggers_for"]
