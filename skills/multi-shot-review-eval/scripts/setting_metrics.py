from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field

from findings import Finding, FindingClass, session_runs
from lane_metrics import triaged_groups
from session_store import Session
from usage import SessionUsage
from tally import Tally, merge_tallies

SEVERITIES = ("P0", "P1", "P2", "P3")
DEDUPE_COMPLETED = "dedupe_completed"
DEDUPE_FAILED = "dedupe_failed"


@dataclass(frozen=True)
class ShotCounts(Tally):
    runs: int = 0
    kept: int = 0
    rejected: int = 0


@dataclass(frozen=True)
class ShotYield:
    """Kept groups that no first shot reported are the value that extra shots add."""

    by_shot: Mapping[str, ShotCounts] = field(default_factory=dict)
    extra_only_kept: int = 0

    def __add__(self, other: ShotYield) -> ShotYield:
        return ShotYield(merge_tallies([self.by_shot, other.by_shot]), self.extra_only_kept + other.extra_only_kept)

    def as_dict(self) -> dict[str, object]:
        return {"by_shot": {shot: asdict(counts) for shot, counts in self.by_shot.items()}, "extra_only_kept": self.extra_only_kept}


@dataclass(frozen=True)
class DedupeCounts(Tally):
    runs: int = 0
    failed: int = 0
    merged: int = 0
    tokens: int | None = 0
    usd: float | None = 0.0


@dataclass(frozen=True)
class JudgeCounts(Tally):
    continues: int = 0
    stops: int = 0
    continues_with_kept: int = 0
    kept_after_continue: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "continue": self.continues,
            "stop": self.stops,
            "continue_with_kept": self.continues_with_kept,
            "kept_after_continue": self.kept_after_continue,
        }


@dataclass(frozen=True)
class SeverityCounts(Tally):
    kept: int = 0
    rejected: int = 0


def shot_yield(session: Session, findings: Iterable[Finding]) -> ShotYield:
    counts: dict[str, ShotCounts] = {}
    for run in session_runs(session):
        shot = str(run.get("shot") or 1)
        counts[shot] = counts.get(shot, ShotCounts()) + ShotCounts(runs=1)
    extra_only = 0
    for outcome, members in triaged_groups(findings):
        kept = outcome is FindingClass.KEPT
        shots = {str(member.shot) for member in members}
        for shot in shots:
            counts[shot] = counts.get(shot, ShotCounts()) + ShotCounts(kept=int(kept), rejected=int(not kept))
        extra_only += kept and "1" not in shots
    return ShotYield(dict(sorted(counts.items(), key=lambda item: int(item[0]))), extra_only)


def dedupe_counts(session: Session, usage: SessionUsage | None) -> DedupeCounts:
    """Dedupe children have no run record; their history events and log pairs are the only trace.

    A child that finishes after its slice is removed or redefined writes a log but no event, so the larger trace counts.
    """

    events = dedupe_events(session)
    completed = [event for event in events if event.get("event") == DEDUPE_COMPLETED]
    failed = sum(event.get("event") == DEDUPE_FAILED for event in events)
    logs = usage.dedupe if usage is not None else ()
    runs = max(len(events), len(logs))
    logged = len(logs) >= runs
    return DedupeCounts(
        runs=runs,
        failed=failed,
        merged=sum(int(event.get("duplicates") or 0) for event in completed),
        tokens=sum(log.tokens for log in logs) if logged and all(log.tokens is not None for log in logs) else None,  # type: ignore[misc]
        usd=sum(log.usd for log in logs) if logged and all(log.usd is not None for log in logs) else None,  # type: ignore[misc]
    )


def dedupe_events(session: Session) -> list[dict]:
    """Each dedupe child of a live slice ends in exactly one of these events, so their count is the least dedupe run count."""

    history = session.state.get("history") or []
    return [event for event in history if isinstance(event, dict) and event.get("event") in (DEDUPE_COMPLETED, DEDUPE_FAILED)]


def judge_counts(session: Session, findings: Iterable[Finding]) -> JudgeCounts:
    """A `continue` pays off when its slice definition later keeps a finding.

    Reactivation starts a new definition with its own pass window, so a verdict only covers runs of its own definition.
    """

    versions = {str(run.get("id")): run.get("definition_version", 1) for run in session_runs(session)}
    kept = [item for item in findings if item.finding_class is FindingClass.KEPT]
    total = JudgeCounts()
    for slice_name, slice_state in session.slices.items():
        continues: dict[int, list[int]] = {}
        stops = 0
        for verdict in slice_state.get("judgements") or []:
            if verdict.get("verdict") == "continue":
                continues.setdefault(verdict.get("definition_version", 1), []).append(int(verdict.get("pass") or 0))
            elif verdict.get("verdict") == "stop":
                stops += 1
        paid = 0
        after = 0
        for version, passes in continues.items():
            later = [item for item in kept if item.slice_name == slice_name and versions.get(item.run_id) == version]
            paid += sum(any(item.pass_number > judged for item in later) for judged in passes)
            after += sum(item.pass_number > min(passes) for item in later)
        total += JudgeCounts(sum(map(len, continues.values())), stops, paid, after)
    return total


def severity_counts(findings: Iterable[Finding]) -> dict[str, SeverityCounts]:
    counts = {severity: SeverityCounts() for severity in SEVERITIES}
    for item in findings:
        if item.finding_class in (FindingClass.KEPT, FindingClass.REJECTED) and item.severity in counts:
            kept = item.finding_class is FindingClass.KEPT
            counts[item.severity] += SeverityCounts(kept=int(kept), rejected=int(not kept))
    return counts
