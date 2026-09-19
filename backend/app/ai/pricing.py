"""Configurable model-pricing registry and cost *estimation*.

Pricing is DATA, never hard-coded into business logic. The defaults below are
public list-price *estimates* (USD per 1,000,000 tokens) with an effective date;
they must be verified and can be overridden via the ``AI_PRICING_OVERRIDES_JSON``
setting. When a model's price is unknown, cost estimation returns ``None`` — the
platform never invents a number and never presents an estimate as a real invoice.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("ai.pricing")


@dataclass(frozen=True)
class ModelPrice:
    input_per_million: Decimal
    output_per_million: Decimal
    currency: str = "USD"
    effective_date: str = "2025-01-01"
    source: str = "public list-price estimate — verify before billing"


def _d(value: str | float) -> Decimal:
    return Decimal(str(value))


# Keyed by "provider:model". These are ESTIMATES for cost visibility only.
_DEFAULT_PRICING: dict[str, ModelPrice] = {
    # Anthropic (per 1M tokens)
    "anthropic:claude-sonnet-4-5": ModelPrice(_d(3.00), _d(15.00)),
    "anthropic:claude-opus-4-1": ModelPrice(_d(15.00), _d(75.00)),
    "anthropic:claude-3-5-haiku": ModelPrice(_d(0.80), _d(4.00)),
    # OpenAI (per 1M tokens)
    "openai:gpt-4o": ModelPrice(_d(2.50), _d(10.00)),
    "openai:gpt-4o-mini": ModelPrice(_d(0.15), _d(0.60)),
    # OpenAI embeddings (output price 0; input only)
    "openai:text-embedding-3-small": ModelPrice(_d(0.02), _d(0.0)),
    "openai:text-embedding-3-large": ModelPrice(_d(0.13), _d(0.0)),
    # Local/test provider — free.
    "mock:mock-1": ModelPrice(_d(0.0), _d(0.0)),
    "mock:mock-embed-1": ModelPrice(_d(0.0), _d(0.0)),
}


def _load_overrides() -> dict[str, ModelPrice]:
    raw = settings.AI_PRICING_OVERRIDES_JSON
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("ai_pricing_overrides_invalid_json")
        return {}
    overrides: dict[str, ModelPrice] = {}
    for key, entry in data.items():
        try:
            overrides[key] = ModelPrice(
                input_per_million=_d(entry["input_per_million"]),
                output_per_million=_d(entry.get("output_per_million", 0)),
                currency=entry.get("currency", "USD"),
                effective_date=entry.get("effective_date", "override"),
                source=entry.get("source", "configured override"),
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("ai_pricing_override_entry_invalid", key=key)
    return overrides


class PricingRegistry:
    def __init__(self) -> None:
        self._prices: dict[str, ModelPrice] = {**_DEFAULT_PRICING, **_load_overrides()}

    def get(self, provider: str, model: str) -> ModelPrice | None:
        return self._prices.get(f"{provider}:{model}")

    def estimate_cost(
        self, provider: str, model: str, input_tokens: int, output_tokens: int
    ) -> tuple[Decimal | None, str]:
        """Return (estimated_cost, currency). Cost is None when price is unknown."""
        price = self.get(provider, model)
        if price is None:
            return None, "USD"
        cost = (
            _d(input_tokens) / _d(1_000_000) * price.input_per_million
            + _d(output_tokens) / _d(1_000_000) * price.output_per_million
        )
        # Round to 6 decimal places (sub-cent precision for token-level costs).
        return cost.quantize(Decimal("0.000001")), price.currency


# Module-level singleton.
pricing_registry = PricingRegistry()
