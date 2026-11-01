from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from findings import Finding, FindingClass


@dataclass(frozen=True)
class LaneCounts:
    kept: int = 0
    unique: int = 0
    shared: int = 0
    rejected: int = 0

    def __add__(self, other: LaneCounts) -> LaneCounts:
        return LaneCounts(
            kept=self.kept + other.kept,
            unique=self.unique + other.unique,
            shared=self.shared + other.shared,
            rejected=self.rejected + other.rejected,
        )

    def as_dict(self) -> dict[str, float | int | None]:
        triaged = self.kept + self.rejected
        return {
            "kept": self.kept,
            "unique": self.unique,
            "shared": self.shared,
            "rejected": self.rejected,
            "rejection_rate": self.rejected / triaged if triaged else None,
        }


def lane_counts(findings: Iterable[Finding], *, ran: Iterable[str] = ()) -> dict[str, LaneCounts]:
    """Count each same-wave duplicate group once, credited to every lane that reported it.

    The kept copy decides the group outcome: the author triages only that copy, so one rejection closes the group.
    Lanes in `ran` report zeros without findings, because a lane that adds nothing is itself a result.
    """

    by_id = {(item.slice_name, item.finding_id): item for item in findings}
    groups: dict[tuple[str, str], list[str]] = {}
    for key, item in by_id.items():
        root = _group_root(key, by_id)
        lanes = groups.setdefault(root, [])
        if item.lane not in lanes:
            lanes.append(item.lane)
    counts = {lane: LaneCounts() for lane in ran}
    for root, lanes in groups.items():
        outcome = by_id[root].finding_class
        if outcome not in (FindingClass.KEPT, FindingClass.REJECTED):
            continue
        kept = outcome is FindingClass.KEPT
        for lane in lanes:
            counts[lane] = counts.get(lane, LaneCounts()) + LaneCounts(
                kept=int(kept),
                unique=int(kept and len(lanes) == 1),
                shared=int(kept and len(lanes) > 1),
                rejected=int(not kept),
            )
    return dict(sorted(counts.items()))


def merge_lane_counts(sessions: Iterable[Mapping[str, LaneCounts]]) -> dict[str, LaneCounts]:
    merged: dict[str, LaneCounts] = {}
    for counts in sessions:
        for lane, value in counts.items():
            merged[lane] = merged.get(lane, LaneCounts()) + value
    return dict(sorted(merged.items()))


def _group_root(key: tuple[str, str], by_id: Mapping[tuple[str, str], Finding]) -> tuple[str, str]:
    # A later shot can demote an earlier kept copy, so a group chains to its last kept copy.
    root, seen = key, {key}
    while (canonical := by_id[root].grouped_into) is not None:
        nxt = (root[0], canonical)
        if nxt not in by_id or nxt in seen:
            break
        root = nxt
        seen.add(root)
    return root
