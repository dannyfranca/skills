from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from review_result import (  # noqa: E402
    JUDGE_SCHEMA_PATH,
    ReviewResultError,
    parse_judge_verdict,
    render_review_markdown,
    validate_stored_finding,
)


class JudgeVerdictTests(unittest.TestCase):
    def test_parses_each_allowed_verdict(self) -> None:
        for verdict in ("continue", "stop"):
            with self.subTest(verdict=verdict):
                parsed = parse_judge_verdict(
                    json.dumps(
                        {"schema_version": 1, "verdict": verdict, "reason": "  Because.  "}
                    )
                )
                self.assertEqual(parsed, {"verdict": verdict, "reason": "Because."})

    def test_rejects_malformed_verdicts(self) -> None:
        invalid = (
            "not json",
            json.dumps([]),
            json.dumps({"schema_version": 1, "verdict": "stop"}),
            json.dumps({"schema_version": 1, "verdict": "stop", "reason": ""}),
            json.dumps({"schema_version": 1, "verdict": "maybe", "reason": "x"}),
            json.dumps({"schema_version": 2, "verdict": "stop", "reason": "x"}),
            json.dumps({"schema_version": 1, "verdict": "stop", "reason": "x", "extra": 1}),
        )
        for text in invalid:
            with self.subTest(text=text):
                with self.assertRaises(ReviewResultError):
                    parse_judge_verdict(text)

    def test_published_judge_schema_matches_parser(self) -> None:
        schema = json.loads(JUDGE_SCHEMA_PATH.read_text(encoding="utf-8"))

        self.assertEqual(sorted(schema["required"]), ["reason", "schema_version", "verdict"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["verdict"]["enum"], ["continue", "stop"])


def _duplicate(**resolution_extra) -> dict:
    return {
        "id": "f_abcdefgh",
        "severity": "P2",
        "title": "Naming",
        "content": "Rename it.",
        "location": {"path": "src/api.py", "start_line": 1, "end_line": 1},
        "status": "ignored",
        "resolution": {"kind": "duplicate", "finding_id": "f_canonica", "at": "now", **resolution_extra},
    }


class AutoDuplicateTests(unittest.TestCase):
    def render(self, finding: dict) -> str:
        return render_review_markdown(
            [validate_stored_finding(finding)],
            variant="default", harness="codex", harness_source="built-in-default", model=None,
            model_source="harness-default", reasoning=None, reasoning_source="harness-default",
        )

    def test_auto_marker_must_be_true(self) -> None:
        for value in (False, 1, "true", None):
            with self.subTest(value=value), self.assertRaises(ReviewResultError):
                validate_stored_finding(_duplicate(auto=value))

    def test_auto_duplicate_renders_the_automatic_wording(self) -> None:
        self.assertIn(
            "Ignored: Duplicate of `f_canonica` (detected automatically across same-wave shots).",
            self.render(_duplicate(auto=True)),
        )

    def test_manual_duplicate_keeps_the_plain_wording(self) -> None:
        markdown = self.render(_duplicate())
        self.assertIn("Ignored: Duplicate of `f_canonica`.", markdown)
        self.assertNotIn("automatically", markdown)


if __name__ == "__main__":
    unittest.main()
