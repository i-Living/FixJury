"""The static metrics page (PLAN §11, task 7.3).

One self-contained HTML file built from ``metric_snapshots``: the headline table,
the denominators, and the charts embedded as base64 images, so serving it is
nginx pointing at a directory and refreshing it is re-running the command.

Nothing here recomputes a metric — the page renders the payload the snapshot
already carries.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from html import escape
from pathlib import Path

CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; background: #f6f6f4; color: #1d1d1b;
       font: 15px/1.55 -apple-system, "Segoe UI", Roboto, Helvetica, sans-serif; }
main { max-width: 72rem; margin: 0 auto; padding: 2.5rem 1.25rem 4rem; }
header h1 { font-size: 1.6rem; margin: 0 0 .35rem; letter-spacing: -.01em; }
header p { color: #6b6b66; margin: 0 0 2rem; }
h2 { font-size: .8rem; text-transform: uppercase; letter-spacing: .06em;
     color: #6b6b66; margin: 2.25rem 0 .6rem; font-weight: 600; }
table { width: 100%; border-collapse: collapse; background: #fff; font-size: 13.5px; }
th, td { text-align: left; padding: .55rem .7rem; border-bottom: 1px solid #e8e8e3; }
th { font-size: .72rem; text-transform: uppercase; letter-spacing: .05em; color: #6b6b66; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tr:last-child td { border-bottom: 0; }
.null { color: #9a9a92; }
.charts { display: grid; grid-template-columns: repeat(auto-fit, minmax(20rem, 1fr)); gap: 1rem; }
.charts figure { margin: 0; background: #fff; border: 1px solid #e8e8e3; padding: .75rem; }
.charts img { width: 100%; height: auto; display: block; }
.notes { margin-top: 2.5rem; color: #6b6b66; font-size: 12.5px; }
.notes li { margin-bottom: .3rem; }
code { background: #f0f0ec; padding: .05rem .3rem; border-radius: 2px; }
"""


def _metrics(payload: Mapping[str, object]) -> Mapping[str, object]:
    block = payload.get("metrics")
    return block if isinstance(block, Mapping) else {}


def _numbers(payload: Mapping[str, object]) -> Mapping[str, object]:
    block = payload.get("denominators")
    return block if isinstance(block, Mapping) else {}


def _percent(value: object) -> str:
    if isinstance(value, int | float):
        return f"{value * 100:.1f}%"
    return '<span class="null">null</span>'


def _plain(value: object) -> str:
    if value is None:
        return '<span class="null">null</span>'
    if isinstance(value, int | float):
        return f"{value:,.2f}"
    return escape(str(value))


def _interval(payload: Mapping[str, object], key: str) -> str:
    block = _metrics(payload).get(key)
    if not isinstance(block, Mapping):
        return '<span class="null">null</span>'
    low, high = block.get("low"), block.get("high")
    if not isinstance(low, int | float) or not isinstance(high, int | float):
        return '<span class="null">null</span>'
    return f"{low * 100:.1f}-{high * 100:.1f}%"


