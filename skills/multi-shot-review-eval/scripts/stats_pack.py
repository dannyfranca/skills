from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from arms import ArmPlan
from contrast_stats import arm_summary, contrast
from ledger import EvalRecord, Ledger
from metrics import MetricSpec, Ratio, primary_metric
from prices import TOTAL_TOKENS_ASSUMPTION, PriceTable
from second_round import second_round_offer
from session_facts import FactsCache, SessionFacts
from session_store import Session
from strata import stratify
from timestamps import format_timestamp


RESAMPLES = 2000


@dataclass(frozen=True)
class StatsRequest:
    eval_id: str
    created_at: str
    seed: int
    specs: tuple[MetricSpec, ...]
    today: date


@dataclass(frozen=True)
class StatsContext:
    facts: FactsCache
    prices: PriceTable
    ledger: Ledger
    sessions_by_id: Mapping[str, Session]


def build_stats(plan: ArmPlan, request: StatsRequest, context: StatsContext) -> dict[str, Any]:
    arm_facts = {arm.name: [context.facts.get(session) for session in arm.sessions] for arm in plan.arms}
    contrasts = _contrasts(arm_facts, request, salt="current")
    completed = {name: [f.session for f in facts if f.session.completed] for name, facts in arm_facts.items()}
    sizes = {f.session.id: f.diff_size for facts in arm_facts.values() for f in facts if f.session.completed}
    primary = primary_metric(request.specs).name
    stats = {
        "eval_id": request.eval_id,
        "created_at": request.created_at,
        "inputs": {"seed": request.seed, "resamples": RESAMPLES},
        "mode": plan.mode.value,
        "contrast_key": plan.contrast_key,
        "window": _window(plan),
        "baseline": plan.baseline.name,
        "primary_metric": primary,
        "metric_specs": [asdict(spec) for spec in request.specs],
        "arms": {name: _arm_section(name, facts, request) for name, facts in arm_facts.items()},
        "session_rows": {name: [_session_row(f) for f in facts] for name, facts in arm_facts.items()},
        "contrasts": contrasts,
        "worsening": _worsening(contrasts),
        "strata": {
            dimension.value: [asdict(row) for row in rows]
            for dimension, rows in stratify(completed, sizes, _primary_values(arm_facts, primary)).items()
        },
        "pooled": _pooled(plan, request, context),
        "adopted_gains": context.ledger.adopted_gains(primary),
        "prices": _prices(arm_facts, context.prices, request.today),
    }
    stats["second_round_offer"] = second_round_offer(stats)
    return stats


def eval_record(stats: dict[str, Any], plan: ArmPlan) -> EvalRecord:
    return EvalRecord(
        eval_id=stats["eval_id"],
        created_at=stats["created_at"],
        contrast_key=plan.contrast_key,
        window=stats["window"],
        n={arm.name: len(arm.completed) for arm in plan.arms},
        metrics=_record_metrics(stats["contrasts"]),
        sessions={arm.name: [session.id for session in arm.sessions] for arm in plan.arms},
        pooled_metrics=_record_metrics(stats["pooled"]["contrasts"]),
    )


