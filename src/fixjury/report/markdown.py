"""Report rendering from stored snapshots (PLAN §11, task 7.2).

The report reads ``metric_snapshots`` and nothing else: every number here was
computed once in :mod:`fixjury.metrics`. Both cost bases are labelled in every
table that shows money, because a list price and a share of a subscription answer
different questions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

BASIS_LABELS = {
    "list_price": "list price (tokens x pinned price + sandbox time; a reference, not an invoice)",
    "amortized": "amortized (share of the flat subscription; the money actually spent)",
}


def _percent(value: object) -> str:
    # `null`, not a dash and not a zero: an unreviewed corpus has no rate, and a
    # reader must not mistake one for the other.
    if not isinstance(value, int | float):
        return "null"
    return f"{value * 100:.1f}%"


def _interval(block: object) -> str:
    if not isinstance(block, dict):
        return "null"
    low, high = block.get("low"), block.get("high")
    if not isinstance(low, int | float) or not isinstance(high, int | float):
        return "null"
    return f"{low * 100:.1f}-{high * 100:.1f}%"


def _count(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        return str(int(value))
    return str(value)


def _number(value: object, *, suffix: str = "") -> str:
    if value is None:
        return "null"
    if isinstance(value, int | float | Decimal):
        return f"{Decimal(str(value)):,.2f}{suffix}"
    return str(value)


def _metrics(payload: Mapping[str, object]) -> Mapping[str, object]:
    block = payload.get("metrics")
    return block if isinstance(block, dict) else {}


def _denominators(payload: Mapping[str, object]) -> Mapping[str, object]:
    block = payload.get("denominators")
    return block if isinstance(block, dict) else {}


def render_agent_table(payload: Mapping[str, object]) -> str:
    """One row of the headline table, for one agent snapshot."""

    metrics = _metrics(payload)
    denominators = _denominators(payload)
    accepted = _count(denominators.get("accepted"))
    return (
        f"| {payload.get('agent', '?')} "
        f"| {_percent(metrics.get('human_acceptance_rate'))} "
        f"| {_interval(metrics.get('human_acceptance_ci'))} "
        f"| {_percent(metrics.get('regression_free_acceptance_rate'))} "
        f"| {_percent(metrics.get('task_success_rate'))} "
        f"| {accepted}/{_count(denominators.get('reviewed_pairs'))} "
        f"| {_number(metrics.get('tokens_per_accepted_fix'))} "
        f"| {_number(metrics.get('cost_per_accepted_fix'))} "
        f"| {_number(metrics.get('median_time_to_repair_s'), suffix=' s')} "
        f"| {_percent(metrics.get('false_completion_rate'))} |"
    )


def render_report(
    snapshots: Sequence[Mapping[str, object]],
    *,
    title: str = "FixJury — benchmark report",
    basis: str = "list_price",
) -> str:
    """Render the markdown report from snapshot payloads."""

    if not snapshots:
        raise ValueError("a report needs at least one snapshot")
    basis_label = BASIS_LABELS.get(basis, basis)
    lines = [
        f"# {title}",
        "",
        f"Cost basis in every table below: **{basis_label}**.",
        "",
        "## Acceptance and success",
        "",
        "| Agent | Acceptance | 95% CI | Regression-free | Task success | Accepted/reviewed "
        "| Tokens per accepted fix | Cost per accepted fix | Median repair "
        "| False completion |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines += [render_agent_table(payload) for payload in snapshots]
    lines += [
        "",
        "`null` means the denominator was zero: a zero denominator is not zero cost or zero "
        "acceptance.",
        "",
        "## Denominators and exclusions",
        "",
        "| Agent | Pairs | Reviewed | Accepted | Regression-free | Automated pass "
        "| Declared success | No patch | Missing held-out | Excluded | Pricing version |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for payload in snapshots:
        denominators = _denominators(payload)
        exclusions = payload.get("exclusions")
        excluded = (
            ", ".join(f"{key}={value}" for key, value in exclusions.items())
            if isinstance(exclusions, dict) and exclusions
            else "none"
        )
        lines.append(
            f"| {payload.get('agent', '?')} "
            f"| {_count(denominators.get('pairs'))} "
            f"| {_count(denominators.get('reviewed_pairs'))} "
            f"| {_count(denominators.get('accepted'))} "
            f"| {_count(denominators.get('regression_free_accepted'))} "
            f"| {_count(denominators.get('automated_pass_pairs'))} "
            f"| {_count(denominators.get('declared_success_runs'))} "
            f"| {_count(denominators.get('no_patch_pairs'))} "
            f"| {_count(denominators.get('missing_heldout_pairs'))} "
            f"| {excluded} "
            f"| {payload.get('pricing_version', '?')} |"
        )
    lines += [
        "",
        "## How to read this",
        "",
        "- Denominators are **pairs (task, agent)**, not attempts: an agent that retries must not "
        "look better than one that does not.",
        "- Acceptance and false completion are computed over **reviewed** pairs only; a pair no "
        "reviewer has seen is unknown, not rejected.",
        "- A pair with no patch is a failed pair, not a missing one: it stays in the denominator "
        "and is counted above.",
        "- `heldout_pass = null` on a pair that did produce a patch is a data defect, not a pass: "
        "such pairs are excluded from the regression-free rate and counted above.",
        "- Costs computed under different `pricing_version` values are not comparable.",
        "",
    ]
    return "\n".join(lines)
