"""Gate and analytics rendering (PLAN_V1.1 §5.1, §5.2).

Both reports print numbers somebody else computed: a gate report prints rule outcomes over a
stored evaluation, an analytics report prints numbers a snapshot already published. Neither
divides anything here.
"""

from __future__ import annotations

from decimal import Decimal

from fixjury.evaluation.gates import GateReport
from fixjury.metrics.analytics import AnalyticsReport


def render_gate_report(report: GateReport) -> str:
    """The gate outcome, then every rule with the evidence behind it."""

    lines = [
        f"# Quality gates — {report.outcome.value}",
        "",
        f"- Policy: **{report.policy.name}** version **{report.policy.version}**.",
        f"- Evaluation: `{report.evaluation_run_id}`.",
        "",
        "| Rule | Outcome | Confirmed | Detail |",
        "| --- | --- | --- | --- |",
    ]
    for item in report.results:
        confirmed = "yes" if item.confirmed else "no"
        detail = item.detail.replace("|", "\\|")
        lines.append(f"| {item.rule} | {item.outcome.value} | {confirmed} | {detail} |")
    lines += [
        "",
        "A confirmed failure comes from a check that ran and failed; an unconfirmed one is an "
        "opinion and is reported for review, never as proof. `INCONCLUSIVE` means the policy "
        "could not be evaluated, which is not a pass (SPEC §7.3).",
        "",
    ]
    return "\n".join(lines)


def _percent(value: float | None) -> str:
    return "null" if value is None else f"{value * 100:.1f}%"


def _money(value: Decimal | None) -> str:
    return "null" if value is None else f"{value:,.4f}"


def _number(value: float | None, *, decimals: int = 2) -> str:
    return "null" if value is None else f"{value:,.{decimals}f}"


def render_analytics(report: AnalyticsReport) -> str:
    """Agent standing, then history, then the totals the window allows."""

    agent_note = f"; agent **{report.agent_filter}**" if report.agent_filter else ""
    window = (
        f"{report.since or 'the first snapshot'} to {report.until or 'now'}"
        if report.since or report.until
        else "every stored snapshot"
    )
    lines = [
        "# Analytics",
        "",
        f"- Window: **{window}**{agent_note}.",
        f"- Sources: {len(report.sources)} snapshot(s).",
        "",
        "## Agent standing",
        "",
        "| Agent | Source | Pairs | Decided | Acceptance | Regression-free | Tokens "
        "| Cost (basis / version) | Cost per accepted fix | Median repair | Judge evaluations "
        "| Judge cost (list price) | False acceptance | False rejection | Brier |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",  # noqa: E501
    ]
    for agent in report.agents:
        pricing = f"{agent.cost_basis} / {agent.pricing_version or 'unknown'}"
        lines.append(
            f"| {agent.agent} | {agent.verdict_source} | {agent.pairs} | {agent.decided} "
            f"| {_percent(agent.acceptance_rate)} | {_percent(agent.regression_free_rate)} "
            f"| {agent.tokens_total} "
            f"| {_money(agent.cost_total)} ({pricing}) "
            f"| {_money(agent.cost_per_accepted_fix)} "
            f"| {_number(agent.median_time_to_repair_s)} s "
            f"| {agent.evaluations} | {_money(agent.judge_cost_total)} "
            f"| {_percent(agent.false_acceptance_rate)} "
            f"| {_percent(agent.false_rejection_rate)} | {_number(agent.brier, decimals=4)} |"
        )
    lines += [
        "",
        "## History",
        "",
        "| Snapshot | Agent | Computed | Source | Acceptance | Regression-free "
        "| Accepted/decided |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for entry in report.history:
        lines.append(
            f"| `{entry.snapshot_id}` | {entry.agent} | {entry.computed_at:%Y-%m-%d %H:%M} "
            f"| {entry.verdict_source} | {_percent(entry.acceptance_rate)} "
            f"| {_percent(entry.regression_free_rate)} | {entry.accepted}/{entry.decided} |"
        )
    if report.totals is not None:
        totals = report.totals
        lines += [
            "",
            "## Totals",
            "",
            f"- Accepted: {totals['accepted']} of {totals['decided']} decided pair(s).",
            f"- Agent tokens: {totals['tokens_total']}; agent cost: {totals['cost_total']} "
            f"({totals['cost_basis']}, {totals['pricing_version'] or 'unknown'}).",
            f"- Judge tokens: {totals['judge_tokens_total']}; judge cost: "
            f"{totals['judge_cost_total']} (list price).",
        ]
    if report.notes:
        lines += ["", "## How to read this", ""]
        lines += [f"- {note}" for note in report.notes]
    lines.append("")
    return "\n".join(lines)


__all__ = ["render_analytics", "render_gate_report"]
