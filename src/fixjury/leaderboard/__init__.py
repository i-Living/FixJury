"""The leaderboard: rankings read from stored snapshots (PLAN_V1.1 §3.5).

Nothing here computes a metric. A snapshot already carries an agent's numbers and its
grouped numbers, so this module orders them, says how much evidence each row rests on, and
names the revision it ranks: the corpus version, the snapshot's ``config_hash``, the
verdict source and the judge configuration that produced the verdicts.

Two rules keep a ranking honest:

- a row built on too few pairs is shown and not ranked. A rate over four pairs has a 95%
  interval wider than forty points, and putting a number on it turns noise into an order.
- a null stays null. An agent with nothing accepted has no cost per accepted fix, and
  sorting it as zero would make "never accepted" look like "cheap".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

#: Below this many decided pairs a row is published but not ranked (SPEC §6.4 asks for the
#: sample size next to every entry; this is where the sample stops carrying an order).
MIN_RANKED_SAMPLE = 5

DIMENSIONS: tuple[str, ...] = ("overall", "difficulty")
"""What the frozen corpus can rank by. Language and task category are not columns on
``tasks``, and decision 2 keeps ``task.yaml`` frozen, so the report says so instead of
inventing a grouping the data cannot support."""

UNRECORDED_DIMENSIONS: tuple[str, ...] = ("language", "category")


class LeaderboardError(RuntimeError):
    """There is nothing to rank, or the snapshots do not agree on what they measured."""


@dataclass(frozen=True)
class Entry:
    """One agent's place in one group, with the evidence behind it."""

    agent: str
    group: str
    pairs: int
    decided: int
    accepted: int
    acceptance_rate: float | None
    acceptance_ci: tuple[float, float] | None
    regression_free_rate: float | None
    cost_per_accepted_fix: str | None
    tokens_per_accepted_fix: str | None
    median_time_to_repair_s: float | None
    rank: int | None
    ranked: bool

    @property
    def sample_note(self) -> str:
        return f"{self.decided} decided pair(s)"


@dataclass(frozen=True)
class Leaderboard:
    """The ranking of one corpus revision under one evaluation configuration."""

    corpus_version: str
    config_hashes: tuple[str, ...]
    verdict_source: str
    judge_config_hashes: tuple[str, ...]
    dimension: str
    entries: tuple[Entry, ...]
    min_ranked_sample: int = MIN_RANKED_SAMPLE
    notes: tuple[str, ...] = field(default_factory=tuple)

    def by_group(self) -> dict[str, tuple[Entry, ...]]:
        grouped: dict[str, list[Entry]] = {}
        for entry in self.entries:
            grouped.setdefault(entry.group, []).append(entry)
        return {group: tuple(rows) for group, rows in grouped.items()}


def _number(block: Mapping[str, object], key: str, *, legacy: str | None = None) -> float | None:
    metrics = block.get("metrics")
    if not isinstance(metrics, Mapping):
        return None
    value = metrics.get(key, metrics.get(legacy) if legacy else None)
    return float(value) if isinstance(value, int | float) else None


def _decimal(block: Mapping[str, object], key: str, *, legacy: str | None = None) -> str | None:
    metrics = block.get("metrics")
    if not isinstance(metrics, Mapping):
        return None
    value = metrics.get(key, metrics.get(legacy) if legacy else None)
    return None if value is None else str(value)


def _interval(block: Mapping[str, object]) -> tuple[float, float] | None:
    metrics = block.get("metrics")
    if not isinstance(metrics, Mapping):
        return None
    value = metrics.get("acceptance_ci", metrics.get("human_acceptance_ci"))
    if not isinstance(value, Mapping):
        return None
    low, high = value.get("low"), value.get("high")
    if not isinstance(low, int | float) or not isinstance(high, int | float):
        return None
    return float(low), float(high)


def _count(block: Mapping[str, object], key: str, *, legacy: str | None = None) -> int:
    denominators = block.get("denominators")
    if not isinstance(denominators, Mapping):
        return 0
    value = denominators.get(key, denominators.get(legacy) if legacy else None)
    return value if isinstance(value, int) else 0


