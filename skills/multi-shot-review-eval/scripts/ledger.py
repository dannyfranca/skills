from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from eval_errors import EvalError


class Decision(StrEnum):
    ADOPT = "adopt"
    REJECT = "reject"
    CONTINUE = "continue"


class RecordType(StrEnum):
    EVAL = "eval"
    DECISION = "decision"


@dataclass(frozen=True)
class EvalRecord:
    eval_id: str
    created_at: str
    contrast_key: str
    window: dict[str, str]
    n: dict[str, int]
    metrics: dict[str, dict[str, dict[str, Any]]]
    sessions: dict[str, list[str]]
    pooled_metrics: dict[str, dict[str, dict[str, Any]]]


class Ledger:
    """Append-only JSONL: eval records plus decision records; a later decision supersedes an earlier one."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append_eval(self, record: EvalRecord) -> None:
        self._append({"type": RecordType.EVAL.value, **asdict(record)})

    def record_decision(self, eval_id: str, decision: Decision, *, at: str) -> None:
        if eval_id not in {record["eval_id"] for record in self._evals()}:
            raise EvalError(f"no eval {eval_id} in {self.path}")
        self._append({"type": RecordType.DECISION.value, "eval_id": eval_id, "decision": decision.value, "at": at})

    def eval_ids(self, contrast_key: str) -> list[str]:
        return [record["eval_id"] for record in self._evals() if record["contrast_key"] == contrast_key]

    def pooled_sessions(self, contrast_key: str) -> dict[str, set[str]]:
        pooled: dict[str, set[str]] = {}
        for record in self._evals():
            if record["contrast_key"] == contrast_key:
                for arm, session_ids in record["sessions"].items():
                    pooled.setdefault(arm, set()).update(session_ids)
        return pooled

    def adopted_gains(self, primary: str) -> dict[str, Any]:
        """One gain per adopted contrast key: re-evaluating the same change must not count it twice.

        The latest adopted eval of a key wins, and its pooled estimate is used because it covers every earlier session.
        """

        latest: dict[str, str] = {}
        for record in self._records():
            if record.get("type") == RecordType.DECISION:
                latest[record["eval_id"]] = record["decision"]
        by_key = {
            record["contrast_key"]: record
            for record in self._evals()
            if latest.get(record["eval_id"]) == Decision.ADOPT
        }
        adopted = [
            {
                "eval_id": record["eval_id"],
                "contrast_key": key,
                "arm": arm,
                "diff": result.get("diff"),
                "rel_diff": result.get("rel_diff"),
            }
            for key, record in by_key.items()
            for arm, result in _gain_metrics(record).get(primary, {}).items()
        ]
        return {
            "metric": primary,
            "adopted": adopted,
            "total_diff": sum(gain["diff"] or 0 for gain in adopted),
            "compounded_rel_diff": math.prod(1 + (gain["rel_diff"] or 0) for gain in adopted) - 1,
        }

    def _evals(self) -> list[dict[str, Any]]:
        return [record for record in self._records() if record.get("type") == RecordType.EVAL]

    def _records(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        records = []
        for line in self.path.read_text().splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                records.append(value)
        return records

    def _append(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")


def _gain_metrics(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return record.get("pooled_metrics") or record["metrics"]
