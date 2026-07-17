#!/usr/bin/env python3
"""Run a clean classifier that manages review slices through state scripts."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from review_state import DEFAULT_MODEL, DEFAULT_REASONING, ReviewState, ReviewStateError


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Let a clean Codex session contextually add and remove review slices."
    )
    parser.add_argument("--review-dir", required=True, type=Path)
    parser.add_argument("--user-directives-file", type=Path)
    parser.add_argument("--executor-context-file", type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--reasoning", default=DEFAULT_REASONING)
    args = parser.parse_args()

    try:
        review_dir = args.review_dir.resolve()
        with ReviewState.locked(review_dir) as state:
            root = Path(state.data["session"]["root"])
            target = dict(state.data["session"]["target"])
        prompt = _classifier_prompt(
            review_dir=review_dir,
            root=root,
            target=target,
            user_directives=_read_optional(args.user_directives_file),
            user_directives_file=(
                args.user_directives_file.resolve()
                if args.user_directives_file is not None
                else None
            ),
            executor_context=_read_optional(args.executor_context_file),
        )
        cmd = [
            "codex",
            "exec",
            "--ephemeral",
            "--sandbox",
            "workspace-write",
            "--skip-git-repo-check",
            "-C",
            str(review_dir),
            "-m",
            args.model,
            "-c",
            f'model_reasoning_effort="{args.reasoning}"',
            prompt,
        ]
        proc = subprocess.run(cmd, cwd=review_dir, check=False)
        if proc.returncode == 0:
            with ReviewState.locked(review_dir) as state:
                if not any(
                    not item.get("removed")
                    for item in state.data["slices"].values()
                ):
                    raise ReviewStateError(
                        "classifier completed without any active review slices"
                    )
        return proc.returncode
    except (OSError, ReviewStateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _classifier_prompt(
    *,
    review_dir: Path,
    root: Path,
    target: dict[str, str],
    user_directives: str,
    user_directives_file: Path | None,
    executor_context: str,
) -> str:
    skill_dir = Path(__file__).resolve().parents[1]
    add_slice = skill_dir / "scripts" / "add_slice.py"
    remove_slice = skill_dir / "scripts" / "remove_slice.py"
    rules = skill_dir / "references" / "classifier-rules.md"
    selection = skill_dir / "references" / "slice-selection.md"
    return f"""You are the clean slice classifier for a stateful multi-shot review.

Read completely:
- {rules}
- {selection}
- {review_dir / 'task.md'}
- {review_dir / '_state.json'}

Repository: {root}
Review target: {json.dumps(target, sort_keys=True)}

Inspect the target yourself with Git commands in the repository. Read changed code and every
applicable review-rule file discovered through the convention in slice-selection.md.

Work contextually. Existing active slices, removed slices, run records, and history are evidence.
Keep suitable slices. Add missing slices, remove obsolete ones, or reactivate removed slices by
adding the same name. Preserve explicit user-controlled slices unless the user directions below
explicitly authorize changing them.

Manage slices only by executing these scripts:
- add/reactivate: {add_slice}
- remove: {remove_slice}

Call them as many times as needed. For focused slices, send the complete reviewer prompt through
`--prompt-file -`. For native slices, pass the session target flag. Keep each slice narrow, but
group closely related lenses when the rules allow it. Prefer the smallest useful set of slices.
To revise an active classifier slice, remove it and then add the same name.

Normally omit `--user-directive-file`. If the supplemental user directions explicitly authorize
changing a user-controlled slice, pass this exact source file to the mutation:
{user_directives_file or "(none supplied)"}

Do not review or edit source code. Do not create a classification plan or JSON artifact.

Authoritative supplemental user directions:
{user_directives or "(none)"}

Advisory parent context:
{executor_context or "(none)"}

Finish after the ordinary state accurately represents the contextual slice selection. Briefly
summarize mutations in your final response.
"""


def _read_optional(path: Path | None) -> str:
    return "" if path is None else path.read_text(encoding="utf-8").strip()


if __name__ == "__main__":
    raise SystemExit(main())