def _record_metrics(contrasts: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> dict[str, dict[str, dict[str, Any]]]:
    metrics: dict[str, dict[str, dict[str, Any]]] = {}
    for arm, results in contrasts.items():
        for name, result in results.items():
            metrics.setdefault(name, {})[arm] = {key: result[key] for key in ("diff", "rel_diff", "ci95", "equivalence")}
    return metrics


def _contrasts(
    arm_facts: Mapping[str, Sequence[SessionFacts]], request: StatsRequest, *, salt: str
) -> dict[str, dict[str, dict[str, Any]]]:
    names = list(arm_facts)
    baseline = names[0]
    results: dict[str, dict[str, dict[str, Any]]] = {}
    for arm in names[1:]:
        results[arm] = {}
        for spec in request.specs:
            result = contrast(
                _values(arm_facts[baseline], spec.name),
                _values(arm_facts[arm], spec.name),
                spec,
                seed=request.seed,
                resamples=RESAMPLES,
                key=f"{salt}:{spec.name}:{baseline}:{arm}",
            )
            results[arm][spec.name] = asdict(result)
    return results


def _primary_values(arm_facts: Mapping[str, Sequence[SessionFacts]], metric: str) -> dict[str, Ratio]:
    return {f.session.id: f.metrics[metric] for facts in arm_facts.values() for f in facts if metric in f.metrics}


def _values(facts: Sequence[SessionFacts], metric: str) -> list:
    return [f.metrics[metric] for f in facts if metric in f.metrics]


def _arm_section(name: str, facts: Sequence[SessionFacts], request: StatsRequest) -> dict[str, Any]:
    completed = [f for f in facts if f.session.completed]
    usages = [f.usage for f in completed if f.usage is not None]
    classes: Counter[str] = Counter()
    for f in completed:
        classes.update(f.finding_classes)
    return {
        "sessions": [f.session.id for f in facts],
        "n_completed": len(completed),
        "n_incomplete": len(facts) - len(completed),
        "usage_coverage": {
            "sessions_with_logs": sum(usage.logs > 0 for usage in usages),
            "sessions_with_tokens": sum(usage.tokens_complete for usage in usages),
            "sessions_with_usd": sum(usage.usd_complete for usage in usages),
            "logs_priced_from_table": sum(usage.usd_from_price_table for usage in usages),
        },
        "sessions_with_diff_size": sum(f.diff_size is not None for f in completed),
        "sessions_missing_findings": sum(not f.findings_complete for f in completed),
        "finding_classes": dict(sorted(classes.items())),
        "metrics": {
            spec.name: asdict(
                arm_summary(_values(facts, spec.name), seed=request.seed, resamples=RESAMPLES, key=f"arm:{name}:{spec.name}")
            )
            for spec in request.specs
        },
    }


def _session_row(facts: SessionFacts) -> dict[str, Any]:
    session = facts.session
    return {
        "id": session.id,
        "created_at": format_timestamp(session.created_at),
        "completed": session.completed,
        "repository": session.repository,
        "diff_size": asdict(facts.diff_size) if facts.diff_size is not None else None,
        "findings_complete": facts.findings_complete,
        "metrics": {name: [ratio.numerator, ratio.denominator] for name, ratio in sorted(facts.metrics.items())},
    }


def _worsening(contrasts: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> list[dict[str, Any]]:
    return [
        {"arm": arm, "metric": metric, "p_worse": result["p_worse"]}
        for arm, results in contrasts.items()
        for metric, result in results.items()
        if result["worsening"]
    ]


def _pooled(plan: ArmPlan, request: StatsRequest, context: StatsContext) -> dict[str, Any]:
    """Union of sessions from every earlier eval with this contrast key, plus the current arms."""

    prior = context.ledger.pooled_sessions(plan.contrast_key)
    arm_facts: dict[str, list[SessionFacts]] = {}
    for arm in plan.arms:
        ids = {session.id for session in arm.sessions} | prior.get(arm.name, set())
        sessions = sorted((context.sessions_by_id[i] for i in ids if i in context.sessions_by_id), key=lambda s: s.created_at)
        arm_facts[arm.name] = [context.facts.get(session) for session in sessions]
    return {
        "eval_ids": context.ledger.eval_ids(plan.contrast_key),
        "n": {name: sum(f.session.completed for f in facts) for name, facts in arm_facts.items()},
        "contrasts": _contrasts(arm_facts, request, salt="pooled"),
    }


def _prices(arm_facts: Mapping[str, Sequence[SessionFacts]], prices: PriceTable, today: date) -> dict[str, Any]:
    missing = sorted({model for facts in arm_facts.values() for f in facts if f.usage for model in f.usage.unpriced_models})
    return {
        "updated": prices.updated.isoformat(),
        "stale": prices.is_stale(today),
        "missing_models": missing,
        "assumptions": [TOTAL_TOKENS_ASSUMPTION],
    }


def _window(plan: ArmPlan) -> dict[str, str]:
    return {"start": format_timestamp(plan.window.start), "end": format_timestamp(plan.window.end)}

