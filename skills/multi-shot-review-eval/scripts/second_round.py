from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from eval_errors import EvalError
from sampling import SampleQuota, draw_sample
from session_store import Session


SAMPLE_FILE = "sample.json"
ROUND2_FILE = "sample-round2.json"
KEPT_SHARE_GAP = 0.10
JUDGEABILITY_NOTES = {
    "exact": (
        "The `commit` of the entry is the working copy that the reviewers read; "
        "read the file with `git -C <repository> show <commit>:<path>`. "
        "A `staged_commit` is the staging area, which has a staged change of the file that the reviewers also read; "
        "read its version with `git -C <repository> show <staged_commit>:<path>`. "
        "Two versions with the same content have different file modes; "
        "`git -C <repository> ls-tree <commit> -- <path>` shows the mode. "
        "A `base_commit` shows that the reviewed change deleted the file from the working copy or from the staging area; "
        "read the deleted file with `git -C <repository> show <base_commit>:<path>`. "
        "A commit without the `path` did not have the file. "
        "With `working_copy_left_out`, the snapshot did not store the working copy, for example an ignored file "
        "or a file without read permission: the `commit` without the `path` does not show a deletion, "
        "and the `staged_commit` is the only reviewed version that a commit holds. "
        "The finding is valid when the working copy or the staging area has the defect, or when the deletion causes it. "
        "A defect that is only in the deleted file is gone, so it does not make the finding valid. "
        "An entry without a `path` is from an older session; use the path of its `location`."
    ),
    "approximate": "No commit holds the reviewed code; judge the finding against the current files in the `repository` of the entry.",
    "unjudgeable": "No commit holds the file of the finding, and the repository is gone or does not have the file now; the finding cannot be judged against code.",
}


def second_round_offer(stats: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Round 2 is worth offering when the kept share is statistically close yet practically apart.

    Close: the arm 95% CIs of the rejected share overlap. Apart: the point estimates differ by at least 10 points.
    Kept share is one minus rejected share, so both conditions use the same metric.
    """

    arms = stats["arms"]
    baseline = arms[stats["baseline"]]["metrics"]["rejected_share"]
    result = {}
    for arm in stats["contrasts"]:
        treatment = arms[arm]["metrics"]["rejected_share"]
        overlap = _overlap(baseline, treatment)
        gap = _gap(baseline["mean"], treatment["mean"])
        result[arm] = {
            "ci_overlap": overlap,
            "kept_share_gap": gap,
            "offer": overlap and gap is not None and gap >= KEPT_SHARE_GAP,
        }
    return result


def sample_document(entries: Sequence[Any]) -> dict[str, Any]:
    return {"judgeability_notes": JUDGEABILITY_NOTES, "entries": [asdict(entry) for entry in entries]}


def draw_second_round(
    eval_dir: Path, sessions_by_id: Mapping[str, Session], *, kept: int, seed: int
) -> dict[str, Any]:
    path = eval_dir / ROUND2_FILE
    # Analyst judgements reference this exact finding set, so a redraw would orphan them.
    if path.exists():
        raise EvalError(f"second-round sample already exists: {path}")
    stats = _read_json(eval_dir / "stats.json")
    prior = _read_json(eval_dir / SAMPLE_FILE)["entries"]
    exclude = {(entry["session_id"], entry["finding_id"]) for entry in prior}
    # Incomplete sessions count only toward reliability, so their findings never enter a quality sample.
    arms = {
        name: [sessions_by_id[i] for i in section["sessions"] if i in sessions_by_id and sessions_by_id[i].completed]
        for name, section in stats["arms"].items()
    }
    entries = draw_sample(arms, quota=SampleQuota(kept=kept, rejected=0), seed=seed, exclude=exclude)
    path.write_text(json.dumps(sample_document(entries), indent=2) + "\n")
    return {
        "eval_dir": str(eval_dir),
        "sample": str(path),
        "entries": _counts(entries),
        "offer_condition": stats["second_round_offer"],
    }


def _counts(entries: Sequence[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.arm] = counts.get(entry.arm, 0) + 1
    return counts


def _overlap(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    if not left["ci95"] or not right["ci95"]:
        return False
    (low_a, high_a), (low_b, high_b) = left["ci95"], right["ci95"]
    return low_a <= high_b and low_b <= high_a


def _gap(baseline: float | None, treatment: float | None) -> float | None:
    if baseline is None or treatment is None:
        return None
    return abs(treatment - baseline)


def _read_json(path: Path) -> Any:
    if not path.is_file():
        raise EvalError(f"missing {path}; run the first round first")
    return json.loads(path.read_text())
