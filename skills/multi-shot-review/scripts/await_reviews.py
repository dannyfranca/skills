#!/usr/bin/env python3
"""Wait for the currently running review wave without starting another."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from review_state import ReviewStateError, await_reviews


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Await the currently running review wave without reserving review work."
    )
    parser.add_argument("--review-dir", required=True, type=Path)
    parser.add_argument("--pretty-json", action="store_true", help="Pretty-print the final summary JSON.")
    args = parser.parse_args()

    try:
        rc, _summary = await_reviews(
            args.review_dir,
            stdout_json=True,
            pretty_json=args.pretty_json,
        )
        return rc
    except ReviewStateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
