#!/usr/bin/env python3
"""Mark one active finding as a duplicate of another open finding."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from review_state import ReviewState, ReviewStateError


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mark one active finding as a duplicate of an open canonical finding."
    )
    parser.add_argument("--review-dir", required=True, type=Path)
    parser.add_argument("--id", required=True, dest="finding_id")
    parser.add_argument("--canonical-id", required=True)
    args = parser.parse_args()
    try:
        with ReviewState.locked(args.review_dir) as state:
            _changed, message = state.dedupe_finding(
                args.finding_id, args.canonical_id
            )
            state.save()
    except (OSError, ReviewStateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
