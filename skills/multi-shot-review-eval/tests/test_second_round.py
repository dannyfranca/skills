from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fixtures import finding, run, slice_, write_session

from eval_errors import EvalError
from second_round import draw_second_round, second_round_offer
from session_store import load_sessions


def stats(baseline: dict, treatment: dict) -> dict:
    return {
        "baseline": "a",
        "primary_metric": "kept_p0_p2",
        "arms": {"a": {"metrics": baseline}, "b": {"metrics": treatment}},
        "contrasts": {"b": {}},
    }


def share(mean: float, low: float, high: float) -> dict:
    return {"mean": mean, "ci95": [low, high]}


class SecondRoundOfferTests(unittest.TestCase):
    def test_overlapping_kept_share_with_ten_point_gap_is_offered(self) -> None:
        offer = second_round_offer(
            stats({"rejected_share": share(0.30, 0.15, 0.45)}, {"rejected_share": share(0.41, 0.25, 0.55)})
        )
        self.assertTrue(offer["b"]["ci_overlap"])
        self.assertAlmostEqual(offer["b"]["kept_share_gap"], 0.11)
        self.assertTrue(offer["b"]["offer"])

    def test_primary_count_change_alone_is_not_offered(self) -> None:
        offer = second_round_offer(
            stats(
                {"rejected_share": share(0.30, 0.2, 0.4), "kept_p0_p2": share(10, 8, 12)},
                {"rejected_share": share(0.30, 0.2, 0.4), "kept_p0_p2": share(11, 9, 13)},
            )
        )
        self.assertFalse(offer["b"]["offer"])

    def test_separated_kept_share_is_not_offered(self) -> None:
        offer = second_round_offer(
            stats({"rejected_share": share(0.10, 0.05, 0.15)}, {"rejected_share": share(0.40, 0.35, 0.45)})
        )
        self.assertFalse(offer["b"]["ci_overlap"])
        self.assertFalse(offer["b"]["offer"])


class DrawSecondRoundTests(unittest.TestCase):
    def test_incomplete_sessions_are_not_sampled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reviews = Path(tmp) / "reviews"
            for index, completed in enumerate((True, False)):
                write_session(
                    reviews,
                    f"20261001-0000-0000000{index}",
                    created=index,
                    completed=completed,
                    slices={"core": slice_(run(f"r{index}", findings=[finding(f"k{index}", "P1")]))},
                )
            sessions = {session.id: session for session in load_sessions(reviews)}
            eval_dir = Path(tmp) / "eval"
            eval_dir.mkdir()
            (eval_dir / "stats.json").write_text(json.dumps({"arms": {"a": {"sessions": list(sessions)}}, "second_round_offer": {}}))
            (eval_dir / "sample.json").write_text(json.dumps({"entries": []}))
            draw_second_round(eval_dir, sessions, kept=5, seed=1)
            entries = json.loads((eval_dir / "sample-round2.json").read_text())["entries"]
            with self.assertRaises(EvalError):
                draw_second_round(eval_dir, sessions, kept=5, seed=2)
        self.assertEqual([entry["finding_id"] for entry in entries], ["k0"])


if __name__ == "__main__":
    unittest.main()
