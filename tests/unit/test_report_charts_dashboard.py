from __future__ import annotations

from pathlib import Path

import pytest

from fixjury.report.charts import render_charts, render_regression_split
from fixjury.report.dashboard import render_dashboard, write_dashboard

DENOMINATORS: dict[str, object] = {
    "pairs": 10,
    "accepted": 4,
    "regression_free_accepted": 3,
    "automated_pass_pairs": 6,
    "declared_success_runs": 8,
    "missing_heldout_pairs": 1,
}

METRICS: dict[str, object] = {
    "human_acceptance_rate": 0.4,
    "human_acceptance_ci": {"low": 0.168, "high": 0.687},
    "regression_free_acceptance_rate": 0.3,
    "regression_free_acceptance_ci": {"low": 0.108, "high": 0.603},
    "task_success_rate": 0.6,
    "tokens_per_accepted_fix": "1250.00",
    "cost_per_accepted_fix": "0.4000",
    "median_time_to_repair_s": 42.5,
    "false_completion_rate": 0.5,
    "tokens_total": 5000,
    "cost_total": "1.6000",
}

PAYLOAD: dict[str, object] = {
    "agent": "baseline",
    "cost_basis": "list_price",
    "pricing_version": "unpriced-2026-11",
    "denominators": DENOMINATORS,
    "exclusions": {"infra_failed": 2},
    "metrics": METRICS,
}


def test_every_chart_is_written(tmp_path: Path) -> None:
    charts = render_charts([PAYLOAD], tmp_path)
    charts["regressions"] = render_regression_split([PAYLOAD], tmp_path)

    assert set(charts) == {"acceptance", "regression_free", "cost", "regressions"}
    for path in charts.values():
        assert path.exists()
        assert path.stat().st_size > 1000, "a real PNG, not an empty file"
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_null_cost_is_drawn_as_a_gap_not_as_zero(tmp_path: Path) -> None:
    payload = dict(PAYLOAD)
    payload["metrics"] = {**METRICS, "cost_per_accepted_fix": None}

    charts = render_charts([payload], tmp_path)

    assert charts["cost"].exists()


def test_two_agents_fit_on_one_chart(tmp_path: Path) -> None:
    other = {**PAYLOAD, "agent": "swe-agent"}

    charts = render_charts([PAYLOAD, other], tmp_path)

    assert charts["acceptance"].exists()


def test_the_page_carries_the_numbers_and_the_charts(tmp_path: Path) -> None:
    charts = render_charts([PAYLOAD], tmp_path)
    charts["regressions"] = render_regression_split([PAYLOAD], tmp_path)

    html = render_dashboard([PAYLOAD], charts)

    assert "baseline" in html
    assert "40.0%" in html
    assert "16.8-68.7%" in html
    assert "0.4000" in html
    assert html.count("data:image/png;base64,") == 4
    assert "unpriced-2026-11" in html
    assert "infra_failed=2" in html


def test_the_page_says_null_when_a_denominator_was_zero(tmp_path: Path) -> None:
    payload = dict(PAYLOAD)
    payload["denominators"] = {**DENOMINATORS, "accepted": 0}
    payload["metrics"] = {**METRICS, "cost_per_accepted_fix": None}

    html = render_dashboard([payload], {})

    assert '<span class="null">null</span>' in html


def test_the_page_is_self_contained(tmp_path: Path) -> None:
    charts = render_charts([PAYLOAD], tmp_path)

    html = render_dashboard([PAYLOAD], charts)

    assert "<script" not in html
    assert "http://" not in html and "https://" not in html


def test_an_empty_page_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one snapshot"):
        render_dashboard([], {})


def test_writing_the_page_creates_the_directory(tmp_path: Path) -> None:
    target = write_dashboard([PAYLOAD], tmp_path / "site")

    assert target.exists()
    assert target.name == "index.html"
    assert "baseline" in target.read_text(encoding="utf-8")


def test_a_hostile_agent_name_is_escaped(tmp_path: Path) -> None:
    payload = {**PAYLOAD, "agent": "<script>alert(1)</script>"}

    html = render_dashboard([payload], {})

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
