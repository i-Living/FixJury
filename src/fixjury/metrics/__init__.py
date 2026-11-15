"""Metric computation and snapshots (PLAN §7; calibration in PLAN_V1.1 §2.7)."""

from __future__ import annotations

from fixjury.metrics.calibration import (
    THRESHOLDS,
    CalibrationReport,
    CalibrationThresholds,
    Rate,
    as_calibration_payload,
    calibrate,
)
from fixjury.metrics.compute import (
    GROUP_KEYS,
    Attempt,
    Interval,
    MetricsReport,
    VerdictSource,
    as_payload,
    compute_metrics,
    grouped_payload,
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
    Judgement,
    MetricsError,
    attempts_from_rows,
    compute_snapshot,
    config_hash_of,
    judgements_for,
    total_cost,
)

__all__ = [
    "GROUP_KEYS",
    "THRESHOLDS",
    "Attempt",
    "CalibrationReport",
    "CalibrationThresholds",
    "Interval",
    "Judgement",
    "MetricsError",
    "MetricsReport",
    "ModelPrice",
    "Pricing",
    "PricingError",
    "Rate",
    "VerdictSource",
    "as_calibration_payload",
    "as_payload",
    "attempts_from_rows",
    "calibrate",
    "compute_metrics",
    "compute_snapshot",
    "config_hash_of",
    "grouped_payload",
    "judgements_for",
    "load_pricing",
    "median",
    "parse_pricing",
    "total_cost",
    "wilson_interval",
]
