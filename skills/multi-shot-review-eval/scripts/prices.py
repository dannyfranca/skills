from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import sys
import tempfile
import tomllib
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_errors import EvalError


STALE_AFTER = timedelta(days=30)
MILLION = 1_000_000
TOTAL_TOKENS_ASSUMPTION = (
    "Codex logs report only a total token count; USD prices that total at the model input rate, "
    "so it overstates cached input and understates output."
)


def default_cache_path() -> Path:
    """Prices change faster than the skill, so they live in a machine cache that research refreshes."""

    root = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(root) / "multi-shot-review-eval" / "prices.toml"


@dataclass(frozen=True)
class ModelPrice:
    input: float
    cached_input: float
    output: float
    source: str
    fetched_at: date
    cache_write: float | None = None

    def is_stale(self, today: date) -> bool:
        return today - self.fetched_at > STALE_AFTER

    def record(self) -> dict[str, Any]:
        rates = {"input": self.input, "cached_input": self.cached_input, "output": self.output}
        if self.cache_write is not None:
            rates["cache_write"] = self.cache_write
        return {**rates, "source": self.source, "fetched_at": self.fetched_at.isoformat()}


@dataclass(frozen=True)
class PriceTable:
    path: Path
    models: dict[str, ModelPrice]

    def total_tokens_usd(self, model: str | None, tokens: int) -> float | None:
        price = self.models.get(model) if model else None
        return None if price is None else tokens * price.input / MILLION

    def stale_models(self, models: Iterable[str], today: date) -> list[str]:
        return sorted(model for model in set(models) if model in self.models and self.models[model].is_stale(today))

    def with_price(self, model: str, price: ModelPrice) -> PriceTable:
        return replace(self, models={**self.models, model: price})


def load_price_table(path: Path) -> PriceTable:
    if not path.exists():
        return PriceTable(path=path, models={})
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, ValueError) as exc:
        raise EvalError(f"could not read price cache {path}: {exc}") from exc
    models = data.get("models", {})
    if not isinstance(models, dict):
        raise EvalError(f"price cache needs a [models] table: {path}")
    return PriceTable(path=path, models={name: _model_price(name, value) for name, value in models.items()})


def write_price_table(table: PriceTable) -> None:
    lines = ["# USD per 1M tokens. Written by prices.py set; each model keeps its own source and fetch date.\n"]
    for name in sorted(table.models):
        lines.append(f"\n[models.{json.dumps(name)}]\n")
        lines += [f"{key} = {json.dumps(value)}\n" for key, value in table.models[name].record().items() if key != "fetched_at"]
        lines.append(f"fetched_at = {table.models[name].fetched_at.isoformat()}\n")
    table.path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=table.path.parent, prefix=table.path.name, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write("".join(lines))
        except OSError:
            temporary.unlink()
            raise
    try:
        temporary.replace(table.path)
    except OSError:
        temporary.unlink()
        raise


def _model_price(name: str, value: Any) -> ModelPrice:
    if not isinstance(value, dict):
        raise EvalError(f"price for {name} must be a table")
    source, fetched_at = value.get("source"), value.get("fetched_at")
    if not isinstance(source, str) or not source.strip():
        raise EvalError(f"price for {name} needs a source")
    if not isinstance(fetched_at, date) or isinstance(fetched_at, datetime):
        raise EvalError(f"price for {name} needs a fetched_at date")
    try:
        return ModelPrice(
            input=_rate(name, value["input"]),
            cached_input=_rate(name, value["cached_input"]),
            output=_rate(name, value["output"]),
            source=source.strip(),
            fetched_at=fetched_at,
            cache_write=_rate(name, value["cache_write"]) if "cache_write" in value else None,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise EvalError(f"price for {name} needs numeric input, cached_input and output") from exc


def _rate(name: str, raw: Any) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise EvalError(f"price for {name} must be a number: {raw!r}")
    try:
        rate = float(raw)
    except OverflowError as exc:
        raise EvalError(f"price for {name} is too large: {raw}") from exc
    if not math.isfinite(rate) or rate < 0:
        raise EvalError(f"price for {name} must be a finite nonnegative rate: {raw}")
    return rate


def main(argv: list[str] | None = None, *, today: date | None = None) -> int:
    args = _parser().parse_args(argv)
    path = Path(args.cache).expanduser() if args.cache else default_cache_path()
    try:
        price = _model_price(
            args.model,
            {
                "input": args.input,
                "cached_input": args.cached_input,
                "output": args.output,
                "source": args.source,
                "fetched_at": today or date.today(),
                **({"cache_write": args.cache_write} if args.cache_write is not None else {}),
            },
        )
        with _locked(path):
            write_price_table(load_price_table(path).with_price(args.model, price))
    except OSError as error:
        print(f"error: could not write price cache {path}: {error}", file=sys.stderr)
        return 2
    except EvalError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"cache": str(path), "model": args.model, **price.record()}))
    return 0


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Parallel research agents can each set a model; the lock keeps every read-modify-write whole."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write one researched model price to the machine price cache.")
    parser.add_argument("--cache", help=f"price cache file; default: {default_cache_path()}")
    commands = parser.add_subparsers(dest="command", required=True)
    put = commands.add_parser("set", help="add or refresh one model; fetched_at becomes today")
    put.add_argument("model", help="model name exactly as the review logs report it")
    put.add_argument("--input", type=float, required=True, help="USD per 1M input tokens")
    put.add_argument("--cached-input", type=float, required=True, help="USD per 1M cached input tokens")
    put.add_argument("--output", type=float, required=True, help="USD per 1M output tokens")
    put.add_argument("--cache-write", type=float, help="USD per 1M cache write tokens, when the provider lists it")
    put.add_argument("--source", required=True, help="URL of the page that gives the price")
    return parser


if __name__ == "__main__":
    sys.exit(main())
