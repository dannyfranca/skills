#!/usr/bin/env python3
"""Ignore one active review finding with an immutable reason."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from review_state import ReviewState, ReviewStateError


def main() -> int:
    parser = argparse.ArgumentParser(description="Ignore one active finding with a reason.")
    parser.add_argument("--review-dir", required=True, type=Path)
    parser.add_argument("--id", required=True, dest="finding_id")
    reason = parser.add_mutually_exclusive_group(required=True)
    reason.add_argument("--reason")
    reason.add_argument("--reason-file", type=Path)
    args = parser.parse_args()
    try:
        reason_text = (
            args.reason
            if args.reason is not None
            else args.reason_file.read_text(encoding="utf-8")
        )
        with ReviewState.locked(args.review_dir) as state:
            _changed, message = state.ignore_finding(args.finding_id, reason_text)
            state.save()
    except (OSError, UnicodeError, ReviewStateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
