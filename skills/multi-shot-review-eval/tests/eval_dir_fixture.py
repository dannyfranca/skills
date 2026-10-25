from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import fixtures  # noqa: F401


DATA = Path(__file__).resolve().parent / "data"
Entry = Mapping[str, Any]


def load_stats() -> dict[str, Any]:
    return json.loads((DATA / "stats.json").read_text())


def load_entries() -> list[dict[str, Any]]:
    return json.loads((DATA / "sample.json").read_text())["entries"]


def default_verdict(entry: Entry) -> str:
    if entry["judgeability"] == "unjudgeable":
        return "unjudgeable"
    return "valid" if entry["finding_class"] == "kept" else "invalid"


def analyst_document(
    name: str,
    entries: list[dict[str, Any]],
    *,
    verdict: Callable[[Entry], str] = default_verdict,
    views: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "analyst": name,
        "effort": "high",
        "judgments": [
            {"session_id": e["session_id"], "finding_id": e["finding_id"], "verdict": verdict(e), "reason": f"checked {e['finding_id']}"}
            for e in entries
        ],
        "views": views if views is not None else [view("b")],
        "notes": "",
    }


def view(arm: str, proposal: str = "keep_testing", confidence: str = "medium") -> dict[str, Any]:
    return {"arm": arm, "proposal": proposal, "confidence": confidence, "reasoning": "kept P0-P2 +2.0 per session, CI [0.9, 3.1]."}


ANALYSTS = ["claude-code/claude-opus-5-5", "codex/gpt-6.1-sol"]


def synthesis_document(missing: list[dict[str, str]] | None = None, analysts: list[Any] | None = None) -> dict[str, Any]:
    return {"analysts": ANALYSTS if analysts is None else analysts, "recommendations": [view("b")], "analysts_missing": missing or []}


def write_eval_dir(
    root: Path,
    *,
    analysts: list[dict[str, Any]] | None = None,
    synthesis: dict[str, Any] | None = None,
    stats: dict[str, Any] | None = None,
) -> Path:
    eval_dir = root / "20261025-1200-a-vs-b"
    (eval_dir / "analysts").mkdir(parents=True)
    shutil.copy(DATA / "sample.json", eval_dir / "sample.json")
    (eval_dir / "stats.json").write_text(json.dumps(stats if stats is not None else load_stats()))
    entries = load_entries()
    for document in analysts if analysts is not None else [analyst_document("claude-code/claude-opus-5-5", entries), analyst_document("codex/gpt-6.1-sol", entries)]:
        (eval_dir / "analysts" / f"{document['analyst'].replace('/', '-')}.json").write_text(json.dumps(document))
    (eval_dir / "synthesis.json").write_text(json.dumps(synthesis if synthesis is not None else synthesis_document()))
    return eval_dir
