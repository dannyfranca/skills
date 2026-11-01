from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from findings import Finding, FindingClass, run_lane, session_runs
from session_store import Session
from tally import Tally, per_usd
from usage import SessionUsage

GroupKey = tuple[str, str]


@dataclass(frozen=True)
class LaneRun:
    lane: str
    tokens: int | None
    usd: float | None


@dataclass(frozen=True)
class LaneCounts(Tally):
    kept: int = 0
    unique: int = 0
    shared: int = 0
    rejected: int = 0
    tokens: int | None = 0
    usd: float | None = 0.0

    def as_dict(self) -> dict[str, float | int | None]:
        triaged = self.kept + self.rejected
        return {
            "kept": self.kept,
            "unique": self.unique,
            "shared": self.shared,
            "rejected": self.rejected,
            "rejection_rate": self.rejected / triaged if triaged else None,
            "tokens": self.tokens,
            "usd": self.usd,
            "kept_per_usd": per_usd(self.kept, self.usd),
            "unique_per_usd": per_usd(self.unique, self.usd),
        }


def lane_runs(session: Session, usage: SessionUsage | None) -> list[LaneRun]:
    """A run without a readable log has an unknown cost, which makes its lane's cost unknown."""

    by_run = usage.by_run if usage is not None else {}
    runs = []
    for run in session_runs(session):
        log = by_run.get(str(run.get("id")))
        runs.append(LaneRun(run_lane(run), log.tokens if log else None, log.usd if log else None))
    return runs


def lane_counts(findings: Iterable[Finding], *, runs: Iterable[LaneRun] = ()) -> dict[str, LaneCounts]:
    """Count each same-wave duplicate group once, credited to every lane that reported it.

    Lanes in `runs` report zeros without findings, because a lane that adds nothing is itself a result.
    """

    counts: dict[str, LaneCounts] = {}
    for item in runs:
        counts[item.lane] = counts.get(item.lane, LaneCounts()) + LaneCounts(tokens=item.tokens, usd=item.usd)
    for outcome, members in triaged_groups(findings):
        lanes = list(dict.fromkeys(member.lane for member in members))
        kept = outcome is FindingClass.KEPT
        for lane in lanes:
            counts[lane] = counts.get(lane, LaneCounts()) + LaneCounts(
                kept=int(kept),
                unique=int(kept and len(lanes) == 1),
                shared=int(kept and len(lanes) > 1),
                rejected=int(not kept),
            )
    return dict(sorted(counts.items()))


def triaged_groups(findings: Iterable[Finding]) -> list[tuple[FindingClass, list[Finding]]]:
    """Same-wave duplicate groups that the author kept or rejected, each with all its reports.

    The kept copy decides the group outcome: the author triages only that copy, so one rejection closes the group.
    """

    by_id = {(item.slice_name, item.finding_id): item for item in findings}
    groups: dict[GroupKey, list[Finding]] = {}
    for key, item in by_id.items():
        groups.setdefault(_group_root(key, by_id), []).append(item)
    return [
        (by_id[root].finding_class, members)
        for root, members in groups.items()
        if by_id[root].finding_class in (FindingClass.KEPT, FindingClass.REJECTED)
    ]


def _group_root(key: GroupKey, by_id: Mapping[GroupKey, Finding]) -> GroupKey:
    # A later shot can demote an earlier kept copy, so a group chains to its last kept copy.
    root, seen = key, {key}
    while (canonical := by_id[root].grouped_into) is not None:
        nxt = (root[0], canonical)
        if nxt not in by_id or nxt in seen:
            break
        root = nxt
        seen.add(root)
    return root
