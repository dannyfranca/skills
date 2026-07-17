#!/usr/bin/env python3
"""Tombstone a review slice while preserving its complete history."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from review_state import ReviewState, ReviewStateError


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove a slice while preserving its complete history.")
    parser.add_argument("--review-dir", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--user-directive-file",
        type=Path,
        help="Record the explicit user instruction authorizing this removal.",
    )
    args = parser.parse_args()
    try:
        directive = (
            args.user_directive_file.read_text(encoding="utf-8")
            if args.user_directive_file is not None
            else None
        )
        with ReviewState.locked(args.review_dir) as state:
            state.remove_slice(
                args.name,
                source="user" if directive is not None else "classifier",
                user_directive=directive,
            )
            state.save()
    except (OSError, ReviewStateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"removed slice {args.name}; history preserved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
