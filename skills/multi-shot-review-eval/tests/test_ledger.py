from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import fixtures  # noqa: F401

from eval_errors import EvalError
from ledger import Decision, EvalRecord, Ledger


def record(
    eval_id: str, key: str = "tags:a|b", sessions=None, diff: float = 1.0, rel: float = 0.1, pooled=None
) -> EvalRecord:
    return EvalRecord(
        eval_id=eval_id,
        created_at="2026-10-01T00:00:00Z",
        contrast_key=key,
        window={"start": "s", "end": "e"},
        n={"a": 1, "b": 1},
        metrics={"kept_p0_p2": {"b": {"diff": diff, "rel_diff": rel, "ci95": [0, 2]}}},
        sessions=sessions or {"a": ["s1"], "b": ["s2"]},
        pooled_metrics=pooled or {},
    )


class LedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.tmp.name) / "_evals" / "ledger.jsonl")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_pooled_sessions_union_prior_records_with_same_key(self) -> None:
        self.ledger.append_eval(record("e1", sessions={"a": ["s1"], "b": ["s2"]}))
        self.ledger.append_eval(record("e2", key="tags:x|y", sessions={"x": ["s9"]}))
        self.ledger.append_eval(record("e3", sessions={"a": ["s1", "s3"], "b": ["s4"]}))
        pooled = self.ledger.pooled_sessions("tags:a|b")
        self.assertEqual(pooled, {"a": {"s1", "s3"}, "b": {"s2", "s4"}})
        self.assertEqual(self.ledger.eval_ids("tags:a|b"), ["e1", "e3"])

    def test_decisions_are_append_only_and_latest_wins(self) -> None:
        self.ledger.append_eval(record("e1", diff=1.0, rel=0.10))
        self.ledger.append_eval(record("e2", key="cutoff:r2", diff=2.0, rel=0.20))
        self.ledger.append_eval(record("e3", diff=5.0, rel=0.5))
        self.ledger.record_decision("e1", Decision.ADOPT, at="t1")
        self.ledger.record_decision("e2", Decision.CONTINUE, at="t2")
        self.ledger.record_decision("e2", Decision.ADOPT, at="t3")
        self.ledger.record_decision("e3", Decision.ADOPT, at="t4")
        self.ledger.record_decision("e3", Decision.REJECT, at="t5")
        lines = self.ledger.path.read_text().splitlines()
        self.assertEqual(len(lines), 8)
        gains = self.ledger.adopted_gains("kept_p0_p2")
        self.assertEqual([gain["eval_id"] for gain in gains["adopted"]], ["e1", "e2"])
        self.assertAlmostEqual(gains["total_diff"], 3.0)
        self.assertAlmostEqual(gains["compounded_rel_diff"], 1.1 * 1.2 - 1)

    def test_repeated_adoption_of_one_contrast_counts_once_with_pooled_estimate(self) -> None:
        self.ledger.append_eval(record("e1", diff=2.0, rel=0.2))
        pooled = {"kept_p0_p2": {"b": {"diff": 1.5, "rel_diff": 0.15, "ci95": [0, 3]}}}
        self.ledger.append_eval(record("e2", diff=2.0, rel=0.2, pooled=pooled))
        self.ledger.record_decision("e1", Decision.ADOPT, at="t1")
        self.ledger.record_decision("e2", Decision.ADOPT, at="t2")
        gains = self.ledger.adopted_gains("kept_p0_p2")
        self.assertEqual([gain["eval_id"] for gain in gains["adopted"]], ["e2"])
        self.assertAlmostEqual(gains["total_diff"], 1.5)
        self.assertAlmostEqual(gains["compounded_rel_diff"], 0.15)

    def test_decision_for_unknown_eval_fails(self) -> None:
        with self.assertRaises(EvalError):
            self.ledger.record_decision("nope", Decision.ADOPT, at="t")

    def test_missing_ledger_is_empty(self) -> None:
        self.assertEqual(self.ledger.pooled_sessions("tags:a|b"), {})
        self.assertEqual(self.ledger.adopted_gains("kept_p0_p2")["adopted"], [])


if __name__ == "__main__":
    unittest.main()
