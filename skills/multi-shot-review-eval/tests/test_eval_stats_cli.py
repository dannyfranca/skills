from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from fixtures import claude_stdout, codex_stderr, finding, run, slice_, write_logs, write_session

from eval_stats import main


NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in keys(item)}
    return set()


class EvalStatsCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.reviews = base / "reviews"
        self.config = base / "msr.toml"
        self.config.write_text("[variants]\ndefault = 0\na = 1\nb = 1\n[variant.a]\n[variant.b]\n")
        for index in range(12):
            variant = "a" if index % 2 == 0 else "b"
            kept = 3 if variant == "a" else 5
            findings = [finding(f"k{index}-{n}", "P1" if n % 2 else "P2") for n in range(kept)]
            findings += [finding(f"x{index}-{n}", outcome="rejected") for n in range(2)]
            directory = write_session(
                self.reviews,
                f"20261001-{index:04d}-{index:08x}",
                variant=variant,
                created=index * 10,
                slices={"core": slice_(run(f"r{index}", findings=findings, start=index * 10 + 1))},
                history_end=index * 10 + 5,
            )
            write_logs(directory, f"r{index}-1-core", stderr=codex_stderr("1,000"))
            write_logs(directory, f"judge-{index}", stdout=claude_stdout(0.1))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def invoke(self, *args: str) -> tuple[int, dict]:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = main(["--review-root", str(self.reviews), "--config", str(self.config), *args], now=NOW)
        return code, json.loads(out.getvalue()) if out.getvalue() else {}

    def test_missing_invocation_logs_leave_usd_uncovered(self) -> None:
        for log in self.reviews.glob("*/*/*/_logs/judge-*"):
            log.unlink()
        _, summary = self.invoke("--seed", "11")
        stats = json.loads((Path(summary["eval_dir"]) / "stats.json").read_text())
        self.assertEqual(stats["arms"]["a"]["usage_coverage"]["sessions_with_usd"], 0)

    def test_full_cycle(self) -> None:
        code, summary = self.invoke("--seed", "11")
        self.assertEqual(code, 0)
        eval_dir = Path(summary["eval_dir"])
        self.assertEqual(eval_dir.parent, self.reviews / "_evals")
        self.assertEqual(eval_dir.name, "20261025-1200-a-vs-b")
        self.assertEqual([arm["name"] for arm in summary["arms"]], ["a", "b"])
        self.assertEqual([arm["n"] for arm in summary["arms"]], [5, 5])

        stats = json.loads((eval_dir / "stats.json").read_text())
        self.assertNotIn("verdict", keys(stats))
        self.assertNotIn("label", keys(stats))
        primary = stats["contrasts"]["b"]["kept_p0_p2"]
        self.assertAlmostEqual(primary["diff"], 2.0)
        self.assertEqual(primary["equivalence"], "outside")
        self.assertIn("mde", primary)
        self.assertIn("sessions_needed", primary)
        self.assertEqual(stats["arms"]["a"]["usage_coverage"]["sessions_with_usd"], 5)
        self.assertIn("repository", stats["strata"])
        stratum = stats["strata"]["repository"][0]["primary"]
        self.assertEqual(stratum["n"], {"a": 5, "b": 5})
        self.assertAlmostEqual(stratum["diff"]["b"], 2.0)
        self.assertTrue(stats["prices"]["assumptions"])
        self.assertEqual(stats["pooled"]["eval_ids"], [])
        rows = stats["session_rows"]["b"]
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["metrics"]["kept_p0_p2"], [5, 1])
        self.assertTrue(rows[0]["findings_complete"])
        self.assertEqual(stats["arms"]["b"]["sessions_missing_findings"], 0)
        self.assertEqual(summary["second_round_offer"], stats["second_round_offer"])
        self.assertIn("offer", stats["second_round_offer"]["b"])

        sample = json.loads((eval_dir / "sample.json").read_text())
        self.assertEqual(len([e for e in sample["entries"] if e["arm"] == "a" and e["finding_class"] == "kept"]), 15)
        self.assertEqual(len([e for e in sample["entries"] if e["finding_class"] == "rejected"]), 20)
        self.assertIn("approximate", sample["judgeability_notes"])

        ledger = (self.reviews / "_evals" / "ledger.jsonl").read_text().splitlines()
        self.assertEqual(len(ledger), 1)
        self.assertEqual(json.loads(ledger[0])["eval_id"], eval_dir.name)

        code, second = self.invoke("--seed", "11")
        self.assertEqual(code, 0)
        self.assertEqual(Path(second["eval_dir"]).name, "20261025-1200-a-vs-b-2")
        pooled = json.loads((Path(second["eval_dir"]) / "stats.json").read_text())["pooled"]
        self.assertEqual(pooled["eval_ids"], [eval_dir.name])
        self.assertEqual(pooled["n"], {"a": 5, "b": 5})

        code, decision = self.invoke("--record-decision", eval_dir.name, "adopt")
        self.assertEqual(code, 0)
        self.assertEqual(decision["decision"], "adopt")
        self.assertAlmostEqual(decision["adopted_gains"]["total_diff"], 2.0)

        code, round2 = self.invoke("--second-round", eval_dir.name)
        self.assertEqual(code, 0)
        self.assertIn("offer_condition", round2)
        round2_sample = json.loads((eval_dir / "sample-round2.json").read_text())
        first_ids = {(e["session_id"], e["finding_id"]) for e in sample["entries"]}
        second_ids = {(e["session_id"], e["finding_id"]) for e in round2_sample["entries"]}
        self.assertFalse(first_ids & second_ids)
        self.assertEqual(len([e for e in round2_sample["entries"] if e["arm"] == "b"]), 5)

    def test_unknown_decision_target_fails(self) -> None:
        code, _ = self.invoke("--record-decision", "nope", "adopt")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
