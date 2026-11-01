from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from diff_size import DiffSize, resolve_diff_size
from findings import load_findings, session_runs
from lane_metrics import LaneCounts, lane_counts, lane_runs
from metrics import Ratio, session_metrics
from prices import PriceTable
from session_store import Session
from setting_metrics import (
    DedupeCounts,
    JudgeCounts,
    SeverityCounts,
    ShotYield,
    dedupe_counts,
    dedupe_events,
    judge_counts,
    severity_counts,
    shot_yield,
)
from usage import ExpectedLogs, SessionUsage, session_usage


@dataclass(frozen=True)
class SessionFacts:
    session: Session
    usage: SessionUsage | None
    diff_size: DiffSize | None
    metrics: dict[str, Ratio]
    finding_classes: Counter[str]
    findings_complete: bool
    lanes: dict[str, LaneCounts] = field(default_factory=dict)
    shots: ShotYield = field(default_factory=ShotYield)
    dedupe: DedupeCounts = field(default_factory=DedupeCounts)
    judge: JudgeCounts = field(default_factory=JudgeCounts)
    severity: dict[str, SeverityCounts] = field(default_factory=dict)


class FactsCache:
    """Log parsing and git calls are the slow part, so each session is measured once per run."""

    def __init__(self, prices: PriceTable) -> None:
        self._prices = prices
        self._facts: dict[str, SessionFacts] = {}

    def get(self, session: Session) -> SessionFacts:
        if session.id not in self._facts:
            self._facts[session.id] = self._collect(session)
        return self._facts[session.id]

    def collected(self) -> list[SessionFacts]:
        return list(self._facts.values())

    def _collect(self, session: Session) -> SessionFacts:
        if not session.completed:
            return SessionFacts(
                session, None, None, session_metrics(session, usage=None, diff_size=None), Counter(), findings_complete=False
            )
        usage = session_usage(session.directory, self._prices, expected=_expected_logs(session))
        diff_size = resolve_diff_size(session)
        loaded = load_findings(session)
        if not loaded.complete:
            return SessionFacts(
                session, usage, diff_size, session_metrics(session, usage=usage, diff_size=diff_size), Counter(), findings_complete=False
            )
        return SessionFacts(
            session=session,
            usage=usage,
            diff_size=diff_size,
            metrics=session_metrics(session, usage=usage, diff_size=diff_size),
            # Class totals and setting tallies must describe the same sessions as the quality metrics, which skip partial findings.
            finding_classes=Counter(item.finding_class.value for item in loaded.findings),
            findings_complete=True,
            lanes=lane_counts(loaded.findings, runs=lane_runs(session, usage)),
            shots=shot_yield(session, loaded.findings),
            dedupe=dedupe_counts(session, usage),
            judge=judge_counts(session, loaded.findings),
            severity=severity_counts(loaded.findings),
        )


def _expected_logs(session: Session) -> ExpectedLogs:
    """Each run, judgement, classification and dedupe writes one log pair, so fewer logs mean unknown spend."""

    return ExpectedLogs(
        run_ids=frozenset(str(run.get("id")) for run in session_runs(session)),
        classifiers=len(session.classifications),
        judges=sum(len(slice_state.get("judgements") or []) for slice_state in session.slices.values()),
        dedupes=len(dedupe_events(session)),
    )
