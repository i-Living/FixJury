from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from fixjury.config import settings
from fixjury.metrics.cost import PricingError, load_pricing, parse_pricing, pricing

REPO = Path(__file__).resolve().parents[2]

PAYLOAD = {
    "version": "2026-11-01",
    "currency": "USD",
    "models": {
        "cheap": {"input_per_mtok": 0.5, "output_per_mtok": 1.5},
        "default": {"input_per_mtok": 1, "output_per_mtok": 2},
    },
    "sandbox": {"per_hour": 0.12},
}


def test_a_million_input_tokens_cost_the_pinned_rate() -> None:
    pricing = parse_pricing(PAYLOAD)

    cost = pricing.list_price(
        model="cheap", tokens_in=1_000_000, tokens_out=0, sandbox_seconds=Decimal("0")
    )

    assert cost == Decimal("0.5000")


def test_output_tokens_use_the_output_rate() -> None:
    pricing = parse_pricing(PAYLOAD)

    cost = pricing.list_price(
        model="cheap", tokens_in=0, tokens_out=2_000_000, sandbox_seconds=Decimal("0")
    )

    assert cost == Decimal("3.0000")


def test_sandbox_seconds_are_billed_by_the_hour() -> None:
    pricing = parse_pricing(PAYLOAD)

    cost = pricing.list_price(
        model="cheap", tokens_in=0, tokens_out=0, sandbox_seconds=Decimal("3600")
    )

    assert cost == Decimal("0.1200")


def test_tokens_and_seconds_add_up() -> None:
    pricing = parse_pricing(PAYLOAD)

    cost = pricing.list_price(
        model="cheap", tokens_in=1_000_000, tokens_out=1_000_000, sandbox_seconds=Decimal("1800")
    )

    assert cost == Decimal("2.0600")


def test_an_unpriced_model_falls_back_to_default() -> None:
    pricing = parse_pricing(PAYLOAD)

    cost = pricing.list_price(
        model="mystery", tokens_in=1_000_000, tokens_out=0, sandbox_seconds=Decimal("0")
    )

    assert cost == Decimal("1.0000")


def test_without_a_default_an_unpriced_model_costs_nothing() -> None:
    pricing = parse_pricing({"version": "v", "models": {}})

    assert pricing.list_price(
        model="mystery", tokens_in=1_000_000, tokens_out=1_000_000, sandbox_seconds=Decimal("60")
    ) == Decimal("0.0000")


def test_a_missing_version_is_refused() -> None:
    with pytest.raises(PricingError, match="version"):
        parse_pricing({"models": {}})


def test_a_non_numeric_rate_is_refused() -> None:
    with pytest.raises(PricingError, match="input_per_mtok"):
        parse_pricing({"version": "v", "models": {"m": {"input_per_mtok": "free"}}})


def test_a_queued_run_and_a_finished_run_agree_on_the_version() -> None:
    """The queue stamps the version before the run exists, so both must read one file."""

    assert pricing().version == load_pricing(REPO / "config" / "pricing.yaml").version


def test_an_unusable_pricing_file_says_so_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    broken = tmp_path / "pricing.yaml"
    broken.write_text("version: [unclosed\n", encoding="utf-8")
    monkeypatch.setattr(settings, "pricing_file", str(broken))

    fallback = pricing()

    assert fallback.version == "unpriced"
    assert fallback.models == {}


def test_the_shipped_pricing_file_pins_a_real_rate() -> None:
    """Zero rates were a placeholder; a published cost needs rates that came from somewhere."""

    pricing = load_pricing(REPO / "config" / "pricing.yaml")

    assert not pricing.version.startswith("unpriced")
    price = pricing.price_for("deepseek-v4.1-flash")
    assert price.input_per_mtok > 0
    assert price.output_per_mtok > 0


def test_a_missing_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(PricingError, match="cannot read"):
        load_pricing(tmp_path / "absent.yaml")
