from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from eval_dir_fixture import analyst_document, load_entries, write_eval_dir

from agreement import cohens_kappa, main
from analyst_files import Verdict


V, I = Verdict.VALID, Verdict.INVALID


class KappaTests(unittest.TestCase):
    def test_known_values(self) -> None:
        cases = [
            ("symmetric", [(V, V)] * 4 + [(V, I)] + [(I, V)] + [(I, I)] * 4, 0.6),
            ("asymmetric margins", [(V, V)] * 2 + [(V, I)] * 3 + [(I, V)] + [(I, I)] * 4, 0.2),
            ("total disagreement", [(V, I)] * 5, 0.0),
        ]
        for name, pairs, expected in cases:
            with self.subTest(name):
                self.assertAlmostEqual(cohens_kappa(pairs), expected)

    def test_undefined(self) -> None:
        for name, pairs in [("no pairs", []), ("one verdict only", [(V, V)] * 6)]:
            with self.subTest(name):
                self.assertIsNone(cohens_kappa(pairs))


class AgreementCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.entries = load_entries()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def invoke(self, eval_dir: Path) -> tuple[int, dict, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main([str(eval_dir)])
        return code, json.loads(out.getvalue()) if out.getvalue() else {}, err.getvalue()

    def test_agreement_and_accuracy_without_synthesis(self) -> None:
        judged = [e for e in self.entries if e["judgeability"] != "unjudgeable"]
        flipped = {e["finding_id"] for e in judged if e["finding_class"] == "kept"}
        dissenter = analyst_document(
            "codex/gpt-6.1-sol",
            self.entries,
            verdict=lambda e: "unjudgeable" if e["judgeability"] == "unjudgeable" else "invalid",
        )
        eval_dir = write_eval_dir(self.root, analysts=[analyst_document("claude-code/claude-opus-5-5", self.entries), dissenter])
        (eval_dir / "synthesis.json").unlink()

        code, result, _ = self.invoke(eval_dir)

        self.assertEqual(code, 0)
        [pair] = result["agreement"]
        self.assertEqual(pair["analysts"], ["claude-code/claude-opus-5-5", "codex/gpt-6.1-sol"])
        self.assertEqual(pair["n"], len(judged))
        self.assertAlmostEqual(pair["agreement"], 1 - len(flipped) / len(judged))
        self.assertAlmostEqual(pair["kappa"], 0.0)
        kept_a = [e for e in judged if e["arm"] == "a" and e["finding_class"] == "kept"]
        rejected_a = [e for e in judged if e["arm"] == "a" and e["finding_class"] == "rejected"]
        self.assertEqual(
            result["sample_accuracy"]["a"]["claude-code/claude-opus-5-5"],
            {"kept_valid": 1.0, "kept_n": len(kept_a), "rejected_invalid": 1.0, "rejected_n": len(rejected_a)},
        )
        self.assertEqual(result["sample_accuracy"]["a"]["codex/gpt-6.1-sol"]["kept_valid"], 0.0)
        self.assertEqual(result["sample_accuracy"]["a"]["codex/gpt-6.1-sol"]["rejected_invalid"], 1.0)

    def test_single_analyst_has_no_pairs(self) -> None:
        eval_dir = write_eval_dir(self.root, analysts=[analyst_document("claude-code/claude-opus-5-5", self.entries)])
        code, result, _ = self.invoke(eval_dir)
        self.assertEqual(code, 0)
        self.assertEqual(result["agreement"], [])

    def test_contract_breach_exits_2(self) -> None:
        broken = analyst_document("claude-code/claude-opus-5-5", self.entries, verdict=lambda e: "maybe")
        code, _, err = self.invoke(write_eval_dir(self.root, analysts=[broken]))
        self.assertEqual(code, 2)
        self.assertIn("verdict", err)


if __name__ == "__main__":
    unittest.main()
