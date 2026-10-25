from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from diff_size import DiffSize, resolve_diff_size
from findings import load_findings
from metrics import Ratio, session_metrics
from prices import PriceTable
from session_store import Session
from usage import SessionUsage, session_usage


@dataclass(frozen=True)
class SessionFacts:
    session: Session
    usage: SessionUsage | None
    diff_size: DiffSize | None
    metrics: dict[str, Ratio]
    finding_classes: Counter[str]
    findings_complete: bool


class FactsCache:
    """Log parsing and git calls are the slow part, so each session is measured once per run."""

    def __init__(self, prices: PriceTable) -> None:
        self._prices = prices
        self._facts: dict[str, SessionFacts] = {}

    def get(self, session: Session) -> SessionFacts:
        if session.id not in self._facts:
            self._facts[session.id] = self._collect(session)
        return self._facts[session.id]

    def _collect(self, session: Session) -> SessionFacts:
        if not session.completed:
            return SessionFacts(
                session, None, None, session_metrics(session, usage=None, diff_size=None), Counter(), findings_complete=False
            )
        usage = session_usage(session.directory, self._prices, expected_logs=_invocations(session))
        diff_size = resolve_diff_size(session)
        loaded = load_findings(session)
        return SessionFacts(
            session=session,
            usage=usage,
            diff_size=diff_size,
            metrics=session_metrics(session, usage=usage, diff_size=diff_size),
            # Class totals must describe the same sessions as the quality metrics, which skip partial findings.
            finding_classes=Counter(item.finding_class.value for item in loaded.findings) if loaded.complete else Counter(),
            findings_complete=loaded.complete,
        )


def _invocations(session: Session) -> int:
    """Each run, judgement and classification writes one log pair, so fewer logs mean unknown spend."""

    slices = session.slices.values()
    runs = sum(len(slice_state.get("runs") or []) for slice_state in slices)
    judgements = sum(len(slice_state.get("judgements") or []) for slice_state in slices)
    return runs + judgements + len(session.classifications)
