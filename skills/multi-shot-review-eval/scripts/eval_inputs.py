from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from analyst_files import AnalystFile, SampleScope, Synthesis, parse_analyst, parse_synthesis
from eval_config import DEFAULT_ANALYST_EFFORT
from eval_errors import EvalError


STATS_FILE = "stats.json"
SAMPLE_FILES = ("sample.json", "sample-round2.json")
ANALYSTS_DIR = "analysts"
SYNTHESIS_FILE = "synthesis.json"
DASHBOARD_FILE = "dashboard.html"


@dataclass(frozen=True)
class SampleEntry:
    """A sampled finding plus the draw it came from, so round 2 stays visible next to round 1."""

    round: int
    fields: dict[str, Any]

    @property
    def key(self) -> tuple[str, str]:
        return (self.fields["session_id"], self.fields["finding_id"])


@dataclass(frozen=True)
class AnalystInputs:
    stats: dict[str, Any]
    entries: tuple[SampleEntry, ...]
    analysts: tuple[AnalystFile, ...]
    files: tuple[Path, ...]
    scope: SampleScope


def load_analyst_inputs(eval_dir: Path) -> AnalystInputs:
    stats = read_json(eval_dir / STATS_FILE)
    entries, sample_files = _entries(eval_dir)
    scope = SampleScope(
        unjudgeable={entry.key: entry.fields["judgeability"] == "unjudgeable" for entry in entries},
        arms=tuple(stats["arms"]),
        baseline=stats["baseline"],
    )
    analyst_files = sorted((eval_dir / ANALYSTS_DIR).glob("*.json"))
    if not analyst_files:
        raise EvalError(f"no analyst files in {eval_dir / ANALYSTS_DIR}")
    analysts = tuple(parse_analyst(read_json(path), scope, path.name) for path in analyst_files)
    names = [analyst.analyst for analyst in analysts]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise EvalError(f"more than one analyst file names the analyst {', '.join(duplicates)}")
    return AnalystInputs(
        stats=stats,
        entries=entries,
        analysts=analysts,
        files=(eval_dir / STATS_FILE, *sample_files, *analyst_files),
        scope=scope,
    )


def load_synthesis(eval_dir: Path, inputs: AnalystInputs) -> Synthesis:
    synthesis = parse_synthesis(read_json(eval_dir / SYNTHESIS_FILE), inputs.scope, SYNTHESIS_FILE)
    loaded = {analyst.analyst for analyst in inputs.analysts}
    missing = [entry.analyst for entry in synthesis.analysts_missing]
    chosen = synthesis.analysts
    # A dashboard looks complete only when every chosen analyst either answered or has a recorded failure.
    problems = [
        *(["analysts needs at least two names"] if len(set(chosen)) < 2 else []),
        *_roster_problems(chosen, inputs),
        *(f"{name} is in analysts more than once" for name in sorted({n for n in chosen if chosen.count(n) > 1})),
        *(f"{name} is not in analysts" for name in sorted((loaded | set(missing)) - set(chosen))),
        *(f"{name} has a file and an analysts_missing entry" for name in sorted(loaded & set(missing))),
        *(f"{name} is in analysts_missing more than once" for name in sorted({n for n in missing if missing.count(n) > 1})),
        *(f"{name} has no file and no analysts_missing entry" for name in chosen if name not in loaded and name not in missing),
    ]
    if problems:
        raise EvalError(f"{SYNTHESIS_FILE}: " + "; ".join(problems))
    return synthesis


def _roster_problems(chosen: tuple[str, ...], inputs: AnalystInputs) -> list[str]:
    configured = inputs.stats["configured_analysts"]
    if configured is None:
        # A run-time pick must bring a second provider; two models from one harness give no independent opinion.
        harnesses = {name.split("/", 1)[0] for name in chosen}
        distinct = ["analysts needs two different harnesses"] if len(set(chosen)) >= 2 and len(harnesses) < 2 else []
        efforts, origin = {name: DEFAULT_ANALYST_EFFORT for name in chosen}, "default"
    else:
        names = {spec["analyst"] for spec in configured}
        if set(chosen) != names:
            return [f"analysts must be the configured analysts {sorted(names)}"]
        distinct = []
        # Rosters saved before the default existed store null for an omitted effort; those analysts ran at any effort.
        efforts = {spec["analyst"]: spec["effort"] for spec in configured if spec["effort"] is not None}
        origin = "configured"
    return distinct + [
        f"{analyst.analyst} ran at effort {analyst.effort}, not the {origin} {efforts[analyst.analyst]}"
        for analyst in inputs.analysts
        if analyst.analyst in efforts and analyst.effort != efforts[analyst.analyst]
    ]


def read_json(path: Path) -> Any:
    if not path.is_file():
        raise EvalError(f"missing {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EvalError(f"{path} is not valid JSON: {exc}") from exc


def _entries(eval_dir: Path) -> tuple[tuple[SampleEntry, ...], list[Path]]:
    entries: list[SampleEntry] = []
    files: list[Path] = []
    for draw, name in enumerate(SAMPLE_FILES, start=1):
        path = eval_dir / name
        if draw > 1 and not path.exists():
            continue
        files.append(path)
        entries += [SampleEntry(round=draw, fields=fields) for fields in read_json(path)["entries"]]
    return tuple(entries), files