def _entries_for(payload: Mapping[str, object], dimension: str) -> list[Entry]:
    """One agent's rows for one dimension, taken from the numbers the snapshot stores."""

    agent = str(payload.get("agent", "?"))
    blocks: list[tuple[str, Mapping[str, object]]] = []
    if dimension == "overall":
        blocks.append(("all tasks", payload))
    else:
        groups = payload.get("groups")
        chosen = groups.get(dimension) if isinstance(groups, Mapping) else None
        if isinstance(chosen, Mapping):
            blocks.extend(
                (str(label), block) for label, block in chosen.items() if isinstance(block, Mapping)
            )
    entries: list[Entry] = []
    for group, block in blocks:
        entries.append(
            Entry(
                agent=agent,
                group=group,
                pairs=_count(block, "pairs"),
                decided=_count(block, "decided_pairs", legacy="reviewed_pairs"),
                accepted=_count(block, "accepted"),
                acceptance_rate=_number(block, "acceptance_rate", legacy="human_acceptance_rate"),
                acceptance_ci=_interval(block),
                regression_free_rate=_number(block, "regression_free_acceptance_rate"),
                cost_per_accepted_fix=_decimal(block, "cost_per_accepted_fix"),
                tokens_per_accepted_fix=_decimal(block, "tokens_per_accepted_fix"),
                median_time_to_repair_s=_number(block, "median_time_to_repair_s"),
                rank=None,
                ranked=False,
            )
        )
    return entries


def _order_key(entry: Entry) -> tuple[float, float, float, str]:
    """Regression-free first, then cheap: an unsupported rate sorts below a measured one."""

    rate = entry.regression_free_rate
    acceptance = entry.acceptance_rate
    cost = entry.cost_per_accepted_fix
    return (
        -(rate if rate is not None else -1.0),
        -(acceptance if acceptance is not None else -1.0),
        float(cost) if cost is not None else float("inf"),
        entry.agent,
    )


def build_leaderboard(
    payloads: Sequence[Mapping[str, object]],
    *,
    corpus_version: str,
    dimension: str = "overall",
    min_ranked_sample: int = MIN_RANKED_SAMPLE,
) -> Leaderboard:
    """Order the snapshots into a ranking, one group at a time."""

    if not payloads:
        raise LeaderboardError("a leaderboard needs at least one snapshot")
    if dimension not in DIMENSIONS:
        allowed = ", ".join(DIMENSIONS)
        raise LeaderboardError(
            f"the frozen corpus cannot rank by {dimension!r}; it records {allowed}"
        )

    sources = sorted({str(payload.get("verdict_source", "human")) for payload in payloads})
    if len(sources) > 1:
        raise LeaderboardError(
            "these snapshots were measured from different verdict sources "
            f"({', '.join(sources)}): a ranking needs one"
        )
    config_hashes: set[str] = set()
    judge_hashes: set[str] = set()
    entries: list[Entry] = []
    for payload in payloads:
        for entry in _entries_for(payload, dimension):
            entries.append(entry)
        config_hash = payload.get("config_hash")
        if isinstance(config_hash, str):
            config_hashes.add(config_hash)
        judge = payload.get("judge")
        if isinstance(judge, Mapping):
            judge_hashes.update(str(item) for item in judge.get("judge_config_hashes", []) if item)

    ranked: list[Entry] = []
    grouped: dict[str, list[Entry]] = {}
    for entry in entries:
        grouped.setdefault(entry.group, []).append(entry)
    for rows in grouped.values():
        rank = 0
        for row in sorted(rows, key=_order_key):
            # A row under the sample floor is listed at its sorted place and given no rank:
            # the order is honest, the number would not be.
            if row.decided < min_ranked_sample or row.regression_free_rate is None:
                ranked.append(replace(row, rank=None, ranked=False))
                continue
            rank += 1
            ranked.append(replace(row, rank=rank, ranked=True))

    notes: list[str] = []
    if dimension == "overall":
        notes.append(
            f"Rows with fewer than {min_ranked_sample} decided pairs are listed without a rank."
        )
    if not judge_hashes:
        notes.append("No judge configuration is recorded in these snapshots.")
    notes.append(
        "Language and task category are not recorded in the frozen corpus, so no ranking "
        "is published for them."
    )
    return Leaderboard(
        corpus_version=corpus_version,
        config_hashes=tuple(sorted(config_hashes)),
        verdict_source=sources[0],
        judge_config_hashes=tuple(sorted(judge_hashes)),
        dimension=dimension,
        entries=tuple(ranked),
        min_ranked_sample=min_ranked_sample,
        notes=tuple(notes),
    )


