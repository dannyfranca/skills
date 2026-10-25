from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from session_store import Session


class FindingClass(StrEnum):
    KEPT = "kept"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"
    UNTRIAGED = "untriaged"
    REMOVED = "removed"


# P0 counts with P1 and P2 so that a variant cannot gain by downgrading severity; a P0 is never worth less than a P1.
QUALITY_SEVERITIES = frozenset({"P0", "P1", "P2"})


@dataclass(frozen=True)
class Finding:
    session_id: str
    slice_name: str
    run_id: str
    pass_number: int
    finding_id: str
    severity: str
    title: str
    content: str
    location: str
    report_path: str | None
    finding_class: FindingClass


def classify(raw: dict[str, Any]) -> FindingClass:
    """Superseded findings survived author triage; a removed slice supersedes its findings without that triage."""

    if raw.get("status") == "open":
        return FindingClass.UNTRIAGED
    resolution = raw.get("resolution") or {}
    if raw.get("status") == "superseded":
        return FindingClass.REMOVED if resolution.get("removed") else FindingClass.KEPT
    return FindingClass.DUPLICATE if resolution.get("kind") == "duplicate" else FindingClass.REJECTED


@dataclass(frozen=True)
class SessionFindings:
    findings: tuple[Finding, ...]
    missing_runs: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.missing_runs


def load_findings(session: Session) -> SessionFindings:
    findings: list[Finding] = []
    missing: list[str] = []
    for slice_name, slice_state in session.slices.items():
        for run in slice_state.get("runs") or []:
            raw_items = _run_findings(session, run)
            expected = run.get("finding_count")
            if raw_items is None or (isinstance(expected, int) and expected != len(raw_items)):
                missing.append(str(run.get("id")))
                continue
            findings.extend(
                _finding(session, slice_name, run, raw, slice_removed=bool(slice_state.get("removed")))
                for raw in raw_items
            )
    return SessionFindings(findings=tuple(findings), missing_runs=tuple(missing))


def session_findings(session: Session) -> Iterator[Finding]:
    yield from load_findings(session).findings


def _finding(
    session: Session, slice_name: str, run: dict[str, Any], raw: dict[str, Any], *, slice_removed: bool
) -> Finding:
    finding_class = classify(raw)
    # Removing a slice leaves its superseded findings without the removed mark, yet nobody kept them.
    if slice_removed and finding_class is FindingClass.KEPT:
        finding_class = FindingClass.REMOVED
    return Finding(
        session_id=session.id,
        slice_name=slice_name,
        run_id=str(run.get("id")),
        pass_number=int(run.get("pass") or 0),
        finding_id=str(raw.get("id")),
        severity=str(raw.get("severity")),
        title=str(raw.get("title") or ""),
        content=str(raw.get("content") or ""),
        location=_location(raw.get("location")),
        report_path=run.get("output_file"),
        finding_class=finding_class,
    )


def _run_findings(session: Session, run: dict[str, Any]) -> list[dict[str, Any]] | None:
    inline = run.get("findings")
    if isinstance(inline, list):
        return [item for item in inline if isinstance(item, dict)]
    if not run.get("findings_archive"):
        return []
    # Sessions may move between machines, so the archive is located by run id, not its stored path.
    archive = session.directory / "history" / f"{run.get('id')}.json"
    try:
        data = json.loads(archive.read_text())
    except (OSError, ValueError):
        return None
    items = data.get("findings") if isinstance(data, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else None


def _location(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    path = value.get("path") or ""
    start, end = value.get("start_line"), value.get("end_line")
    if start is None:
        return path
    if end is None or end == start:
        return f"{path}:{start}"
    return f"{path}:{start}-{end}"
