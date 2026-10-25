from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import StrEnum

from diff_size import DiffSize
from eval_errors import EvalError
from metric_values import session_metric_values
from session_store import Session
from usage import SessionUsage


class Direction(StrEnum):
    HIGHER = "higher"
    LOWER = "lower"
    NEUTRAL = "neutral"


class MarginKind(StrEnum):
    RELATIVE = "relative"
    ABSOLUTE = "absolute"


@dataclass(frozen=True)
class Margin:
    kind: MarginKind
    value: float

    def absolute(self, baseline: float) -> float:
        return self.value * abs(baseline) if self.kind == MarginKind.RELATIVE else self.value


@dataclass(frozen=True)
class Ratio:
    """A per-session numerator and denominator; arms aggregate as a ratio of sums."""

    numerator: float
    denominator: float


@dataclass(frozen=True)
class MetricSpec:
    name: str
    direction: Direction
    margin: Margin
    primary: bool = False


QUALITY = Margin(MarginKind.RELATIVE, 0.03)
RATE = Margin(MarginKind.ABSOLUTE, 0.02)
COST = Margin(MarginKind.RELATIVE, 0.10)

METRICS: tuple[MetricSpec, ...] = (
    MetricSpec("kept_p0_p2", Direction.HIGHER, QUALITY, primary=True),
    MetricSpec("kept_p0_p2_per_100_lines", Direction.HIGHER, QUALITY),
    MetricSpec("pass1_kept_share", Direction.HIGHER, QUALITY),
    MetricSpec("rejected_share", Direction.LOWER, RATE),
    MetricSpec("untriaged_per_session", Direction.LOWER, COST),
    # Overlapping slices and extra shots raise duplicates while also raising recall, so a rise is not a regression.
    MetricSpec("duplicates_per_session", Direction.NEUTRAL, COST),
    MetricSpec("waves", Direction.LOWER, COST),
    MetricSpec("slices_past_cap", Direction.LOWER, COST),
    MetricSpec("judged_slices", Direction.NEUTRAL, COST),
    MetricSpec("judge_runs", Direction.NEUTRAL, COST),
    MetricSpec("wall_minutes", Direction.LOWER, COST),
    MetricSpec("busy_minutes", Direction.LOWER, COST),
    MetricSpec("tokens", Direction.LOWER, COST),
    MetricSpec("usd", Direction.LOWER, COST),
    MetricSpec("failure_rate", Direction.LOWER, RATE),
    MetricSpec("classifier_retries", Direction.LOWER, COST),
    MetricSpec("incomplete_rate", Direction.LOWER, RATE),
)


def primary_metric(specs: Iterable[MetricSpec]) -> MetricSpec:
    return next(spec for spec in specs if spec.primary)


def apply_margin_overrides(specs: Iterable[MetricSpec], overrides: Iterable[str]) -> tuple[MetricSpec, ...]:
    """Apply `metric=value` overrides; the value keeps the metric's margin kind, and `%` divides by 100."""

    by_name = {spec.name: spec for spec in specs}
    for override in overrides:
        name, separator, text = override.partition("=")
        if not separator or name not in by_name:
            raise EvalError(f"margin override must be metric=value with a known metric: {override}")
        text = text.strip()
        try:
            value = float(text.removesuffix("%")) / (100 if text.endswith("%") else 1)
        except ValueError as exc:
            raise EvalError(f"margin value is not a number: {override}") from exc
        if not math.isfinite(value) or value <= 0:
            raise EvalError(f"margin value must be a positive finite number: {override}")
        spec = by_name[name]
        by_name[name] = replace(spec, margin=Margin(spec.margin.kind, value))
    return tuple(by_name.values())


def session_metrics(
    session: Session, *, usage: SessionUsage | None, diff_size: DiffSize | None
) -> dict[str, Ratio]:
    """Incomplete sessions only count toward incomplete_rate; their partial work would bias every other metric."""

    if not session.completed:
        return {"incomplete_rate": Ratio(1, 1)}
    return {
        name: Ratio(numerator, denominator)
        for name, (numerator, denominator) in session_metric_values(session, usage, diff_size).items()
        if denominator > 0
    }
