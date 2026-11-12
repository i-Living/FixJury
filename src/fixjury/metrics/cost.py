"""List-price cost (PLAN §3.2.1, invariant 8).

Two bases exist and both are labelled:

* ``list_price`` — tokens times pinned price plus sandbox seconds times pinned rate. A
  reference number, not money that was spent;
* ``amortized`` — a share of the flat subscription, computed in the snapshot.

This module only knows ``list_price``. Everything it returns carries the
``pricing_version`` it was computed with, because costs from different versions
are not comparable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

LOGGER = logging.getLogger(__name__)
UNPRICED = "unpriced"
MICRO = Decimal("1000000")
SECONDS_PER_HOUR = Decimal("3600")


class PricingError(RuntimeError):
    """The pricing file cannot be used."""


@dataclass(frozen=True)
class ModelPrice:
    input_per_mtok: Decimal = Decimal("0")
    output_per_mtok: Decimal = Decimal("0")


@dataclass(frozen=True)
class Pricing:
    version: str
    currency: str
    models: dict[str, ModelPrice]
    sandbox_per_hour: Decimal = Decimal("0")

    def price_for(self, model: str) -> ModelPrice:
        """A model without a price is priced at zero, and that is logged."""

        if model in self.models:
            return self.models[model]
        if "default" in self.models:
            return self.models["default"]
        return ModelPrice()

    def list_price(
        self, *, model: str, tokens_in: int, tokens_out: int, sandbox_seconds: Decimal
    ) -> Decimal:
        """Tokens and sandbox time at the pinned rates, rounded to 4 decimals."""

        price = self.price_for(model)
        token_cost = (
            Decimal(tokens_in) * price.input_per_mtok + Decimal(tokens_out) * price.output_per_mtok
        ) / MICRO
        sandbox_cost = Decimal(sandbox_seconds) / SECONDS_PER_HOUR * self.sandbox_per_hour
        return (token_cost + sandbox_cost).quantize(Decimal("0.0001"))


def _decimal(value: object, *, what: str) -> Decimal:
    if isinstance(value, int | float | str) and not isinstance(value, bool):
        try:
            return Decimal(str(value))
        except ArithmeticError as exc:
            raise PricingError(f"{what} is not a number: {value!r}") from exc
    raise PricingError(f"{what} is not a number: {value!r}")


def parse_pricing(payload: object) -> Pricing:
    """Read a pricing mapping. Missing rates are zero, a missing version is not."""

    if not isinstance(payload, dict):
        raise PricingError("pricing file must contain a mapping")
    version = payload.get("version")
    if not isinstance(version, str) or not version:
        raise PricingError("pricing file needs a version")
    currency = payload.get("currency", "USD")
    if not isinstance(currency, str):
        raise PricingError("currency must be a string")

    models: dict[str, ModelPrice] = {}
    raw_models = payload.get("models") or {}
    if not isinstance(raw_models, dict):
        raise PricingError("models must be a mapping")
    for name, rates in raw_models.items():
        if not isinstance(rates, dict):
            raise PricingError(f"model {name} must be a mapping")
        models[str(name)] = ModelPrice(
            input_per_mtok=_decimal(rates.get("input_per_mtok", 0), what=f"{name}.input_per_mtok"),
            output_per_mtok=_decimal(
                rates.get("output_per_mtok", 0), what=f"{name}.output_per_mtok"
            ),
        )

    sandbox = payload.get("sandbox") or {}
    if not isinstance(sandbox, dict):
        raise PricingError("sandbox must be a mapping")
    return Pricing(
        version=version,
        currency=currency,
        models=models,
        sandbox_per_hour=_decimal(sandbox.get("per_hour", 0), what="sandbox.per_hour"),
    )


def load_pricing(path: Path) -> Pricing:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PricingError(f"{path}: cannot read file: {exc}") from exc
    except yaml.YAMLError as exc:
        raise PricingError(f"{path}: invalid YAML: {exc}") from exc
    return parse_pricing(raw)
