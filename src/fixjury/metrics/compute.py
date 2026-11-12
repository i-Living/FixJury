"""Metric definitions (PLAN §3.2), as pure functions over plain rows.

Everything here is arithmetic on values the database already holds, so the same
inputs always produce the same numbers and a test can pin them. No SQL string in
this module computes a metric, and nothing here reads the clock.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

Z_95 = 1.959963984540054


@dataclass(frozen=True)
class Attempt:
    """One stored run, flattened to what the metrics read."""

    task_id: str
    agent: str
    status: str
    declared_success: bool | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_list_usd: Decimal = Decimal("0")
    diff_sha256: str | None = None
    duration_s: float | None = None
    automated_pass: bool | None = None
    heldout_pass: bool | None = None
    policy_pass: bool | None = None
    human_verdict: str | None = None

    @property
    def finished(self) -> bool:
        """A run that reached the end with something to verify."""

        return self.status == "finished" and bool(self.diff_sha256)


@dataclass(frozen=True)
class Interval:
    low: float
    high: float


@dataclass(frozen=True)
class MetricsReport:
    """Everything §3.2 asks for, plus the denominators that explain it."""

    agent: str
    pairs: int
    accepted: int
    regression_free_accepted: int
    automated_pass_pairs: int
    declared_success_runs: int
    tokens_total: int
    cost_total: Decimal
    human_acceptance_rate: float
    human_acceptance_ci: Interval | None
    regression_free_acceptance_rate: float
    regression_free_acceptance_ci: Interval | None
    task_success_rate: float
    tokens_per_accepted_fix: Decimal | None
    cost_per_accepted_fix: Decimal | None
    median_time_to_repair_s: float | None
    false_completion_rate: float | None
    missing_heldout_pairs: int
    exclusions: dict[str, int] = field(default_factory=dict)


def wilson_interval(successes: int, total: int, z: float = Z_95) -> Interval | None:
    """95% Wilson score interval; ``None`` when there is nothing to divide by."""

    if total <= 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    spread = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return Interval(low=max(0.0, centre - spread), high=min(1.0, centre + spread))


def median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _pairs(attempts: Sequence[Attempt]) -> dict[str, list[Attempt]]:
    """Group attempts by task: the denominator is pairs, never attempts."""

    grouped: dict[str, list[Attempt]] = {}
    for attempt in attempts:
        grouped.setdefault(attempt.task_id, []).append(attempt)
    return grouped


def _is_accepted(runs: Sequence[Attempt]) -> bool:
    return any(run.human_verdict == "accept" for run in runs)


def _is_regression_free(runs: Sequence[Attempt]) -> bool:
    return any(
        run.human_verdict == "accept" and run.heldout_pass is True and run.policy_pass is True
        for run in runs
    )


def _is_automated_pass(runs: Sequence[Attempt]) -> bool:
    return any(run.automated_pass is True for run in runs)


def compute_metrics(attempts: Sequence[Attempt], *, agent: str) -> MetricsReport:
    """Compute every §3.2 metric for one agent."""

    grouped = _pairs(attempts)
    finished_pairs = {
        task_id: runs for task_id, runs in grouped.items() if any(run.finished for run in runs)
    }
    pairs = len(finished_pairs)
    accepted_runs = [run for runs in finished_pairs.values() for run in runs if _is_accepted(runs)]
    accepted_pairs = sum(1 for runs in finished_pairs.values() if _is_accepted(runs))
    regression_free = sum(1 for runs in finished_pairs.values() if _is_regression_free(runs))
    automated = sum(1 for runs in finished_pairs.values() if _is_automated_pass(runs))

    all_runs = [run for runs in finished_pairs.values() for run in runs]
    tokens_total = sum(run.tokens_in + run.tokens_out for run in all_runs)
    cost_total = sum((run.cost_list_usd for run in all_runs), Decimal("0"))

    declared = [run for run in all_runs if run.declared_success is not None]
    declared_positive = [run for run in declared if run.declared_success is True]
    false_completions = [
        run for run in declared_positive if not _is_accepted(finished_pairs.get(run.task_id, []))
    ]

    accepted_durations = [run.duration_s for run in accepted_runs if run.duration_s is not None]
    exclusions: dict[str, int] = {}
    for attempt in attempts:
        if attempt.status in {"infra_failed", "cancelled"}:
            exclusions[attempt.status] = exclusions.get(attempt.status, 0) + 1

    return MetricsReport(
        agent=agent,
        pairs=pairs,
        accepted=accepted_pairs,
        regression_free_accepted=regression_free,
        automated_pass_pairs=automated,
        declared_success_runs=len(declared_positive),
        tokens_total=tokens_total,
        cost_total=cost_total,
        human_acceptance_rate=accepted_pairs / pairs if pairs else 0.0,
        human_acceptance_ci=wilson_interval(accepted_pairs, pairs),
        regression_free_acceptance_rate=regression_free / pairs if pairs else 0.0,
        regression_free_acceptance_ci=wilson_interval(regression_free, pairs),
        task_success_rate=automated / pairs if pairs else 0.0,
        tokens_per_accepted_fix=(
            (Decimal(tokens_total) / Decimal(accepted_pairs)).quantize(Decimal("0.01"))
            if accepted_pairs
            else None
        ),
        # A zero denominator is not zero cost: the report must show null.
        cost_per_accepted_fix=(
            (cost_total / Decimal(accepted_pairs)).quantize(Decimal("0.0001"))
            if accepted_pairs
            else None
        ),
        median_time_to_repair_s=median(accepted_durations),
        false_completion_rate=(
            len(false_completions) / len(declared_positive) if declared_positive else None
        ),
        missing_heldout_pairs=sum(
            1
            for runs in finished_pairs.values()
            if any(run.heldout_pass is None for run in runs)
            and not any(run.heldout_pass is True for run in runs)
        ),
        exclusions=exclusions,
    )


def as_payload(
    report: MetricsReport, *, cost_basis: str, pricing_version: str
) -> dict[str, object]:
    """The JSONB payload of a ``metric_snapshots`` row."""

    return {
        "agent": report.agent,
        "cost_basis": cost_basis,
        "pricing_version": pricing_version,
        "denominators": {
            "pairs": report.pairs,
            "accepted": report.accepted,
            "regression_free_accepted": report.regression_free_accepted,
            "automated_pass_pairs": report.automated_pass_pairs,
            "declared_success_runs": report.declared_success_runs,
            "missing_heldout_pairs": report.missing_heldout_pairs,
        },
        "exclusions": report.exclusions,
        "metrics": {
            "human_acceptance_rate": report.human_acceptance_rate,
            "human_acceptance_ci": (
                None
                if report.human_acceptance_ci is None
                else {
                    "low": report.human_acceptance_ci.low,
                    "high": report.human_acceptance_ci.high,
                }
            ),
            "regression_free_acceptance_rate": report.regression_free_acceptance_rate,
            "regression_free_acceptance_ci": (
                None
                if report.regression_free_acceptance_ci is None
                else {
                    "low": report.regression_free_acceptance_ci.low,
                    "high": report.regression_free_acceptance_ci.high,
                }
            ),
            "task_success_rate": report.task_success_rate,
            "tokens_per_accepted_fix": (
                None
                if report.tokens_per_accepted_fix is None
                else str(report.tokens_per_accepted_fix)
            ),
            "cost_per_accepted_fix": (
                None if report.cost_per_accepted_fix is None else str(report.cost_per_accepted_fix)
            ),
            "median_time_to_repair_s": report.median_time_to_repair_s,
            "false_completion_rate": report.false_completion_rate,
            "tokens_total": report.tokens_total,
            "cost_total": str(report.cost_total),
        },
    }
