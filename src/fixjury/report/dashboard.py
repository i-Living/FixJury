"""The metrics page: the data export and the build for the Next.js app in ``web/``.

Task 7.3 in the plan: a static, read-only page served by nginx. The page itself is a
Next.js static export, so this module owns only what Python must own — the numbers it
is allowed to draw. Nothing here recomputes a metric: the export carries the payload
``metric_snapshots`` already stores, plus the corpus split from
``fixjury.report.charts.corpus_split``, so the page and the PNG report cannot disagree.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from fixjury.report.charts import corpus_split

DEFAULT_TITLE = "FixJury — agent repair benchmark"


class DashboardError(RuntimeError):
    """The page could not be built; the message says what to fix."""


def default_web_dir() -> Path:
    """The Next.js app, ``web/`` beside ``src/`` in this repository."""

    return Path(__file__).resolve().parents[3] / "web"


def dashboard_data(
    payloads: Sequence[Mapping[str, object]],
    *,
    title: str = DEFAULT_TITLE,
    basis: str = "list_price",
    generated_at: str | None = None,
) -> dict[str, object]:
    """The JSON the page is built from: one record per agent, plus the corpus split."""

    if not payloads:
        raise ValueError("a dashboard needs at least one snapshot")
    stamp = generated_at or datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return {
        "title": title,
        "basis": basis,
        "generated_at": stamp,
        "agents": [
            {**payload, "split": corpus_split(payload)}
            for payload in sorted(payloads, key=lambda payload: str(payload.get("agent", "")))
        ],
    }


def write_dashboard_data(
    payloads: Sequence[Mapping[str, object]],
    path: Path,
    *,
    title: str = DEFAULT_TITLE,
    basis: str = "list_price",
    generated_at: str | None = None,
) -> Path:
    """Write the export the Next.js build reads."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            dashboard_data(payloads, title=title, basis=basis, generated_at=generated_at),
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def build_dashboard(
    out_dir: Path,
    payloads: Sequence[Mapping[str, object]],
    *,
    title: str = DEFAULT_TITLE,
    basis: str = "list_price",
    generated_at: str | None = None,
    web_dir: Path | None = None,
) -> Path:
    """Build the static export into ``out_dir`` and return the page it wrote."""

    web = Path(web_dir) if web_dir is not None else default_web_dir()
    if not (web / "package.json").is_file():
        raise DashboardError(f"no Next.js app at {web}: the page source lives in web/")
    npm = shutil.which("npm")
    if npm is None:
        raise DashboardError(
            "npm is not on PATH: the metrics page is a Next.js static export, "
            "install Node 20.9 or newer and `npm ci` in web/ first"
        )

    with tempfile.TemporaryDirectory() as workdir:
        data = write_dashboard_data(
            payloads,
            Path(workdir) / "snapshots.json",
            title=title,
            basis=basis,
            generated_at=generated_at,
        )
        result = subprocess.run(  # noqa: S603 - resolved npm, fixed argv, no shell
            [npm, "run", "build"],
            cwd=web,
            env={**os.environ, "DASHBOARD_DATA": str(data)},
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode != 0:
        output = (result.stderr or result.stdout).strip().splitlines()[-15:]
        raise DashboardError("the dashboard build failed:\n" + "\n".join(output))

    exported = web / "out"
    if not (exported / "index.html").is_file():
        raise DashboardError(f"the build wrote no index.html under {exported}")
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(exported, out_dir, dirs_exist_ok=True)
    return out_dir / "index.html"
