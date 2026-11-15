"""Report rendering from stored snapshots (PLAN §11, task 7.2).

The report reads ``metric_snapshots`` and nothing else: every number here was
computed once in :mod:`fixjury.metrics`. Both cost bases are labelled in every
table that shows money, because a list price and a share of a subscription answer
different questions.

From V1.1 a snapshot also names its verdict source. A payload written before that key
existed is a human series, by definition, and is rendered as one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

BASIS_LABELS = {
    "list_price": "list price (tokens x pinned price + sandbox time; a reference, not an invoice)",
    "amortized": "amortized (share of the flat subscription; the money actually spent)",
}

#: What a snapshot says when its source predates the field: V1 measured the reviewer.
DEFAULT_SOURCE = "human (V1 snapshot)"


def _percent(value: object) -> str:
    # `null`, not a dash and not a zero: an undecided corpus has no rate, and a
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


def _metric(payload: Mapping[str, object], key: str, *, legacy: str | None = None) -> object:
    """A V1.1 key, or the name the same number had before the verdict source existed."""

    metrics = _metrics(payload)
    return metrics[key] if key in metrics else metrics.get(legacy or key)


def _denominator(payload: Mapping[str, object], key: str, *, legacy: str) -> object:
    denominators = _denominators(payload)
    return denominators[key] if key in denominators else denominators.get(legacy)


def source_of(payload: Mapping[str, object]) -> str:
    return str(payload.get("verdict_source", DEFAULT_SOURCE))


def render_agent_table(payload: Mapping[str, object]) -> str:
    """One row of the headline table, for one agent snapshot."""

    accepted = _count(_denominators(payload).get("accepted"))
    decided = _count(_denominator(payload, "decided_pairs", legacy="reviewed_pairs"))
    acceptance = _percent(_metric(payload, "acceptance_rate", legacy="human_acceptance_rate"))
    interval = _interval(_metric(payload, "acceptance_ci", legacy="human_acceptance_ci"))
    regression_free = _percent(
        _metric(
            payload,
            "regression_free_acceptance_rate",
        )
    )
    task_success = _percent(_metric(payload, "task_success_rate"))
    tokens = _number(_metric(payload, "tokens_per_accepted_fix"))
    cost = _number(_metric(payload, "cost_per_accepted_fix"))
    repair = _number(_metric(payload, "median_time_to_repair_s"), suffix=" s")
    false_completion = _percent(_metric(payload, "false_completion_rate"))
    return (
        f"| {payload.get('agent', '?')} "
        f"| {source_of(payload)} "
        f"| {acceptance} "
        f"| {interval} "
        f"| {regression_free} "
        f"| {task_success} "
        f"| {accepted}/{decided} "
        f"| {tokens} "
        f"| {cost} "
        f"| {repair} "
        f"| {false_completion} |"
    )


def _rate(block: object) -> str:
    """``count/of`` with its share, from a calibration rate block."""

    if not isinstance(block, dict):
        return "not measured"
    count, of = block.get("count"), block.get("of")
    rate = block.get("rate")
    return f"{_count(count)}/{_count(of)} ({_percent(rate)})"


def _calibration(payload: Mapping[str, object]) -> Mapping[str, object] | None:
    block = payload.get("calibration")
    return block if isinstance(block, dict) else None


def render_calibration_table(payloads: Sequence[Mapping[str, object]]) -> list[str]:
    """The judge's calibration, per agent, when a snapshot carries it."""

    section = [payload for payload in payloads if _calibration(payload) is not None]
    if not section:
        return []
    lines = [
        "",
        "## Judge calibration",
        "",
        "The reference label is the held-out suite, which no model produced. A pair the "
        "held-out suite could not answer has no label and is counted as unlabelled, never "
        "as a judge mistake.",
        "",
        "| Agent | Labelled / unlabelled | False acceptance | False rejection | "
        "Inter-judge agreement | Brier | Escalation | Within thresholds |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for payload in section:
        block = _calibration(payload) or {}
        lines.append(
            f"| {payload.get('agent', '?')} "
            f"| {_count(block.get('labeled_pairs'))} / {_count(block.get('unlabeled_pairs'))} "
            f"| {_rate(block.get('false_acceptance'))} "
            f"| {_rate(block.get('false_rejection'))} "
            f"| {_rate(block.get('agreement'))} "
            f"| {_number(block.get('brier'))} "
            f"| {_rate(block.get('escalation'))} "
            f"| {_verdict_word(block.get('meets_thresholds'))} |"
        )
    return lines


def _verdict_word(value: object) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "not measured"


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
        "| Agent | Verdict source | Acceptance | 95% CI | Regression-free | Task success "
        "| Accepted/decided | Tokens per accepted fix | Cost per accepted fix | Median repair "
        "| False completion |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines += [render_agent_table(payload) for payload in snapshots]
    lines += [
        "",
        "`null` means the denominator was zero: a zero denominator is not zero cost or zero "
        "acceptance.",
        "",
        "## Denominators and exclusions",
        "",
        "| Agent | Pairs | Decided | Accepted | Regression-free | Automated pass "
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
            f"| {_count(_denominator(payload, 'decided_pairs', legacy='reviewed_pairs'))} "
            f"| {_count(denominators.get('accepted'))} "
            f"| {_count(denominators.get('regression_free_accepted'))} "
            f"| {_count(denominators.get('automated_pass_pairs'))} "
            f"| {_count(denominators.get('declared_success_runs'))} "
            f"| {_count(denominators.get('no_patch_pairs'))} "
            f"| {_count(denominators.get('missing_heldout_pairs'))} "
            f"| {excluded} "
            f"| {payload.get('pricing_version', '?')} |"
        )
    lines += render_calibration_table(snapshots)
    lines += [
        "",
        "## How to read this",
        "",
        "- Denominators are **pairs (task, agent)**, not attempts: an agent that retries must not "
        "look better than one that does not.",
        "- Acceptance and false completion are computed over **decided** pairs only — pairs a "
        "verdict exists for, from the source named in the table. A pair nobody judged is "
        "unknown, not rejected.",
        "- A pair with no patch is a failed pair, not a missing one: it stays in the denominator "
        "and is counted above.",
        "- `heldout_pass = null` on a pair that did produce a patch is a data defect, not a pass: "
        "such pairs are excluded from the regression-free rate and counted above.",
        "- Judge tokens and cost are reported beside the agent's, never inside them: a cost per "
        "accepted fix that mixed the two would answer neither question.",
        "- Costs computed under different `pricing_version` values are not comparable.",
        "",
    ]
    return "\n".join(lines)
