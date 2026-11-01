from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from fixtures import PRICES, at, claude_stdout, finding, run, slice_, write_logs, write_session

from findings import load_findings
from prices import load_price_table
from session_facts import FactsCache
from session_store import Session, load_sessions
from setting_metrics import DedupeCounts, JudgeCounts, SeverityCounts, ShotCounts, ShotYield, judge_counts, severity_counts, shot_yield
from usage import session_usage


def shot_run(run_id: str, shot: int, findings: list[dict[str, Any]], pass_number: int = 1) -> dict[str, Any]:
    return {**run(run_id, pass_number, findings=findings), "shot": shot}


def grouped(finding_id: str, canonical_id: str) -> dict[str, Any]:
    item = finding(finding_id, outcome="duplicate")
    item["resolution"] = {"kind": "duplicate", "finding_id": canonical_id, "at": at(0), "auto": True}
    return item


def dedupe_event(name: str, duplicates: int = 0) -> dict[str, Any]:
    return {"event": name, "slice": "core", "pass": 1, "duplicates": duplicates, "at": at(5)}


class SettingMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def session(self, *runs: dict[str, Any], history: list[dict[str, Any]] | None = None, **slice_options: Any) -> Session:
        self.directory = write_session(self.root, "20261001-0000-aaaaaaaa", slices={"core": slice_(*runs, **slice_options)}, events=history)
        return load_sessions(self.root)[0]

    def test_shots_count_groups_and_extra_only_kept(self) -> None:
        session = self.session(
            shot_run("r1", 1, [finding("f_1"), finding("f_2", outcome="rejected")]),
            shot_run("r2", 2, [grouped("f_3", "f_1"), finding("f_4"), finding("f_5", outcome="rejected")]),
            shot_run("r3", 3, []),
        )
        result = shot_yield(session, load_findings(session).findings)
        self.assertEqual(
            result.by_shot,
            {"1": ShotCounts(runs=1, kept=1, rejected=1), "2": ShotCounts(runs=1, kept=2, rejected=1), "3": ShotCounts(runs=1)},
        )
        self.assertEqual(result.extra_only_kept, 1)

    def test_shot_yields_add_across_sessions(self) -> None:
        left = ShotYield({"1": ShotCounts(runs=1, kept=1)}, 0)
        right = ShotYield({"1": ShotCounts(runs=1), "2": ShotCounts(runs=1, kept=1)}, 1)
        self.assertEqual(
            (left + right).as_dict(),
            {"by_shot": {"1": {"runs": 2, "kept": 1, "rejected": 0}, "2": {"runs": 1, "kept": 1, "rejected": 0}}, "extra_only_kept": 1},
        )

    def test_judge_continue_pays_off_when_its_definition_keeps_later(self) -> None:
        session = self.session(
            run("r1", 3, findings=[finding("f_1")]),
            run("r2", 4, findings=[finding("f_2"), finding("f_3", outcome="rejected")]),
            judged=((3, "continue"), (6, "continue"), (6, "stop")),
        )
        counts = judge_counts(session, load_findings(session).findings)
        self.assertEqual(counts, JudgeCounts(continues=2, stops=1, continues_with_kept=1, kept_after_continue=1))
        self.assertEqual(counts.as_dict(), {"continue": 2, "stop": 1, "continue_with_kept": 1, "kept_after_continue": 1})

    def test_judge_ignores_kept_findings_of_another_definition(self) -> None:
        session = self.session(
            {**run("r1", 4, findings=[finding("f_1")]), "definition_version": 2},
            judged=((3, "continue"),),
        )
        self.assertEqual(judge_counts(session, load_findings(session).findings), JudgeCounts(continues=1))

    def test_severity_counts_every_level(self) -> None:
        session = self.session(
            run("r1", 1, findings=[finding("f_1", "P0"), finding("f_2", "P3", outcome="rejected"), finding("f_3", "P1", outcome="open")]),
        )
        counts = severity_counts(load_findings(session).findings)
        self.assertEqual(
            counts,
            {"P0": SeverityCounts(kept=1), "P1": SeverityCounts(), "P2": SeverityCounts(), "P3": SeverityCounts(rejected=1)},
        )

    def test_dedupe_counts_events_and_log_cost(self) -> None:
        session = self.session(
            run("r1", 1, findings=[finding("f_1")]),
            history=[dedupe_event("dedupe_completed", duplicates=2), dedupe_event("dedupe_failed")],
        )
        write_logs(self.directory, "r1-1-core", stdout=claude_stdout(0.5))
        write_logs(self.directory, "classifier-c0", stdout=claude_stdout(0.1))
        write_logs(self.directory, "dedupe-20261001T000000Z-1-core-aaaaaa", stdout=claude_stdout(0.2))
        facts = FactsCache(load_price_table(PRICES)).get(session)
        self.assertEqual((facts.dedupe.runs, facts.dedupe.failed, facts.dedupe.merged, facts.dedupe.usd), (2, 1, 2, None))
        self.assertFalse(facts.usage.usd_complete)

        write_logs(self.directory, "dedupe-20261001T000000Z-1-core-bbbbbb", stdout=claude_stdout(0.3))
        facts = FactsCache(load_price_table(PRICES)).get(session)
        self.assertAlmostEqual(facts.dedupe.usd, 0.5)
        self.assertTrue(facts.usage.usd_complete)
        self.assertAlmostEqual(facts.metrics["kept_p0_p2_per_usd"].denominator, 1.1)

    def test_dedupe_log_without_event_counts_as_run(self) -> None:
        session = self.session(run("r1", 1, findings=[finding("f_1")]))
        write_logs(self.directory, "dedupe-20261001T000000Z-1-core-aaaaaa", stdout=claude_stdout(0.2))
        facts = FactsCache(load_price_table(PRICES)).get(session)
        self.assertEqual((facts.dedupe.runs, facts.dedupe.failed, facts.dedupe.merged), (1, 0, 0))
        self.assertAlmostEqual(facts.dedupe.usd, 0.2)

    def test_dedupe_cost_is_zero_without_dedupe_runs(self) -> None:
        session = self.session(run("r1", 1))
        facts = FactsCache(load_price_table(PRICES)).get(session)
        self.assertEqual(facts.dedupe, DedupeCounts())

    def test_partial_findings_contribute_no_settings(self) -> None:
        session = self.session(run("r1", 1, findings=[finding("f_1")], archived=True), history=[dedupe_event("dedupe_completed", 1)])
        (self.directory / "history" / "r1.json").unlink()
        facts = FactsCache(load_price_table(PRICES)).get(session)
        self.assertEqual((facts.shots, facts.dedupe, facts.judge, facts.severity), (ShotYield(), DedupeCounts(), JudgeCounts(), {}))


if __name__ == "__main__":
    unittest.main()
