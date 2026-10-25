from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
PRICES = SKILL / "references" / "prices.toml"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

BASE_TIME = datetime(2026, 10, 1, tzinfo=timezone.utc)


def iso(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def at(minutes: float) -> str:
    return iso(BASE_TIME + timedelta(minutes=minutes))


def finding(
    finding_id: str,
    severity: str = "P2",
    *,
    outcome: str = "kept",
    title: str | None = None,
) -> dict[str, Any]:
    status, resolution = {
        "kept": ("superseded", {"kind": "superseded", "successor_run_id": "next", "at": at(0)}),
        "removed": ("superseded", {"kind": "superseded", "removed": True, "at": at(0)}),
        "rejected": ("ignored", {"kind": "rejected", "text": "not a defect", "at": at(0)}),
        "duplicate": ("ignored", {"kind": "duplicate", "finding_id": "f_other", "at": at(0)}),
        "open": ("open", None),
    }[outcome]
    return {
        "id": finding_id,
        "severity": severity,
        "title": title or f"Finding {finding_id}",
        "content": f"Body of {finding_id}. " * 5,
        "location": {"path": "src/lib.rs", "start_line": 1, "end_line": 2},
        "status": status,
        "resolution": resolution,
    }


def run(
    run_id: str,
    pass_number: int = 1,
    *,
    findings: list[dict[str, Any]] | None = None,
    status: str | None = None,
    start: float = 1,
    minutes: float = 2,
    archived: bool = False,
) -> dict[str, Any]:
    findings = findings or []
    return {
        "id": run_id,
        "pass": pass_number,
        "shot": 1,
        "status": status or ("findings" if findings else "no_findings"),
        "started_at": at(start),
        "ended_at": at(start + minutes),
        "findings": findings,
        "archived": archived,
        "output_file": f"/reviews/{run_id}.md",
    }


def slice_(
    *runs: dict[str, Any],
    harness: str = "codex",
    model: str = "gpt-6-sol",
    judgements: int = 0,
    judged: tuple[tuple[int, str], ...] = (),
    removed: bool = False,
) -> dict[str, Any]:
    return {
        "harness": harness,
        "model": model,
        "runs": list(runs),
        "judgements": [
            {"pass": 3, "verdict": "stop", "at": at(30), "definition_version": 1}
            for _ in range(judgements)
        ]
        + [
            {"pass": judged_pass, "verdict": verdict, "at": at(30), "definition_version": 1}
            for judged_pass, verdict in judged
        ],
        "removed": removed,
    }


def write_session(
    review_root: Path,
    session_name: str,
    *,
    repo: str = "owner/repo",
    variant: str | None = "a",
    created: float = 0,
    completed: bool = True,
    slices: dict[str, dict[str, Any]] | None = None,
    classifications: list[str] | None = None,
    target: dict[str, Any] | None = None,
    root: Path | None = None,
    session_extra: dict[str, Any] | None = None,
    history_end: float | None = None,
) -> Path:
    directory = review_root / repo / session_name
    directory.mkdir(parents=True)
    state_slices: dict[str, Any] = {}
    for name, definition in (slices or {}).items():
        runs = []
        for run_def in definition["runs"]:
            stored = {key: value for key, value in run_def.items() if key != "archived"}
            if run_def["archived"]:
                history = directory / "history"
                history.mkdir(exist_ok=True)
                archive = history / f"{run_def['id']}.json"
                archive.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "run_id": run_def["id"],
                            "slice": name,
                            "pass": run_def["pass"],
                            "archived_at": at(0),
                            "findings": run_def["findings"],
                        }
                    )
                )
                stored["findings"] = None
                stored["findings_archive"] = str(archive)
            else:
                stored["findings_archive"] = None
            stored.update(
                harness=definition["harness"], model=definition["model"], finding_count=len(run_def["findings"])
            )
            runs.append(stored)
        state_slices[name] = {
            "name": name,
            "harness": definition["harness"],
            "model": definition["model"],
            "runs": runs,
            "judgements": definition["judgements"],
            "removed": definition["removed"],
            "complete": True,
        }
    session: dict[str, Any] = {
        "created_at": at(created),
        "repository": {"name": repo, "branch": "main", "remote": None},
        "review_dir": str(directory),
        "root": str(root or review_root / "missing-root"),
        "target": target or {"kind": "uncommitted"},
        "config": {},
    }
    if variant is not None:
        session["variant"] = variant
    session.update(session_extra or {})
    end = history_end if history_end is not None else created + 10
    state = {
        "schema_version": 4,
        "completed": completed,
        "last_error": None,
        "session": session,
        "slices": state_slices,
        "classifications": [
            {
                "id": f"c{index}",
                "status": status,
                "harness": "codex",
                "model": "gpt-6.1-sol",
                "started_at": at(created),
                "ended_at": at(created + 1),
            }
            for index, status in enumerate(classifications or ["succeeded"])
        ],
        "history": [{"event": "run_completed", "at": at(end)}],
    }
    (directory / "_state.json").write_text(json.dumps(state))
    return directory


def write_logs(
    session_dir: Path, name: str, *, stdout: str = "", stderr: str = "", modified: datetime | None = None
) -> None:
    logs = session_dir / "_logs"
    logs.mkdir(exist_ok=True)
    for stream, text in (("stdout", stdout), ("stderr", stderr)):
        path = logs / f"{name}.{stream}.log"
        path.write_text(text)
        if modified is not None:
            os.utime(path, (modified.timestamp(), modified.timestamp()))


def claude_stdout(cost: float = 0.5, model: str = "claude-opus-5-5") -> str:
    return json.dumps(
        {
            "type": "result",
            "total_cost_usd": cost,
            "usage": {"input_tokens": 10, "output_tokens": 20},
            "modelUsage": {
                model: {
                    "inputTokens": 100,
                    "outputTokens": 50,
                    "cacheReadInputTokens": 30,
                    "cacheCreationInputTokens": 20,
                    "costUSD": cost,
                }
            },
        }
    )


def codex_stderr(tokens: str = "85,542", model: str = "gpt-6-sol") -> str:
    return (
        "OpenAI Codex v0.163.0\n--------\nworkdir: /tmp\nmodel: "
        f"{model}\nprovider: openai\n--------\nuser\nreview\n" + "noise\n" * 50
        + f"tokens used\n{tokens}\n"
    )
