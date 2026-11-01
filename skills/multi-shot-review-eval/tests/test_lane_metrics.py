from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from fixtures import PRICES, finding, run, slice_, write_session

from findings import load_findings, run_lanes
from lane_metrics import LaneCounts, lane_counts, merge_lane_counts
from prices import load_price_table
from session_facts import FactsCache
from session_store import load_sessions


def lane_run(run_id: str, lane: str, findings: list[dict[str, Any]], pass_number: int = 1) -> dict[str, Any]:
    return {**run(run_id, pass_number, findings=findings), "lane": lane}


def grouped(finding_id: str, canonical_id: str, auto: Any = True) -> dict[str, Any]:
    item = finding(finding_id, outcome="duplicate")
    item["resolution"] = {"kind": "duplicate", "finding_id": canonical_id, "at": "2026-10-01T00:00:00.000Z", "auto": auto}
    return item


class LaneCountsTests(unittest.TestCase):
    def counts(self, *runs: dict[str, Any], **slice_options: Any) -> dict[str, LaneCounts]:
        with tempfile.TemporaryDirectory() as tmp:
            write_session(Path(tmp), "20261001-0000-aaaaaaaa", slices={"core": slice_(*runs, **slice_options)})
            session = load_sessions(Path(tmp))[0]
            return lane_counts(load_findings(session).findings, ran=run_lanes(session))

    def test_groups_credit_every_reporting_lane(self) -> None:
        counts = self.counts(
            lane_run("r1", "codex:a", [finding("f_1"), finding("f_2"), finding("f_3", outcome="rejected")]),
            lane_run("r2", "claude:b", [grouped("f_4", "f_1"), grouped("f_5", "f_3", auto="model"), finding("f_6")]),
        )
        self.assertEqual(counts["codex:a"], LaneCounts(kept=2, unique=1, shared=1, rejected=1))
        self.assertEqual(counts["claude:b"], LaneCounts(kept=2, unique=1, shared=1, rejected=1))

    def test_group_chains_to_its_last_kept_copy(self) -> None:
        counts = self.counts(
            lane_run("r1", "codex:a", [grouped("f_1", "f_2")]),
            lane_run("r2", "codex:a#2", [grouped("f_2", "f_3")]),
            lane_run("r3", "claude:b", [finding("f_3", outcome="rejected")]),
        )
        self.assertEqual(set(counts), {"codex:a", "codex:a#2", "claude:b"})
        self.assertTrue(all(value == LaneCounts(rejected=1) for value in counts.values()))

    def test_parent_duplicates_and_untriaged_findings_give_no_credit(self) -> None:
        manual = finding("f_2", outcome="duplicate")
        manual["resolution"]["finding_id"] = "f_1"
        counts = self.counts(
            lane_run("r1", "codex:a", [finding("f_1"), finding("f_3", outcome="open")]),
            lane_run("r2", "claude:b", [manual], pass_number=2),
        )
        self.assertEqual(counts, {"codex:a": LaneCounts(kept=1, unique=1), "claude:b": LaneCounts()})

    def test_runs_without_lanes_report_their_profile_as_one_lane(self) -> None:
        counts = self.counts(
            run("r1", 1, findings=[finding("f_1"), finding("f_2", outcome="rejected")]),
            run("r2", 2, findings=[finding("f_3")]),
            harness="codex",
            model="model-c",
        )
        self.assertEqual(counts, {"codex:model-c": LaneCounts(kept=2, unique=2, rejected=1)})

    def test_lanes_without_triaged_findings_report_zeros(self) -> None:
        counts = self.counts(run("r1", 1), harness="codex", model="model-c")
        self.assertEqual(counts, {"codex:model-c": LaneCounts()})

    def test_rejection_rate_needs_triaged_findings(self) -> None:
        self.assertIsNone(LaneCounts().as_dict()["rejection_rate"])
        self.assertEqual(LaneCounts(kept=3, rejected=1).as_dict()["rejection_rate"], 0.25)

    def test_merge_adds_lanes_across_sessions(self) -> None:
        merged = merge_lane_counts([{"a": LaneCounts(kept=1)}, {"a": LaneCounts(rejected=1), "b": LaneCounts(kept=2)}])
        self.assertEqual(merged, {"a": LaneCounts(kept=1, rejected=1), "b": LaneCounts(kept=2)})

    def test_partial_findings_contribute_no_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = write_session(
                Path(tmp),
                "20261001-0000-aaaaaaaa",
                slices={"core": slice_(run("r1", 1, findings=[finding("f_1")], archived=True))},
            )
            (directory / "history" / "r1.json").unlink()
            facts = FactsCache(load_price_table(PRICES)).get(load_sessions(Path(tmp))[0])
        self.assertEqual(facts.lanes, {})


if __name__ == "__main__":
    unittest.main()
