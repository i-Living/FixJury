"""Charts for the report and the dashboard (PLAN §11, task 7.2).

Three pictures, each answering one question the tables answer in numbers:
acceptance with its interval, what a fix costs, and how much of the corpus the
agent got wrong. A missing value is drawn as a gap with a label, never as a zero
bar — a bar at zero says "this cost nothing", which is a different claim.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt

INK = "#1d1d1b"
MUTED = "#8a8a83"
GOOD = "#3f7d52"
CAUTION = "#b7791f"
BAD = "#a4493c"


def _metrics(payload: Mapping[str, object]) -> Mapping[str, object]:
    block = payload.get("metrics")
    return block if isinstance(block, Mapping) else {}


def _numbers(payload: Mapping[str, object]) -> Mapping[str, object]:
    block = payload.get("denominators")
    return block if isinstance(block, Mapping) else {}


def _rate(payload: Mapping[str, object], key: str) -> float | None:
    value = _metrics(payload).get(key)
    return float(value) if isinstance(value, int | float) else None


def _interval(payload: Mapping[str, object], key: str) -> tuple[float, float] | None:
    block = _metrics(payload).get(key)
    if not isinstance(block, Mapping):
        return None
    low, high = block.get("low"), block.get("high")
    if not isinstance(low, int | float) or not isinstance(high, int | float):
        return None
    return float(low), float(high)


def _bar_chart(
    labels: Sequence[str],
    values: Sequence[float | None],
    *,
    title: str,
    ylabel: str,
    path: Path,
    intervals: Sequence[tuple[float, float] | None] | None = None,
    percent: bool = True,
    color: str = GOOD,
) -> Path:
    figure, axes = plt.subplots(figsize=(6.4, 3.4), dpi=160)
    positions = range(len(labels))
    heights = [value if value is not None else 0.0 for value in values]
    for position, height, value in zip(positions, heights, values, strict=True):
        axes.bar(
            position,
            height,
            color=color if value is not None else "none",
            edgecolor=MUTED,
            linewidth=0.8 if value is None else 0,
            width=0.55,
        )
    if intervals:
        errors = [
            (
                max(0.0, value - low),
                max(0.0, high - value),
            )
            if value is not None and interval is not None
            else None
            for value, interval in zip(values, intervals, strict=True)
            for low, high in [interval or (0.0, 0.0)]
        ]
        if any(error is not None for error in errors):
            axes.errorbar(
                list(positions),
                heights,
                yerr=[
                    [error[0] if error else 0.0 for error in errors],
                    [error[1] if error else 0.0 for error in errors],
                ],
                fmt="none",
                ecolor=INK,
                elinewidth=1.0,
                capsize=4,
            )
    per_position = list(intervals) if intervals else [None] * len(labels)
    for position, height, value, interval in zip(
        positions, heights, values, per_position, strict=True
    ):
        label = "n/a" if value is None else (f"{value * 100:.0f}%" if percent else f"{value:.2f}")
        # Above the interval cap, or the label collides with the error bar.
        top = max(height, interval[1]) if interval is not None else height
        axes.annotate(
            label,
            (position, top),
            textcoords="offset points",
            xytext=(0, 4),
            ha="center",
            fontsize=8,
            color=INK if value is not None else MUTED,
        )
    axes.set_title(title, fontsize=10, color=INK, loc="left")
    axes.set_ylabel(ylabel, fontsize=8, color=MUTED)
    axes.set_xticks(list(positions))
    axes.set_xticklabels(labels, fontsize=8, color=INK)
    if percent:
        axes.set_ylim(0, 1.08)
        axes.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
        axes.set_yticklabels(["0", "25", "50", "75", "100%"], fontsize=7, color=MUTED)
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axes.spines[side].set_color("#d8d8d2")
    axes.tick_params(length=0)
    axes.grid(axis="y", color="#ececE6", linewidth=0.8)
    axes.set_axisbelow(True)
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, format="png", facecolor="#ffffff")
    plt.close(figure)
    return path


def render_charts(payloads: Sequence[Mapping[str, object]], out_dir: Path) -> dict[str, Path]:
    """Write the three charts and return their paths by name."""

    labels = [str(payload.get("agent", "?")) for payload in payloads]
    acceptance = [_rate(payload, "human_acceptance_rate") for payload in payloads]
    intervals = [_interval(payload, "human_acceptance_ci") for payload in payloads]
    regression_free = [_rate(payload, "regression_free_acceptance_rate") for payload in payloads]
    cost = [_rate(payload, "cost_per_accepted_fix") for payload in payloads]

    charts = {
        "acceptance": _bar_chart(
            labels,
            acceptance,
            title="Human acceptance, 95% Wilson interval",
            ylabel="share of pairs",
            path=out_dir / "acceptance.png",
            intervals=intervals,
            color=GOOD,
        ),
        "regression_free": _bar_chart(
            labels,
            regression_free,
            title="Regression-free acceptance",
            ylabel="share of pairs",
            path=out_dir / "regression-free.png",
            color=GOOD,
        ),
        "cost": _bar_chart(
            labels,
            cost,
            title="Cost per accepted fix (list price)",
            ylabel="USD",
            path=out_dir / "cost.png",
            percent=False,
            color=CAUTION,
        ),
    }
    return charts


def render_regression_split(payloads: Sequence[Mapping[str, object]], out_dir: Path) -> Path:
    """Stacked bars: accepted and regression-free, accepted but not, not accepted."""

    labels = [str(payload.get("agent", "?")) for payload in payloads]
    top: list[float] = []
    middle: list[float] = []
    bottom: list[float] = []
    for payload in payloads:
        numbers = _numbers(payload)
        pairs = numbers.get("pairs")
        accepted = numbers.get("accepted")
        clean = numbers.get("regression_free_accepted")
        total = float(pairs) if isinstance(pairs, int | float) and pairs else 0.0
        accepted_count = float(accepted) if isinstance(accepted, int | float) else 0.0
        clean_count = float(clean) if isinstance(clean, int | float) else 0.0
        if total <= 0:
            top.append(0.0)
            middle.append(0.0)
            bottom.append(0.0)
            continue
        top.append(clean_count / total)
        middle.append(max(0.0, (accepted_count - clean_count) / total))
        bottom.append(max(0.0, (total - accepted_count) / total))

    figure, axes = plt.subplots(figsize=(6.4, 3.4), dpi=160)
    positions = list(range(len(labels)))
    axes.bar(positions, top, color=GOOD, width=0.55, label="accepted, regression-free")
    axes.bar(positions, middle, bottom=top, color=CAUTION, width=0.55, label="accepted, regression")
    axes.bar(
        positions,
        bottom,
        bottom=[a + b for a, b in zip(top, middle, strict=True)],
        color=BAD,
        width=0.55,
        label="not accepted",
    )
    # Labels carry the meaning too: colour alone is not an accessible encoding.
    for position, (clean, regressed, missed) in enumerate(zip(top, middle, bottom, strict=True)):
        cursor = 0.0
        for share, ink in ((clean, "#ffffff"), (regressed, "#ffffff"), (missed, "#ffffff")):
            if share >= 0.08:
                axes.annotate(
                    f"{share * 100:.0f}%",
                    (position, cursor + share / 2),
                    ha="center",
                    va="center",
                    fontsize=8,
                    color=ink,
                )
            cursor += share
    axes.set_title("Where the corpus ends up", fontsize=10, color=INK, loc="left")
    axes.set_ylabel("share of pairs", fontsize=8, color=MUTED)
    axes.set_ylim(0, 1.08)
    axes.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    axes.set_yticklabels(["0", "25", "50", "75", "100%"], fontsize=7, color=MUTED)
    axes.set_xticks(positions)
    axes.set_xticklabels(labels, fontsize=8, color=INK)
    axes.tick_params(length=0)
    axes.legend(fontsize=7, frameon=False, loc="upper center", ncol=3, bbox_to_anchor=(0.5, -0.12))
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axes.spines[side].set_color("#d8d8d2")
    figure.tight_layout()
    path = out_dir / "regressions.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, format="png", facecolor="#ffffff")
    plt.close(figure)
    return path
