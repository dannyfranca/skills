#!/usr/bin/env python3
"""Tombstone a review slice at the user's explicit request."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from review_state import ReviewState, ReviewStateError


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove a slice while preserving its complete history.")
    parser.add_argument("--review-dir", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--user-directive-file", required=True, type=Path)
    args = parser.parse_args()
    try:
        directive = args.user_directive_file.read_text(encoding="utf-8")
        with ReviewState.locked(args.review_dir) as state:
            state.remove_slice(args.name, user_directive=directive)
            state.save()
    except (OSError, ReviewStateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"removed slice {args.name}; history preserved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