def _percent(value: float | None) -> str:
    return "null" if value is None else f"{value * 100:.1f}%"


def _ci(entry: Entry) -> str:
    if entry.acceptance_ci is None:
        return "null"
    low, high = entry.acceptance_ci
    return f"{low * 100:.1f}-{high * 100:.1f}%"


def _rank(entry: Entry) -> str:
    return str(entry.rank) if entry.rank is not None else "—"


def _money(value: str | None) -> str:
    return "null" if value is None else f"{float(value):,.4f}"


def _tokens(value: str | None) -> str:
    return "null" if value is None else f"{float(value):,.2f}"


def _seconds(value: float | None) -> str:
    return "null" if value is None else f"{value:.2f} s"


def render_markdown(board: Leaderboard) -> str:
    """The leaderboard as a document, with everything a reader needs to distrust it."""

    config_hashes = ", ".join(f"`{item}`" for item in board.config_hashes) or "unknown"
    judge_hashes = ", ".join(f"`{item}`" for item in board.judge_config_hashes) or "none recorded"
    lines = [
        f"# FixJury leaderboard — {board.dimension}",
        "",
        f"- Corpus: **{board.corpus_version}** (frozen; task files are never edited in place).",
        f"- Verdict source: **{board.verdict_source}**.",
        f"- Run configuration hash: {config_hashes}.",
        f"- Judge configuration: {judge_hashes}.",
        f"- Ranked from at least **{board.min_ranked_sample}** decided pairs.",
        "",
    ]
    for group, rows in board.by_group().items():
        lines += [
            f"## {group}",
            "",
            "| Rank | Agent | Pairs | Decided | Acceptance | 95% CI | Regression-free "
            "| Tokens per accepted fix | Cost per accepted fix | Median repair |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for row in rows:
            lines.append(
                f"| {_rank(row)} | {row.agent} | {row.pairs} | {row.decided} "
                f"| {_percent(row.acceptance_rate)} | {_ci(row)} "
                f"| {_percent(row.regression_free_rate)} "
                f"| {_tokens(row.tokens_per_accepted_fix)} "
                f"| {_money(row.cost_per_accepted_fix)} "
                f"| {_seconds(row.median_time_to_repair_s)} |"
            )
        lines.append("")
    lines.append("## How to read this")
    lines.append("")
    lines += [f"- {note}" for note in board.notes]
    lines += [
        "- A dash in the rank column is not a last place: it is a row the sample cannot "
        "support ranking yet.",
        "- `null` is a zero denominator, not a zero rate: an agent with nothing accepted has "
        "no cost per accepted fix.",
        "",
    ]
    return "\n".join(lines)


def as_payload(board: Leaderboard) -> dict[str, object]:
    """The same ranking as JSON, for the API phase to serve without recomputing it."""

    return {
        "corpus_version": board.corpus_version,
        "verdict_source": board.verdict_source,
        "config_hashes": list(board.config_hashes),
        "judge_config_hashes": list(board.judge_config_hashes),
        "dimension": board.dimension,
        "min_ranked_sample": board.min_ranked_sample,
        "notes": list(board.notes),
        "entries": [
            {
                "agent": entry.agent,
                "group": entry.group,
                "rank": entry.rank,
                "ranked": entry.ranked,
                "pairs": entry.pairs,
                "decided_pairs": entry.decided,
                "accepted": entry.accepted,
                "acceptance_rate": entry.acceptance_rate,
                "acceptance_ci": (
                    None
                    if entry.acceptance_ci is None
                    else {"low": entry.acceptance_ci[0], "high": entry.acceptance_ci[1]}
                ),
                "regression_free_acceptance_rate": entry.regression_free_rate,
                "tokens_per_accepted_fix": entry.tokens_per_accepted_fix,
                "cost_per_accepted_fix": entry.cost_per_accepted_fix,
                "median_time_to_repair_s": entry.median_time_to_repair_s,
            }
            for entry in board.entries
        ],
    }


__all__ = [
    "DIMENSIONS",
    "MIN_RANKED_SAMPLE",
    "UNRECORDED_DIMENSIONS",
    "Entry",
    "Leaderboard",
    "LeaderboardError",
    "as_payload",
    "build_leaderboard",
    "render_markdown",
]
