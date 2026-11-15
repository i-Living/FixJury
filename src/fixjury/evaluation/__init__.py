"""Evaluation core: contracts, structured check evidence and the scoring engine.

The benchmark and, later, GitHub Review share this package: a request built from persisted
rows goes in, an :class:`~fixjury.evaluation.contracts.EvaluationResult` comes out, and the
rules that decide what a verdict may be live in :mod:`fixjury.evaluation.scoring`.
"""

from __future__ import annotations

from fixjury.evaluation.checks import (
    coverage_percent,
    outcomes_from_stored,
    stored_outcomes,
    to_check_results,
)
from fixjury.evaluation.contracts import (
    AssessmentSummary,
    CheckResult,
    CheckStatus,
    CriterionScore,
    EvaluationConfiguration,
    EvaluationContext,
    EvaluationFinding,
    EvaluationRequest,
    EvaluationResult,
    EvaluationScore,
    JudgeMode,
    canonical_json,
    digest,
)
from fixjury.evaluation.scoring import (
    DEFAULT_WEIGHTS,
    ScoringError,
    ScoringProfile,
    assemble_result,
    critical_failures,
    decide_verdict,
    evidence_gaps,
    load_profile,
    score,
)

__all__ = [
    "DEFAULT_WEIGHTS",
    "AssessmentSummary",
    "CheckResult",
    "CheckStatus",
    "CriterionScore",
    "EvaluationConfiguration",
    "EvaluationContext",
    "EvaluationFinding",
    "EvaluationRequest",
    "EvaluationResult",
    "EvaluationScore",
    "JudgeMode",
    "ScoringError",
    "ScoringProfile",
    "assemble_result",
    "canonical_json",
    "coverage_percent",
    "critical_failures",
    "decide_verdict",
    "digest",
    "evidence_gaps",
    "load_profile",
    "outcomes_from_stored",
    "score",
    "stored_outcomes",
    "to_check_results",
]
