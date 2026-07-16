#!/usr/bin/env python3
"""Initialize a state-managed review directory and print its path."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from review_state import ReviewStateError, init_review_state


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create .review/<timestamp-random>/ with an initialized _state.json file."
    )
    parser.add_argument(
        "--root",
        default=".",
        type=Path,
        help="Execution path where .review should be created. Defaults to the current directory.",
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--uncommitted", action="store_true", help="Review current working-tree changes (default).")
    target.add_argument("--base", help="Review changes against this base branch.")
    target.add_argument("--commit", help="Review changes introduced by this commit.")
    parser.add_argument("--task", help="Original user request text for this review session.")
    parser.add_argument(
        "--task-file",
        type=Path,
        help="Path to a Markdown/text file containing the original user request, or '-' for stdin.",
    )
    args = parser.parse_args()
    if (args.task is not None) == (args.task_file is not None):
        parser.error("choose exactly one task source: --task or --task-file")
    try:
        task = args.task
        if args.task_file is not None:
            task = sys.stdin.read() if str(args.task_file) == "-" else args.task_file.read_text(encoding="utf-8")
        review_target = (
            {"kind": "base", "value": args.base}
            if args.base is not None
            else {"kind": "commit", "value": args.commit}
            if args.commit is not None
            else {"kind": "uncommitted"}
        )
        review_dir = init_review_state(args.root, task or "", target=review_target)
    except (OSError, ReviewStateError) as exc:
        parser.error(str(exc))
    print(review_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