def _embed(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def render_dashboard(
    payloads: Sequence[Mapping[str, object]],
    charts: Mapping[str, Path],
    *,
    title: str = "FixJury — agent repair benchmark",
    basis: str = "list_price",
) -> str:
    """The whole page as one string."""

    if not payloads:
        raise ValueError("a dashboard needs at least one snapshot")
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    head = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{escape(title)}</title>",
        f"<style>{CSS}</style>",
        "</head><body><main>",
        "<header>",
        f"<h1>{escape(title)}</h1>",
        (
            f"<p>Frozen corpus, one row per agent, metric snapshots generated {generated}. "
            f"Cost basis: <strong>{escape(basis)}</strong>.</p>"
        ),
        "</header>",
    ]

    headline = [
        "<h2>Acceptance and success</h2>",
        "<table><thead><tr>",
        '<th>Agent</th><th class="num">Acceptance</th><th class="num">95% CI</th>',
        '<th class="num">Regression-free</th><th class="num">Task success</th>',
        '<th class="num">Accepted / pairs</th><th class="num">Tokens per fix</th>',
        '<th class="num">Cost per fix</th><th class="num">Median repair</th>',
        '<th class="num">False completion</th>',
        "</tr></thead><tbody>",
    ]
    for payload in payloads:
        metrics = _metrics(payload)
        numbers = _numbers(payload)
        headline.append(
            "<tr>"
            f"<td>{escape(str(payload.get('agent', '?')))}</td>"
            f'<td class="num">{_percent(metrics.get("human_acceptance_rate"))}</td>'
            f'<td class="num">{_interval(payload, "human_acceptance_ci")}</td>'
            f'<td class="num">{_percent(metrics.get("regression_free_acceptance_rate"))}</td>'
            f'<td class="num">{_percent(metrics.get("task_success_rate"))}</td>'
            f'<td class="num">{_plain(numbers.get("accepted"))} / '
            f"{_plain(numbers.get('pairs'))}</td>"
            f'<td class="num">{_plain(metrics.get("tokens_per_accepted_fix"))}</td>'
            f'<td class="num">{_plain(metrics.get("cost_per_accepted_fix"))}</td>'
            f'<td class="num">{_plain(metrics.get("median_time_to_repair_s"))}</td>'
            f'<td class="num">{_percent(metrics.get("false_completion_rate"))}</td>'
            "</tr>"
        )
    headline.append("</tbody></table>")

    figures = ['<h2>Charts</h2><div class="charts">']
    captions = {
        "acceptance": "Human acceptance with its interval",
        "regression_free": "Regression-free acceptance",
        "regressions": "Where the corpus ends up",
        "cost": "Cost per accepted fix",
    }
    for name, path in charts.items():
        if not path.exists():
            continue
        figures.append(
            f'<figure><img alt="{escape(captions.get(name, name))}" src="{_embed(path)}"></figure>'
        )
    figures.append("</div>")

    details = [
        "<h2>Denominators and exclusions</h2>",
        "<table><thead><tr>",
        '<th>Agent</th><th class="num">Pairs</th><th class="num">Accepted</th>',
        '<th class="num">Regression-free</th><th class="num">Automated pass</th>',
        '<th class="num">Missing held-out</th><th>Excluded</th><th>Pricing version</th>',
        "</tr></thead><tbody>",
    ]
    for payload in payloads:
        numbers = _numbers(payload)
        exclusions = payload.get("exclusions")
        excluded = (
            ", ".join(f"{escape(str(key))}={value}" for key, value in exclusions.items())
            if isinstance(exclusions, Mapping) and exclusions
            else "none"
        )
        details.append(
            "<tr>"
            f"<td>{escape(str(payload.get('agent', '?')))}</td>"
            f'<td class="num">{_plain(numbers.get("pairs"))}</td>'
            f'<td class="num">{_plain(numbers.get("accepted"))}</td>'
            f'<td class="num">{_plain(numbers.get("regression_free_accepted"))}</td>'
            f'<td class="num">{_plain(numbers.get("automated_pass_pairs"))}</td>'
            f'<td class="num">{_plain(numbers.get("missing_heldout_pairs"))}</td>'
            f"<td>{excluded}</td>"
            f"<td><code>{escape(str(payload.get('pricing_version', '?')))}</code></td>"
            "</tr>"
        )
    details.append("</tbody></table>")

    notes = [
        '<div class="notes"><ul>',
        "<li>Denominators are pairs (task, agent), not attempts: an agent that retries "
        "must not look better than one that does not.</li>",
        "<li><code>null</code> means the denominator was zero: a zero denominator "
        "is not zero cost.</li>",
        "<li>A missing held-out result is a data defect, not a pass: those pairs leave the "
        "regression-free rate and are counted above.</li>",
        "<li>Costs computed under different <code>pricing_version</code> values are "
        "not comparable.</li>",
        "</ul></div>",
    ]

    return "\n".join([*head, *headline, *figures, *details, *notes, "</main></body></html>"])


def write_dashboard(
    payloads: Sequence[Mapping[str, object]],
    out_dir: Path,
    *,
    title: str = "FixJury — agent repair benchmark",
    basis: str = "list_price",
    charts: Mapping[str, Path] | None = None,
) -> Path:
    """Write ``index.html`` (and leave the chart PNGs beside it)."""

    out_dir.mkdir(parents=True, exist_ok=True)
    html = render_dashboard(payloads, charts or {}, title=title, basis=basis)
    target = out_dir / "index.html"
    target.write_text(html, encoding="utf-8")
    return target
