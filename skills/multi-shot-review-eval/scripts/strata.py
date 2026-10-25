from __future__ import annotations

import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from diff_size import DiffSize
from metrics import Ratio
from session_store import Session
from usage import log_model


IMBALANCE_SHARE_GAP = 0.20
UNKNOWN = "unknown"
DEFAULT_MODEL = "default"


class Dimension(StrEnum):
    REPOSITORY = "repository"
    HARNESS_MODEL = "harness_model"
    DIFF_SIZE = "diff_size"


@dataclass(frozen=True)
class StratumPrimary:
    """Point estimates only: a stratum is too small for intervals to mean much, but its direction shows confounding."""

    n: dict[str, int]
    means: dict[str, float | None]
    diff: dict[str, float | None]


@dataclass(frozen=True)
class StratumRow:
    value: str
    counts: dict[str, int]
    shares: dict[str, float]
    imbalanced: bool
    primary: StratumPrimary


def stratify(
    arms: Mapping[str, Sequence[Session]],
    diff_sizes: Mapping[str, DiffSize | None],
    primary_values: Mapping[str, Ratio],
) -> dict[Dimension, list[StratumRow]]:
    tercile = _tercile_labeler([size.changed_lines for size in diff_sizes.values() if size is not None])
    labelers: dict[Dimension, Callable[[Session], list[str]]] = {
        Dimension.REPOSITORY: lambda session: [session.repository],
        Dimension.HARNESS_MODEL: _harness_models,
        Dimension.DIFF_SIZE: lambda session: [tercile(diff_sizes.get(session.id))],
    }
    return {dimension: _rows(arms, labeler, primary_values) for dimension, labeler in labelers.items()}


def _rows(
    arms: Mapping[str, Sequence[Session]],
    labeler: Callable[[Session], list[str]],
    primary_values: Mapping[str, Ratio],
) -> list[StratumRow]:
    counts: dict[str, dict[str, int]] = {}
    members: dict[str, dict[str, list[Ratio]]] = {}
    for arm, sessions in arms.items():
        for session in sessions:
            for value in labeler(session):
                counts.setdefault(value, dict.fromkeys(arms, 0))[arm] += 1
                if session.id in primary_values:
                    members.setdefault(value, {name: [] for name in arms})[arm].append(primary_values[session.id])
    rows = []
    for value in sorted(counts):
        shares = {arm: counts[value][arm] / len(sessions) if sessions else 0.0 for arm, sessions in arms.items()}
        present = [count > 0 for count in counts[value].values()]
        imbalanced = (any(present) and not all(present)) or max(shares.values()) - min(shares.values()) > IMBALANCE_SHARE_GAP
        primary = _primary(members.get(value) or {name: [] for name in arms})
        rows.append(StratumRow(value=value, counts=counts[value], shares=shares, imbalanced=imbalanced, primary=primary))
    return rows


def _primary(values: Mapping[str, Sequence[Ratio]]) -> StratumPrimary:
    means = {arm: _ratio_of_sums(ratios) for arm, ratios in values.items()}
    baseline, *others = means
    return StratumPrimary(
        n={arm: len(ratios) for arm, ratios in values.items()},
        means=means,
        diff={
            arm: None if means[arm] is None or means[baseline] is None else means[arm] - means[baseline] for arm in others
        },
    )


def _ratio_of_sums(values: Sequence[Ratio]) -> float | None:
    """Matches the arm mean in contrast_stats, so stratum and headline estimates compare like for like."""

    denominator = sum(value.denominator for value in values)
    return sum(value.numerator for value in values) / denominator if denominator else None


def _harness_models(session: Session) -> list[str]:
    # Runs record the profile that actually executed; removed slices still spent work on their runs.
    pairs = {
        f"{run.get('harness') or UNKNOWN}/{_run_model(session, run)}"
        for slice_state in session.slices.values()
        for run in slice_state.get("runs") or []
    }
    # Findings carry no run profile, so a session's quality belongs to its whole profile mix, not to each model in it.
    return ["+".join(sorted(pairs)) or UNKNOWN]


def _run_model(session: Session, run: Mapping[str, Any]) -> str:
    """A run without a model used the harness default of its day; a later slice redefinition must not relabel it."""

    if run.get("model"):
        return str(run["model"])
    for stdout in sorted((session.directory / "_logs").glob(f"{run.get('id')}-*.stdout.log")):
        model = log_model(stdout, stdout.with_name(stdout.name.removesuffix(".stdout.log") + ".stderr.log"))
        if model:
            return model
    return DEFAULT_MODEL


def _tercile_labeler(sizes: list[int]) -> Callable[[DiffSize | None], str]:
    """Tercile cut points come from all known sizes so every arm is bucketed on the same scale."""

    cuts = statistics.quantiles(sizes, n=3) if len(sizes) >= 2 else [float("inf"), float("inf")]

    def label(size: DiffSize | None) -> str:
        if size is None:
            return UNKNOWN
        if size.changed_lines <= cuts[0]:
            return "small"
        return "medium" if size.changed_lines <= cuts[1] else "large"

    return label
