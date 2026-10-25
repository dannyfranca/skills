from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from metrics import Direction, MetricSpec, Ratio


Z_95 = 1.959964
Z_POWER_80 = 0.841621
WORSENING_PROBABILITY = 0.80
# One session per arm resamples to itself, so between-session variance is not estimable.
MIN_SESSIONS_FOR_UNCERTAINTY = 2


class Equivalence(StrEnum):
    INSIDE = "inside"
    OUTSIDE = "outside"
    UNDETERMINED = "undetermined"


Interval = tuple[float, float]


@dataclass(frozen=True)
class ArmSummary:
    n: int
    mean: float | None
    ci95: Interval | None


@dataclass(frozen=True)
class ContrastResult:
    n_baseline: int
    n_treatment: int
    baseline_mean: float | None
    treatment_mean: float | None
    diff: float | None
    rel_diff: float | None
    ci95: Interval | None
    ci90: Interval | None
    se: float | None
    mde: float | None
    margin_abs: float | None
    equivalence: Equivalence
    sessions_needed: int | None
    p_worse: float | None
    worsening: bool


def arm_summary(values: Sequence[Ratio], *, seed: int, resamples: int, key: str) -> ArmSummary:
    mean = _ratio_of_sums(values)
    if mean is None:
        return ArmSummary(n=len(values), mean=None, ci95=None)
    rng = random.Random(f"{seed}:{key}")
    draws = sorted(value for value in (_resample(values, rng) for _ in range(resamples)) if value is not None)
    return ArmSummary(n=len(values), mean=mean, ci95=_interval(draws, 0.95))


def contrast(
    baseline: Sequence[Ratio],
    treatment: Sequence[Ratio],
    spec: MetricSpec,
    *,
    seed: int,
    resamples: int,
    key: str,
) -> ContrastResult:
    """Session-level bootstrap of treatment minus baseline; sessions, not findings, are the unit of resampling."""

    base_mean, treat_mean = _ratio_of_sums(baseline), _ratio_of_sums(treatment)
    if base_mean is None or treat_mean is None:
        return _empty(len(baseline), len(treatment), base_mean, treat_mean)
    if min(len(baseline), len(treatment)) < MIN_SESSIONS_FOR_UNCERTAINTY:
        return replace(
            _empty(len(baseline), len(treatment), base_mean, treat_mean),
            diff=treat_mean - base_mean,
            rel_diff=(treat_mean - base_mean) / abs(base_mean) if base_mean else None,
            margin_abs=spec.margin.absolute(base_mean),
        )
    rng = random.Random(f"{seed}:{key}")
    diffs, base_draws, treat_draws = [], [], []
    for _ in range(resamples):
        base, treat = _resample(baseline, rng), _resample(treatment, rng)
        if base is not None and treat is not None:
            diffs.append(treat - base)
            base_draws.append(base)
            treat_draws.append(treat)
    diffs.sort()
    diff = treat_mean - base_mean
    ci95, ci90 = _interval(diffs, 0.95), _interval(diffs, 0.90)
    se = _stdev(diffs)
    mde = (Z_95 + Z_POWER_80) * se
    margin_abs = spec.margin.absolute(base_mean)
    p_worse = _p_worse(diffs, spec.direction)
    return ContrastResult(
        n_baseline=len(baseline),
        n_treatment=len(treatment),
        baseline_mean=base_mean,
        treatment_mean=treat_mean,
        diff=diff,
        rel_diff=diff / abs(base_mean) if base_mean else None,
        ci95=ci95,
        ci90=ci90,
        se=se,
        mde=mde,
        margin_abs=margin_abs,
        equivalence=_equivalence(ci90, ci95, margin_abs),
        sessions_needed=_sessions_needed(
            margin_abs, _ArmSpread(base_draws, len(baseline)), _ArmSpread(treat_draws, len(treatment))
        ),
        p_worse=p_worse,
        worsening=not spec.primary and p_worse is not None and p_worse >= WORSENING_PROBABILITY,
    )


def _ratio_of_sums(values: Sequence[Ratio]) -> float | None:
    denominator = sum(value.denominator for value in values)
    return sum(value.numerator for value in values) / denominator if denominator else None


def _resample(values: Sequence[Ratio], rng: random.Random) -> float | None:
    picks = rng.choices(values, k=len(values))
    denominator = sum(value.denominator for value in picks)
    return sum(value.numerator for value in picks) / denominator if denominator else None


def _interval(sorted_values: list[float], level: float) -> Interval | None:
    if not sorted_values:
        return None
    tail = (1 - level) / 2
    return (_quantile(sorted_values, tail), _quantile(sorted_values, 1 - tail))


def _quantile(sorted_values: list[float], q: float) -> float:
    position = q * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _p_worse(diffs: list[float], direction: Direction) -> float | None:
    if not diffs:
        return None
    match direction:
        case Direction.HIGHER:
            return sum(diff < 0 for diff in diffs) / len(diffs)
        case Direction.LOWER:
            return sum(diff > 0 for diff in diffs) / len(diffs)
        case Direction.NEUTRAL:
            return None


def _equivalence(ci90: Interval | None, ci95: Interval | None, margin: float) -> Equivalence:
    """A zero margin (a relative margin on a zero baseline) cannot bound any interval."""

    if margin <= 0:
        return Equivalence.UNDETERMINED
    if ci90 is not None and -margin <= ci90[0] and ci90[1] <= margin:
        return Equivalence.INSIDE
    if ci95 is not None and (ci95[0] > margin or ci95[1] < -margin):
        return Equivalence.OUTSIDE
    return Equivalence.UNDETERMINED


@dataclass(frozen=True)
class _ArmSpread:
    draws: Sequence[float]
    n: int

    @property
    def per_session_variance(self) -> float:
        """A bootstrap mean variance scales as 1/n, so n times it is the variance one session contributes."""

        return _stdev(self.draws) ** 2 * self.n


def _sessions_needed(margin: float, baseline: _ArmSpread, treatment: _ArmSpread) -> int | None:
    """Solve for the balanced per-arm n at which the MDE equals the margin; arms can differ in size and spread."""

    if margin <= 0:
        return None
    variance = baseline.per_session_variance + treatment.per_session_variance
    return max(2, math.ceil(variance * ((Z_95 + Z_POWER_80) / margin) ** 2))


def _empty(n_baseline: int, n_treatment: int, base: float | None, treat: float | None) -> ContrastResult:
    return ContrastResult(
        n_baseline=n_baseline,
        n_treatment=n_treatment,
        baseline_mean=base,
        treatment_mean=treat,
        diff=None,
        rel_diff=None,
        ci95=None,
        ci90=None,
        se=None,
        mde=None,
        margin_abs=None,
        equivalence=Equivalence.UNDETERMINED,
        sessions_needed=None,
        p_worse=None,
        worsening=False,
    )
