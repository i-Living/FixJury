#!/usr/bin/env python3
"""Write a fixture export so the metrics page can be built without a database.

The page is prerendered from the export `fixjury dashboard` writes, so a bare
`npm run build` needs one. This script writes that export with the same shape,
through the same function, so CI can check the page and its Python contract
without Postgres and snapshots:

    uv run python scripts/dashboard_fixture.py /tmp/snapshots.json
    (cd web && DASHBOARD_DATA=/tmp/snapshots.json npm run build)

The figures are a fixture, never a measurement: one agent carries numbers, the
other carries `null` wherever a denominator is zero, so the build also exercises
the null path. `generated_at` says so on the page; never publish this output.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fixjury.report.dashboard import write_dashboard_data

PAYLOADS: list[dict[str, object]] = [
    {
        "agent": "fixture-agent",
        "cost_basis": "list_price",
        "pricing_version": "fixture",
        "denominators": {
            "pairs": 4,
            "accepted": 2,
            "regression_free_accepted": 1,
            "automated_pass_pairs": 3,
            "declared_success_runs": 3,
            "missing_heldout_pairs": 0,
        },
        "exclusions": {"infra_failed": 1},
        "metrics": {
            "human_acceptance_rate": 0.5,
            "human_acceptance_ci": {"low": 0.15, "high": 0.85},
            "regression_free_acceptance_rate": 0.25,
            "regression_free_acceptance_ci": None,
            "task_success_rate": 0.75,
            "tokens_per_accepted_fix": "1500.00",
            "cost_per_accepted_fix": "0.5000",
            "median_time_to_repair_s": 61.5,
            "false_completion_rate": 0.25,
            "tokens_total": 3000,
            "cost_total": "1.0000",
        },
    },
    {
        "agent": "fixture-null-agent",
        "cost_basis": "list_price",
        "pricing_version": "fixture",
        "denominators": {
            "pairs": 0,
            "accepted": 0,
            "regression_free_accepted": 0,
            "automated_pass_pairs": 0,
            "declared_success_runs": 0,
            "missing_heldout_pairs": 0,
        },
        "exclusions": {},
        "metrics": {
            "human_acceptance_rate": None,
            "human_acceptance_ci": None,
            "regression_free_acceptance_rate": None,
            "regression_free_acceptance_ci": None,
            "task_success_rate": None,
            "tokens_per_accepted_fix": None,
            "cost_per_accepted_fix": None,
            "median_time_to_repair_s": None,
            "false_completion_rate": None,
            "tokens_total": 0,
            "cost_total": "0.0000",
        },
    },
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Where to write the export.")
    args = parser.parse_args(argv)

    write_dashboard_data(
        PAYLOADS,
        args.path,
        title="FixJury — build fixture",
        generated_at="fixture, not a measurement",
    )
    print(f"wrote {args.path} for {len(PAYLOADS)} fixture agents")
    return 0


if __name__ == "__main__":
    sys.exit(main())
