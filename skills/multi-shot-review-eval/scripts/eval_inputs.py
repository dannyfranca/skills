from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from analyst_files import DEFAULT_ANALYSTS, AnalystFile, SampleScope, Synthesis, parse_analyst, parse_synthesis
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
    # A dashboard looks complete only when every default analyst either answered or has a recorded failure.
    problems = [
        *(f"{name} is not a default analyst" for name in sorted((loaded | set(missing)) - set(DEFAULT_ANALYSTS))),
        *(f"{name} has a file and an analysts_missing entry" for name in sorted(loaded & set(missing))),
        *(f"{name} is in analysts_missing more than once" for name in sorted({n for n in missing if missing.count(n) > 1})),
        *(f"{name} has no file and no analysts_missing entry" for name in DEFAULT_ANALYSTS if name not in loaded and name not in missing),
    ]
    if problems:
        raise EvalError(f"{SYNTHESIS_FILE}: " + "; ".join(problems))
    return synthesis


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
