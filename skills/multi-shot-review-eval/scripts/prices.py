from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from eval_errors import EvalError


STALE_AFTER = timedelta(days=30)
MILLION = 1_000_000
TOTAL_TOKENS_ASSUMPTION = (
    "Codex logs report only a total token count; USD prices that total at the model input rate, "
    "so it overstates cached input and understates output."
)


@dataclass(frozen=True)
class ModelPrice:
    input: float
    cached_input: float
    output: float
    cache_write: float | None = None


@dataclass(frozen=True)
class PriceTable:
    updated: date
    models: dict[str, ModelPrice]

    def total_tokens_usd(self, model: str | None, tokens: int) -> float | None:
        price = self.models.get(model) if model else None
        return None if price is None else tokens * price.input / MILLION

    def is_stale(self, today: date) -> bool:
        return today - self.updated > STALE_AFTER


def load_price_table(path: Path) -> PriceTable:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, ValueError) as exc:
        raise EvalError(f"could not read price table {path}: {exc}") from exc
    updated = data.get("updated")
    if not isinstance(updated, date):
        raise EvalError(f"price table needs an `updated` date: {path}")
    models = data.get("models")
    if not isinstance(models, dict):
        raise EvalError(f"price table needs a [models] table: {path}")
    return PriceTable(updated=updated, models={name: _model_price(name, value) for name, value in models.items()})


def _model_price(name: str, value: Any) -> ModelPrice:
    try:
        return ModelPrice(
            input=_rate(name, value["input"]),
            cached_input=_rate(name, value["cached_input"]),
            output=_rate(name, value["output"]),
            cache_write=_rate(name, value["cache_write"]) if "cache_write" in value else None,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise EvalError(f"price for {name} needs numeric input, cached_input and output") from exc


def _rate(name: str, raw: Any) -> float:
    rate = float(raw)
    if not math.isfinite(rate) or rate < 0:
        raise EvalError(f"price for {name} must be a finite nonnegative rate: {raw}")
    return rate
