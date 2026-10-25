from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from arms import ArmPlan, ArmRequest, PlanMode, plan_arms
from eval_config import load_eval_config
from eval_errors import EvalError
from ledger import Decision, Ledger
from metrics import METRICS, apply_margin_overrides, primary_metric
from prices import load_price_table
from sampling import SampleQuota, draw_sample
from second_round import SAMPLE_FILE, draw_second_round, sample_document
from session_facts import FactsCache
from session_store import load_sessions
from stats_pack import StatsContext, StatsRequest, build_stats, eval_record
from timestamps import format_timestamp, parse_timestamp


DEFAULT_SEED = 20261025
EVALS_DIR = "_evals"
LEDGER_FILE = "ledger.jsonl"
PRICES_PATH = Path(__file__).resolve().parent.parent / "references" / "prices.toml"


def main(argv: list[str] | None = None, *, now: datetime | None = None) -> int:
    args = _parser().parse_args(argv)
    now = now or datetime.now(timezone.utc)
    try:
        summary = _run(args, now)
    except EvalError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2))
    return 0


def _run(args: argparse.Namespace, now: datetime) -> dict[str, Any]:
    explicit = Path(args.config).expanduser() if args.config else None
    config = load_eval_config(Path.cwd(), explicit=explicit)
    review_root = Path(args.review_root).expanduser() if args.review_root else config.review_root
    evals = review_root / EVALS_DIR
    ledger = Ledger(evals / LEDGER_FILE)
    if args.record_decision:
        eval_id, decision = args.record_decision
        ledger.record_decision(eval_id, _decision(decision), at=format_timestamp(now))
        specs = apply_margin_overrides(METRICS, args.margin)
        return {"eval_id": eval_id, "decision": decision, "adopted_gains": ledger.adopted_gains(primary_metric(specs).name)}
    sessions = load_sessions(review_root)
    sessions_by_id = {session.id: session for session in sessions}
    if args.second_round:
        eval_dir = evals / args.second_round
        if not eval_dir.is_dir():
            raise EvalError(f"no eval directory {eval_dir}")
        return draw_second_round(eval_dir, sessions_by_id, kept=args.sample_kept, seed=args.seed)
    plan = plan_arms(sessions, config.active_tags, _arm_request(args))
    eval_dir = _new_eval_dir(evals, now, plan)
    request = StatsRequest(
        eval_id=eval_dir.name,
        created_at=format_timestamp(now),
        seed=args.seed,
        specs=apply_margin_overrides(METRICS, args.margin),
        today=now.date(),
    )
    prices = load_price_table(PRICES_PATH)
    context = StatsContext(FactsCache(prices), prices, ledger, sessions_by_id)
    stats = build_stats(plan, request, context)
    sample = draw_sample(
        {arm.name: arm.completed for arm in plan.arms},
        quota=SampleQuota(kept=args.sample_kept, rejected=args.sample_rejected),
        seed=args.seed,
        exclude=frozenset(),
    )
    eval_dir.mkdir(parents=True)
    _write_json(eval_dir / "stats.json", stats)
    _write_json(eval_dir / SAMPLE_FILE, sample_document(sample))
    ledger.append_eval(eval_record(stats, plan))
    return _summary(eval_dir, plan, stats)


def _summary(eval_dir: Path, plan: ArmPlan, stats: dict[str, Any]) -> dict[str, Any]:
    primary = stats["primary_metric"]
    return {
        "eval_dir": str(eval_dir),
        "mode": plan.mode.value,
        "contrast_key": plan.contrast_key,
        "window": stats["window"],
        "arms": [{"name": arm.name, "n": len(arm.completed)} for arm in plan.arms],
        "primary_metric": primary,
        "primary": {
            arm: {key: results[primary][key] for key in ("diff", "rel_diff", "ci95", "equivalence", "sessions_needed")}
            for arm, results in stats["contrasts"].items()
        },
        "worsening": stats["worsening"],
        "pooled_eval_ids": stats["pooled"]["eval_ids"],
        "second_round_offer": stats["second_round_offer"],
    }


def _new_eval_dir(evals: Path, now: datetime, plan: ArmPlan) -> Path:
    if plan.mode == PlanMode.CUTOFF:
        slug = "cutoff-" + plan.contrast_key.removeprefix("cutoff:").split("#")[0].split("@")[0]
    else:
        slug = "-vs-".join(arm.name for arm in plan.arms)
    base = f"{now.astimezone(timezone.utc):%Y%m%d-%H%M}-{_safe(slug)}"
    candidate, suffix = evals / base, 2
    while candidate.exists():
        candidate, suffix = evals / f"{base}-{suffix}", suffix + 1
    return candidate


def _safe(text: str) -> str:
    return "".join(char if char.isalnum() or char in "-_." else "-" for char in text)[:80]


def _arm_request(args: argparse.Namespace) -> ArmRequest:
    return ArmRequest(
        tags=tuple(tag.strip() for tag in args.tags.split(",") if tag.strip()) if args.tags else None,
        since=_moment(args.since, "--since"),
        until=_moment(args.until, "--until"),
        repo=args.repo,
        cutoff=args.cutoff,
    )


def _moment(value: str | None, flag: str) -> datetime | None:
    if value is None:
        return None
    moment = parse_timestamp(value)
    if moment is None:
        raise EvalError(f"{flag} is not an ISO timestamp: {value}")
    return moment


def _decision(value: str) -> Decision:
    try:
        return Decision(value)
    except ValueError as exc:
        raise EvalError(f"decision must be one of {[d.value for d in Decision]}: {value}") from exc


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministic A/B statistics over multi-shot-review sessions.")
    parser.add_argument("--review-root", help="default: config review_root, else ~/.reviews")
    parser.add_argument("--config", help="explicit multi-shot-review.toml; default: the config chain")
    parser.add_argument("--tags", help="comma-separated arm tags; the first is the baseline")
    parser.add_argument("--since", help="ISO start; overrides the computed window start")
    parser.add_argument("--until", help="ISO end; overrides the computed window end")
    parser.add_argument("--repo", help="owner/repo filter")
    parser.add_argument("--cutoff", help="skill_revision or ISO timestamp that splits before/after arms")
    parser.add_argument("--margin", action="append", default=[], help="metric=value equivalence margin, repeatable")
    parser.add_argument("--sample-kept", type=int, default=20)
    parser.add_argument("--sample-rejected", type=int, default=10)
    parser.add_argument("--second-round", metavar="EVAL_ID", help="draw an extra kept-only sample for an eval")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--record-decision", nargs=2, metavar=("EVAL_ID", "DECISION"), help="adopt|reject|continue")
    return parser


if __name__ == "__main__":
    sys.exit(main())
