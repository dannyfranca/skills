from __future__ import annotations

import tempfile
import unittest
from collections import Counter
from pathlib import Path

from fixtures import finding, run, slice_, write_session

from eval_errors import EvalError
from sampling import Judgeability, SampleQuota, draw_sample, judgeability
from session_store import load_sessions


class SamplingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for index, variant in enumerate(("a", "a", "b")):
            kept = [finding(f"k{index}-{n}", severity) for n, severity in enumerate(["P1", "P3"] * 5)]
            rejected = [finding(f"x{index}-{n}", outcome="rejected") for n in range(4)]
            write_session(
                self.root,
                f"20261001-0000-{index:08x}",
                variant=variant,
                slices={"core": slice_(run(f"r{index}", findings=kept + rejected))},
            )
        sessions = load_sessions(self.root)
        self.arms = {"a": [s for s in sessions if s.variant == "a"], "b": [s for s in sessions if s.variant == "b"]}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def draw(self, seed: int = 1, exclude=frozenset(), quota=SampleQuota(kept=4, rejected=2)):
        return draw_sample(self.arms, quota=quota, seed=seed, exclude=exclude, judgeability_of=lambda s: Judgeability.UNJUDGEABLE)

    def test_negative_quota_is_rejected(self) -> None:
        for kept, rejected in ((-1, 0), (0, -1)):
            with self.subTest(kept=kept, rejected=rejected), self.assertRaises(EvalError):
                SampleQuota(kept=kept, rejected=rejected)

    def test_report_path_resolves_inside_the_session_directory(self) -> None:
        for session in self.arms["a"] + self.arms["b"]:
            for report in ("r0.md", "r1.md", "r2.md"):
                (session.directory / report).write_text("report")
        self.assertTrue(all(Path(entry.report_path).parent.name.startswith("2026") for entry in self.draw()))

    def test_quota_per_arm_and_class(self) -> None:
        sample = self.draw()
        counts = Counter((entry.arm, entry.finding_class.value) for entry in sample)
        self.assertEqual(counts, {("a", "kept"): 4, ("a", "rejected"): 2, ("b", "kept"): 4, ("b", "rejected"): 2})
        entry = sample[0]
        self.assertEqual(entry.slice_name, "core")
        self.assertIsNone(entry.report_path)
        self.assertEqual(entry.location, "src/lib.rs:1-2")
        self.assertTrue(entry.content)
        self.assertEqual(entry.judgeability, Judgeability.UNJUDGEABLE)

    def test_seeded_and_excludes_prior_entries(self) -> None:
        first = self.draw(seed=5)
        self.assertEqual(first, self.draw(seed=5))
        excluded = frozenset((entry.session_id, entry.finding_id) for entry in first)
        second = self.draw(seed=5, exclude=excluded)
        self.assertFalse(excluded & {(entry.session_id, entry.finding_id) for entry in second})

    def test_small_pools_return_what_exists(self) -> None:
        sample = self.draw(quota=SampleQuota(kept=50, rejected=50))
        counts = Counter((entry.arm, entry.finding_class.value) for entry in sample)
        self.assertEqual(counts[("b", "kept")], 10)
        self.assertEqual(counts[("b", "rejected")], 4)

    def test_severity_bias_prefers_p1(self) -> None:
        severities: Counter[str] = Counter()
        for seed in range(200):
            for entry in self.draw(seed=seed, quota=SampleQuota(kept=2, rejected=0)):
                severities[entry.severity] += 1
        self.assertGreater(severities["P1"], severities["P3"] * 2)


class IncompleteFindingsTests(unittest.TestCase):
    def test_sessions_with_missing_archives_are_not_sampled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            broken = write_session(
                root,
                "20261001-0000-00000001",
                slices={
                    "core": slice_(
                        run("r1", 1, findings=[finding("lost", "P1")], archived=True),
                        run("r2", 2, findings=[finding("readable", "P1"), finding("gone", outcome="rejected")]),
                    )
                },
            )
            (broken / "history" / "r1.json").unlink()
            write_session(
                root, "20261001-0000-00000002", slices={"core": slice_(run("r3", findings=[finding("ok", "P1")]))}
            )
            sample = draw_sample(
                {"a": load_sessions(root)},
                quota=SampleQuota(kept=10, rejected=10),
                seed=1,
                exclude=frozenset(),
                judgeability_of=lambda s: Judgeability.UNJUDGEABLE,
            )
        self.assertEqual([entry.finding_id for entry in sample], ["ok"])


class JudgeabilityTests(unittest.TestCase):
    def test_uncommitted_with_root_is_approximate_and_missing_root_unjudgeable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_session(root, "20261001-0000-aaaaaaaa", root=root)
            write_session(root, "20261001-0000-bbbbbbbb")
            first, second = load_sessions(root)
            self.assertEqual(judgeability(first), Judgeability.APPROXIMATE)
            self.assertEqual(judgeability(second), Judgeability.UNJUDGEABLE)


if __name__ == "__main__":
    unittest.main()
