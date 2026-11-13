from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from fixjury.report.dashboard import (
    DashboardError,
    build_dashboard,
    dashboard_data,
    default_web_dir,
    write_dashboard_data,
)

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


def test_the_export_carries_one_record_per_agent_in_agent_order() -> None:
    data = dashboard_data(
        [PAYLOAD, {**PAYLOAD, "agent": "swe-agent"}, {**PAYLOAD, "agent": "aaa-agent"}],
        generated_at="2026-11-13 09:00 UTC",
    )

    assert data["agents"] == [
        {
            **PAYLOAD,
            "agent": "aaa-agent",
            "split": {"regression_free": 0.3, "with_regression": 0.1, "not_accepted": 0.6},
        },
        {**PAYLOAD, "split": {"regression_free": 0.3, "with_regression": 0.1, "not_accepted": 0.6}},
        {
            **PAYLOAD,
            "agent": "swe-agent",
            "split": {"regression_free": 0.3, "with_regression": 0.1, "not_accepted": 0.6},
        },
    ]
    assert data["generated_at"] == "2026-11-13 09:00 UTC"
    assert data["basis"] == "list_price"


def test_the_export_keeps_a_null_denominator_as_null() -> None:
    payload = {**PAYLOAD, "metrics": {**METRICS, "cost_per_accepted_fix": None}}

    data = dashboard_data([payload], generated_at="2026-11-13 09:00 UTC")

    assert data["agents"][0]["metrics"]["cost_per_accepted_fix"] is None  # type: ignore[index]


def test_an_empty_export_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one snapshot"):
        dashboard_data([])


def test_the_export_is_json_on_disk(tmp_path: Path) -> None:
    target = write_dashboard_data([PAYLOAD], tmp_path / "work" / "snapshots.json")

    written = json.loads(target.read_text(encoding="utf-8"))

    assert written["title"].startswith("FixJury")
    assert written["generated_at"].endswith("UTC")
    assert written["agents"][0]["agent"] == "baseline"


def test_the_page_source_is_web_beside_src() -> None:
    assert default_web_dir().name == "web"
    assert (default_web_dir() / "package.json").is_file(), "the Next.js app lives in web/"


def test_a_missing_npm_says_what_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    with pytest.raises(DashboardError, match="npm is not on PATH"):
        build_dashboard(Path("site"), [PAYLOAD])


def test_a_missing_page_source_is_named(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/npm")

    with pytest.raises(DashboardError, match=r"no Next\.js app"):
        build_dashboard(tmp_path / "site", [PAYLOAD], web_dir=tmp_path / "nowhere")


def test_the_build_hands_the_export_to_next_and_copies_the_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    web = tmp_path / "web"
    (web / "out").mkdir(parents=True)
    (web / "package.json").write_text("{}", encoding="utf-8")
    (web / "out" / "index.html").write_text("<html>page</html>", encoding="utf-8")
    seen: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs["env"]
        assert isinstance(env, dict)
        exported = Path(env["DASHBOARD_DATA"]).read_text(encoding="utf-8")
        seen["command"] = command
        seen["cwd"] = kwargs["cwd"]
        seen["data"] = json.loads(exported)
        return subprocess.CompletedProcess(command, 0, stdout="built", stderr="")

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/npm")
    monkeypatch.setattr(subprocess, "run", fake_run)

    target = build_dashboard(tmp_path / "site", [PAYLOAD], web_dir=web)

    assert target == tmp_path / "site" / "index.html"
    assert target.read_text(encoding="utf-8") == "<html>page</html>"
    assert seen["command"] == ["/usr/bin/npm", "run", "build"]
    assert seen["cwd"] == web
    assert seen["data"]["agents"][0]["agent"] == "baseline"  # type: ignore[index]


def test_a_failed_build_reports_the_tail_of_the_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    web = tmp_path / "web"
    web.mkdir()
    (web / "package.json").write_text("{}", encoding="utf-8")

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="Error: bad data\n")

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/npm")
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(DashboardError, match="bad data"):
        build_dashboard(tmp_path / "site", [PAYLOAD], web_dir=web)
