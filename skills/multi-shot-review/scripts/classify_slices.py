#!/usr/bin/env python3
"""Run a clean classifier that manages review slices through state scripts."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from review_config import load_review_config
from harnesses import HarnessError, get_harness, resolve_profile
from review_instructions import load_classifier_guidance
from review_state import ReviewState, ReviewStateError


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Let a clean harness session contextually add and remove review slices."
    )
    parser.add_argument("--review-dir", required=True, type=Path)
    parser.add_argument("--user-directives-file", type=Path)
    parser.add_argument("--executor-context-file", type=Path)
    parser.add_argument("--harness")
    parser.add_argument("--model")
    parser.add_argument("--reasoning")
    args = parser.parse_args()

    try:
        review_dir = args.review_dir.resolve()
        with ReviewState.classifier_locked(review_dir):
            with ReviewState.locked(review_dir) as state:
                if state.recover_running_classifications():
                    state.save()
            return _run_classifier(args, review_dir)
    except (HarnessError, OSError, ReviewStateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _run_classifier(args: argparse.Namespace, review_dir: Path) -> int:
    """Resolve and run one classifier while the session lock is held."""

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
    profile = resolve_profile(
        config.classifier,
        harness=args.harness,
        model=args.model,
        reasoning=args.reasoning,
        override_source="slice-override",
    )
    skill_dir = Path(__file__).resolve().parents[1]
    invocation = get_harness(profile.harness).classifier_invocation(
        prompt=prompt,
        review_dir=review_dir,
        profile=profile,
        add_slice_script=skill_dir / "scripts" / "add_slice.py",
        remove_slice_script=skill_dir / "scripts" / "remove_slice.py",
    )
    with ReviewState.locked(review_dir) as state:
        classification_id = state.start_classification(profile)
        state.save()
    try:
        proc = subprocess.run(
            invocation.command,
            cwd=review_dir,
            input=invocation.input_text,
            text=True,
            check=False,
        )
    except OSError:
        with ReviewState.locked(review_dir) as state:
            state.complete_classification(classification_id, 127)
            state.save()
        raise
    except BaseException as exc:
        exit_code = 130 if isinstance(exc, KeyboardInterrupt) else 1
        with ReviewState.locked(review_dir) as state:
            state.complete_classification(classification_id, exit_code)
            state.save()
        raise
    with ReviewState.locked(review_dir) as state:
        effective_exit_code = proc.returncode
        if proc.returncode == 0 and not any(
            not item.get("removed")
            for item in state.data["slices"].values()
        ):
            effective_exit_code = 2
        state.complete_classification(classification_id, effective_exit_code)
        state.save()
    if effective_exit_code != proc.returncode:
        raise ReviewStateError(
            "classifier completed without any active review slices"
        )
    return proc.returncode


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

Each add may pass `--harness <harness>`, `--model <model>`, and/or `--reasoning <effort>` when a
specific choice materially suits that slice. Otherwise omit the option; the tool applies its
configured slice default or leaves the choice to the review harness. Scoped REVIEW guidance may
require one or more of these choices. Treat harness, model, and reasoning choices as part of the
durable slice definition, not as prompt text.

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
