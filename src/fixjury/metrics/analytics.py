"""Analytics over stored snapshots (PLAN_V1.1 §5.2).

An analytics view is a *filtered read of published numbers*, not a second measurement. The
snapshots were computed once by :mod:`fixjury.metrics.compute`; this module selects them by
agent and by time window, sums what is additive (tokens, cost, evaluations) and leaves every
rate exactly as the snapshot published it. Nothing here re-reads ``runs`` or ``verdicts``.

Two consequences worth stating in the output:

- a total across agents is computed only when the underlying snapshots share a cost basis
  and pricing version. Two versions answer two questions, and adding them produces a third
  number nobody measured.
- a rate is never averaged across agents. An agent's acceptance is a pair-count over its own
  denominator; the mean of two agents' rates is not the rate of the pair of them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


class AnalyticsError(RuntimeError):
    """The view cannot be built: no snapshots, or snapshots that cannot be added together."""


@dataclass(frozen=True)
class SnapshotRef:
    """One stored snapshot, identified so the view can name its sources."""

    snapshot_id: str
    agent: str
    computed_at: datetime
    verdict_source: str
    config_hash: str
    cost_basis: str
    pricing_version: str | None
    payload: Mapping[str, object]


@dataclass(frozen=True)
class AgentAnalytics:
    """One agent as the newest snapshot in the window describes it."""

    agent: str
    snapshot_id: str
    computed_at: datetime
    verdict_source: str
    #: The basis and pricing version this agent's money was computed under. Two agents can
    #: differ, and a cost column without them invites a comparison nobody measured.
    cost_basis: str
    pricing_version: str | None
    pairs: int
    decided: int
    accepted: int
    acceptance_rate: float | None
    regression_free_rate: float | None
    tokens_total: int
    cost_total: Decimal
    cost_per_accepted_fix: Decimal | None
    tokens_per_accepted_fix: Decimal | None
    median_time_to_repair_s: float | None
    evaluations: int
    escalated: int
    judge_tokens_total: int
    judge_cost_total: Decimal
    false_acceptance_rate: float | None
    false_rejection_rate: float | None
    brier: float | None


@dataclass(frozen=True)
class HistoryEntry:
    """One snapshot in the window, oldest first: the evaluation history of an agent."""

    agent: str
    snapshot_id: str
    computed_at: datetime
    acceptance_rate: float | None
    regression_free_rate: float | None
    accepted: int
    decided: int
    verdict_source: str


@dataclass(frozen=True)
class AnalyticsReport:
    agents: tuple[AgentAnalytics, ...]
    history: tuple[HistoryEntry, ...]
    since: date | None
    until: date | None
    agent_filter: str | None
    #: Sums across agents, present only when the snapshots are comparable.
    totals: Mapping[str, object] | None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(sorted({entry.snapshot_id for entry in self.history}))


def _metrics(payload: Mapping[str, object]) -> Mapping[str, object]:
    block = payload.get("metrics")
    return block if isinstance(block, Mapping) else {}


def _denominators(payload: Mapping[str, object]) -> Mapping[str, object]:
    block = payload.get("denominators")
    return block if isinstance(block, Mapping) else {}


def _number(block: Mapping[str, object], key: str, *, default: float = 0.0) -> float:
    value = block.get(key)
    return float(value) if isinstance(value, int | float) else default


def _decimal(block: Mapping[str, object], key: str) -> Decimal:
    value = block.get(key)
    try:
        return Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return Decimal("0")


def _optional_decimal(block: Mapping[str, object], key: str) -> Decimal | None:
    value = block.get(key)
    return None if value is None else _decimal(block, key)


def _rate(block: Mapping[str, object], key: str) -> float | None:
    value = block.get(key)
    return float(value) if isinstance(value, int | float) else None


def _acceptance(metrics: Mapping[str, object]) -> float | None:
    """A V1.1 snapshot calls it acceptance; a V1 snapshot called it human acceptance."""

    if "acceptance_rate" in metrics:
        return _rate(metrics, "acceptance_rate")
    return _rate(metrics, "human_acceptance_rate")


def _decided(denominators: Mapping[str, object]) -> int:
    """Decided pairs, under the V1.1 name or the V1 one."""

    if "decided_pairs" in denominators:
        return int(_number(denominators, "decided_pairs"))
    return int(_number(denominators, "reviewed_pairs"))


def _calibration_rate(payload: Mapping[str, object], key: str) -> float | None:
    block = payload.get("calibration")
    if not isinstance(block, Mapping):
        return None
    measured = block.get(key)
    if not isinstance(measured, Mapping):
        return None
    value = measured.get("rate")
    return float(value) if isinstance(value, int | float) else None


def _judge(payload: Mapping[str, object]) -> Mapping[str, object]:
    block = payload.get("judge")
    return block if isinstance(block, Mapping) else {}


def _agent_analytics(ref: SnapshotRef) -> AgentAnalytics:
    payload = ref.payload
    metrics = _metrics(payload)
    denominators = _denominators(payload)
    judge = _judge(payload)
    calibration = payload.get("calibration")
    brier = calibration.get("brier") if isinstance(calibration, Mapping) else None
    return AgentAnalytics(
        agent=ref.agent,
        snapshot_id=ref.snapshot_id,
        computed_at=ref.computed_at,
        verdict_source=ref.verdict_source,
        cost_basis=ref.cost_basis,
        pricing_version=ref.pricing_version,
        pairs=int(_number(denominators, "pairs")),
        decided=_decided(denominators),
        accepted=int(_number(denominators, "accepted")),
        acceptance_rate=_acceptance(metrics),
        regression_free_rate=_rate(metrics, "regression_free_acceptance_rate"),
        tokens_total=int(_number(metrics, "tokens_total")),
        cost_total=_decimal(metrics, "cost_total"),
        cost_per_accepted_fix=_optional_decimal(metrics, "cost_per_accepted_fix"),
        tokens_per_accepted_fix=_optional_decimal(metrics, "tokens_per_accepted_fix"),
        median_time_to_repair_s=_rate(metrics, "median_time_to_repair_s"),
        evaluations=int(_number(judge, "evaluations")),
        escalated=int(_number(judge, "escalated")),
        judge_tokens_total=int(_number(metrics, "judge_tokens_total")),
        judge_cost_total=_decimal(metrics, "judge_cost_total"),
        false_acceptance_rate=_calibration_rate(payload, "false_acceptance"),
        false_rejection_rate=_calibration_rate(payload, "false_rejection"),
        brier=float(brier) if isinstance(brier, int | float) else None,
    )


def build_analytics(
    snapshots: Sequence[SnapshotRef],
    *,
    agent: str | None = None,
    since: date | None = None,
    until: date | None = None,
) -> AnalyticsReport:
    """Select snapshots by agent and window, then describe them.

    The newest snapshot per agent is the agent's current standing; every snapshot in the
    window is history. A snapshot outside the window is not a gap in the data, it is outside
    the question that was asked.
    """

    if since is not None and until is not None and since > until:
        raise AnalyticsError(f"the window starts after it ends: {since} > {until}")

    def in_window(ref: SnapshotRef) -> bool:
        day = ref.computed_at.date()
        if since is not None and day < since:
            return False
        return not (until is not None and day > until)

    selected = [
        ref for ref in snapshots if (agent is None or ref.agent == agent) and in_window(ref)
    ]
    if not selected:
        raise AnalyticsError(
            "no snapshot matches this filter: compute metrics for the agent or widen the window"
        )

    ordered = sorted(selected, key=lambda ref: (ref.computed_at, ref.agent))
    newest: dict[str, SnapshotRef] = {}
    for ref in ordered:
        # Later rows overwrite: `ordered` ends with the newest snapshot per agent.
        newest[ref.agent] = ref
    agents = tuple(_agent_analytics(newest[name]) for name in sorted(newest))
    history = tuple(
        HistoryEntry(
            agent=ref.agent,
            snapshot_id=ref.snapshot_id,
            computed_at=ref.computed_at,
            acceptance_rate=_acceptance(_metrics(ref.payload)),
            regression_free_rate=_rate(_metrics(ref.payload), "regression_free_acceptance_rate"),
            accepted=int(_number(_denominators(ref.payload), "accepted")),
            decided=_decided(_denominators(ref.payload)),
            verdict_source=ref.verdict_source,
        )
        for ref in ordered
    )

    notes: list[str] = []
    totals: dict[str, object] | None = None
    bases = {(ref.cost_basis, ref.pricing_version or "") for ref in ordered}
    if len(bases) > 1:
        notes.append(
            "Totals are not published: these snapshots were priced under different cost "
            "bases or pricing versions, and adding them would produce a third number."
        )
    else:
        totals = {
            "accepted": sum(item.accepted for item in agents),
            "decided": sum(item.decided for item in agents),
            "tokens_total": sum(item.tokens_total for item in agents),
            "cost_total": str(sum((item.cost_total for item in agents), Decimal("0"))),
            "judge_tokens_total": sum(item.judge_tokens_total for item in agents),
            "judge_cost_total": str(sum((item.judge_cost_total for item in agents), Decimal("0"))),
            "cost_basis": ordered[0].cost_basis,
            "pricing_version": ordered[0].pricing_version,
        }
    if len(agents) > 1:
        notes.append(
            "Acceptance is never averaged across agents: each rate is a count over that "
            "agent's own decided pairs."
        )

    return AnalyticsReport(
        agents=agents,
        history=history,
        since=since,
        until=until,
        agent_filter=agent,
        totals=totals,
        notes=tuple(notes),
    )


__all__ = [
    "AgentAnalytics",
    "AnalyticsError",
    "AnalyticsReport",
    "HistoryEntry",
    "SnapshotRef",
    "build_analytics",
]
