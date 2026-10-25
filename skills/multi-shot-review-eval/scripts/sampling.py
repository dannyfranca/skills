from __future__ import annotations

import random
from pathlib import Path
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from diff_size import target_resolves
from eval_errors import EvalError
from findings import Finding, FindingClass, load_findings
from review_target import TargetKind
from session_store import Session


# P0–P2 findings carry the review value the primary metric counts, so audits favor them over P3.
SEVERITY_WEIGHTS = {"P0": 3.0, "P1": 3.0, "P2": 2.0, "P3": 1.0}


class Judgeability(StrEnum):
    EXACT = "exact"
    APPROXIMATE = "approximate"
    UNJUDGEABLE = "unjudgeable"


@dataclass(frozen=True)
class SampleQuota:
    kept: int
    rejected: int

    def __post_init__(self) -> None:
        if self.kept < 0 or self.rejected < 0:
            raise EvalError(f"sample quotas must be nonnegative, got kept={self.kept} rejected={self.rejected}")


@dataclass(frozen=True)
class SampleEntry:
    arm: str
    session_id: str
    session_dir: str
    slice_name: str
    finding_id: str
    finding_class: FindingClass
    severity: str
    title: str
    location: str
    content: str
    report_path: str | None
    judgeability: Judgeability


def judgeability(session: Session) -> Judgeability:
    """Exact means the reviewed commit still exists; approximate means only code that moved since the review remains."""

    if session.root is None or not session.root.is_dir():
        return Judgeability.UNJUDGEABLE
    if target_resolves(session):
        return Judgeability.EXACT
    if session.target.kind in (TargetKind.BASE, TargetKind.UNCOMMITTED):
        return Judgeability.APPROXIMATE
    return Judgeability.UNJUDGEABLE


def draw_sample(
    arms: Mapping[str, Sequence[Session]],
    *,
    quota: SampleQuota,
    seed: int,
    exclude: Collection[tuple[str, str]],
    judgeability_of: Callable[[Session], Judgeability] = judgeability,
) -> list[SampleEntry]:
    entries = []
    for arm, sessions in arms.items():
        by_id = {session.id: session for session in sessions}
        # Sessions with unreadable runs are absent from the finding metrics, so the audit must not see them either.
        loaded = (load_findings(session) for session in sessions)
        pool = [
            finding
            for findings in loaded
            if findings.complete
            for finding in findings.findings
            if (finding.session_id, finding.finding_id) not in exclude
        ]
        verdicts = {session_id: judgeability_of(session) for session_id, session in by_id.items()}
        for finding_class, size in ((FindingClass.KEPT, quota.kept), (FindingClass.REJECTED, quota.rejected)):
            candidates = [finding for finding in pool if finding.finding_class == finding_class]
            rng = random.Random(f"{seed}:{arm}:{finding_class}")
            for finding in _weighted_without_replacement(candidates, size, rng):
                entries.append(_entry(arm, by_id[finding.session_id], finding, verdicts[finding.session_id]))
    return entries


def _weighted_without_replacement(candidates: list[Finding], size: int, rng: random.Random) -> list[Finding]:
    # Efraimidis-Spirakis: the top keys u^(1/w) form a weighted sample without replacement.
    keyed = sorted(
        candidates,
        key=lambda finding: rng.random() ** (1 / SEVERITY_WEIGHTS.get(finding.severity, 1.0)),
        reverse=True,
    )
    return keyed[:size]


def _entry(arm: str, session: Session, finding: Finding, judged: Judgeability) -> SampleEntry:
    return SampleEntry(
        arm=arm,
        session_id=session.id,
        session_dir=str(session.directory),
        slice_name=finding.slice_name,
        finding_id=finding.finding_id,
        finding_class=finding.finding_class,
        severity=finding.severity,
        title=finding.title,
        location=finding.location,
        content=finding.content,
        report_path=_current_report_path(session, finding.report_path),
        judgeability=judged,
    )


def _current_report_path(session: Session, recorded: str | None) -> str | None:
    """Runs record the report path from the worktree that ran them, which may be gone by evaluation time."""
    if recorded is None:
        return None
    if Path(recorded).is_file():
        return recorded
    moved = session.directory / Path(recorded).name
    return str(moved) if moved.is_file() else None
