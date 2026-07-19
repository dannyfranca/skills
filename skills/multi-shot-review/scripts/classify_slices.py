#!/usr/bin/env python3
"""Run a clean classifier that manages review slices through state scripts."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from review_config import load_review_config
from review_instructions import load_classifier_guidance
from review_state import DEFAULT_REASONING, ReviewState, ReviewStateError


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Let a clean Codex session contextually add and remove review slices."
    )
    parser.add_argument("--review-dir", required=True, type=Path)
    parser.add_argument("--user-directives-file", type=Path)
    parser.add_argument("--executor-context-file", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--reasoning", default=DEFAULT_REASONING)
    args = parser.parse_args()

    try:
        review_dir = args.review_dir.resolve()
        with ReviewState.locked(review_dir) as state:
            root = Path(state.data["session"]["root"])
            target = dict(state.data["session"]["target"])
        config = load_review_config(root)
        review_instructions = load_classifier_guidance(
            root,
            target,
            review_file=config.review_file,
        )
        prompt = _classifier_prompt(
            review_dir=review_dir,
            root=root,
            target=target,
            review_instructions=review_instructions,
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
        ]
        classifier_model = (
            args.model if args.model is not None else config.classifier_model
        )
        if classifier_model is not None and not classifier_model.strip():
            raise ReviewStateError("classifier model must be a non-empty string")
        if classifier_model is not None:
            cmd.extend(["-m", classifier_model])
        cmd.extend(
            [
                "-c",
                f'model_reasoning_effort="{args.reasoning}"',
                "-c",
                "project_doc_fallback_filenames=[]",
                prompt,
            ]
        )
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
    review_instructions: str,
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

Inspect the target yourself with Git commands in the repository. Read changed code and applicable
repository rules described by slice-selection.md.

The tooling may provide already-resolved scoped guidance below. Treat it as classifier-only. Use it
to select slices and, when material, translate only relevant concrete requirements into focused
slice prompts. Never tell a reviewer to load the source guidance, copy it wholesale into a slice
prompt, or assume a native slice receives it. When this guidance must govern reviewer behavior, use
a focused slice prompt.

Additional scoped guidance:
{review_instructions}

Manage slices only by executing these scripts:
- add/reactivate: {add_slice}
- remove: {remove_slice}

Call them as many times as needed. For focused slices, send the complete reviewer prompt through
`--prompt-file -`. For native slices, pass the session target flag.

Each add may pass `--model <model>` when a specific model materially suits that slice. Otherwise
omit `--model`; the tool applies the configured slice default or leaves model choice to the review
harness. Treat model choice as part of the durable slice definition, not as prompt text.

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
