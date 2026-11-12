"""Metric computation and snapshots (PLAN §7)."""

from __future__ import annotations

from fixjury.metrics.compute import (
    Attempt,
    Interval,
    MetricsReport,
    as_payload,
    compute_metrics,
    median,
    wilson_interval,
)
from fixjury.metrics.cost import (
    ModelPrice,
    Pricing,
    PricingError,
    load_pricing,
    parse_pricing,
)
from fixjury.metrics.service import (
    MetricsError,
    attempts_from_rows,
    compute_snapshot,
    config_hash_of,
    total_cost,
)

__all__ = [
    "Attempt",
    "Interval",
    "MetricsError",
    "MetricsReport",
    "ModelPrice",
    "Pricing",
    "PricingError",
    "as_payload",
    "attempts_from_rows",
    "compute_metrics",
    "compute_snapshot",
    "config_hash_of",
    "load_pricing",
    "median",
    "parse_pricing",
    "total_cost",
    "wilson_interval",
]
