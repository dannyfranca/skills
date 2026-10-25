from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from findings import QUALITY_SEVERITIES, FindingClass, load_findings
from judge_time import judge_minutes
from timestamps import parse_timestamp

if TYPE_CHECKING:
    from findings import Finding
    from diff_size import DiffSize
    from session_store import Session
    from usage import SessionUsage


FAILED_RUN_STATUSES = frozenset({"failed", "timeout"})


FINDING_METRICS = frozenset(
    {
        "kept_p0_p2",
        "kept_p0_p2_per_100_lines",
        "pass1_kept_share",
        "rejected_share",
        "untriaged_per_session",
        "duplicates_per_session",
    }
)


def session_metric_values(
    session: Session, usage: SessionUsage | None, diff_size: DiffSize | None
) -> dict[str, tuple[float, float]]:
    runs = [run for slice_state in session.slices.values() for run in slice_state.get("runs") or []]
    judgements = [len(slice_state.get("judgements") or []) for slice_state in session.slices.values()]
    classifications = session.classifications
    values: dict[str, tuple[float, float]] = {
        "waves": (max((int(run.get("pass") or 0) for run in runs), default=0), 1),
        "slices_past_cap": (sum(_past_cap(slice_state) for slice_state in session.slices.values()), 1),
        "judged_slices": (sum(count > 0 for count in judgements), 1),
        "judge_runs": (sum(judgements), 1),
        "wall_minutes": (_wall_minutes(session, runs), 1),
        "busy_minutes": (
            sum(_minutes(item) for item in [*runs, *classifications]) + judge_minutes(session.directory),
            1,
        ),
        "failure_rate": (sum(run.get("status") in FAILED_RUN_STATUSES for run in runs), len(runs)),
        "classifier_retries": (sum(item.get("status") == "failed" for item in classifications), 1),
        "incomplete_rate": (0, 1),
    }
    loaded = load_findings(session)
    # A lost archive would read as zero findings and fake a quality drop, so such sessions report no quality.
    if loaded.complete:
        values.update(_finding_values(loaded.findings, diff_size))
    # Partial log coverage would understate cost, so only fully covered sessions report it.
    if usage is not None and usage.tokens_complete:
        values["tokens"] = (usage.tokens, 1)
    if usage is not None and usage.usd_complete:
        values["usd"] = (usage.usd, 1)
    return values


def _finding_values(findings: tuple[Finding, ...], diff_size: DiffSize | None) -> dict[str, tuple[float, float]]:
    kept = [item for item in findings if item.finding_class == FindingClass.KEPT]
    kept_quality = [item for item in kept if item.severity in QUALITY_SEVERITIES]
    rejected = sum(item.finding_class == FindingClass.REJECTED for item in findings)
    values: dict[str, tuple[float, float]] = {
        "kept_p0_p2": (len(kept_quality), 1),
        "pass1_kept_share": (sum(item.pass_number == 1 for item in kept_quality), len(kept_quality)),
        "rejected_share": (rejected, len(kept) + rejected),
        "untriaged_per_session": (sum(item.finding_class == FindingClass.UNTRIAGED for item in findings), 1),
        "duplicates_per_session": (sum(item.finding_class == FindingClass.DUPLICATE for item in findings), 1),
    }
    if diff_size is not None:
        values["kept_p0_p2_per_100_lines"] = (len(kept_quality) * 100, diff_size.changed_lines)
    return values


def _past_cap(slice_state: dict) -> bool:
    """A slice is past its cap when any definition ran a pass after a judge let it continue beyond its first window.

    Reactivation opens a new definition with its own pass window, so verdicts only govern runs of their own definition;
    an earlier definition that went past its cap still spent that extra work.
    """

    continues: dict[int, list[int]] = {}
    for item in slice_state.get("judgements") or []:
        if item.get("verdict") == "continue":
            continues.setdefault(item.get("definition_version", 1), []).append(int(item.get("pass") or 0))
    latest: dict[int, int] = {}
    for run in slice_state.get("runs") or []:
        version = run.get("definition_version", 1)
        latest[version] = max(latest.get(version, 0), int(run.get("pass") or 0))
    return any(latest.get(version, 0) > min(passes) for version, passes in continues.items())


def _wall_minutes(session: Session, runs: list[dict]) -> float:
    moments: list[datetime | None] = [parse_timestamp(event.get("at")) for event in session.state.get("history") or [] if isinstance(event, dict)]
    moments += [parse_timestamp(item.get("ended_at")) for item in [*runs, *session.classifications]]
    known = [moment for moment in moments if moment is not None]
    end = max(known, default=session.created_at)
    return max(0.0, (end - session.created_at).total_seconds() / 60)


def _minutes(item: dict) -> float:
    start, end = parse_timestamp(item.get("started_at")), parse_timestamp(item.get("ended_at"))
    if start is None or end is None or end < start:
        return 0.0
    return (end - start).total_seconds() / 60
