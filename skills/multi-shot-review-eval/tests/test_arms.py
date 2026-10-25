from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from fixtures import BASE_TIME, write_session

from arms import ArmRequest, PlanMode, plan_arms
from eval_errors import EvalError
from session_store import load_sessions


class ArmPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.counter = 0

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def add(self, variant: str, created: float, **kwargs) -> None:
        self.counter += 1
        write_session(self.root, f"20261001-0000-{self.counter:08x}", variant=variant, created=created, **kwargs)

    def names(self, arm) -> list[float]:
        return [(s.created_at - BASE_TIME).total_seconds() / 60 for s in arm.sessions]

    def test_active_tags_use_overlap_of_completed_sessions(self) -> None:
        for minute in (0, 100, 200):
            self.add("a", minute)
        for minute in (50, 150, 300):
            self.add("b", minute)
        self.add("b", 120, completed=False)
        self.add("b", 400, completed=False)
        plan = plan_arms(load_sessions(self.root), ("a", "b"), ArmRequest())
        self.assertEqual(plan.mode, PlanMode.TAGS)
        self.assertEqual(plan.window.start, BASE_TIME + timedelta(minutes=50))
        self.assertEqual(plan.window.end, BASE_TIME + timedelta(minutes=200))
        self.assertEqual([arm.name for arm in plan.arms], ["a", "b"])
        self.assertEqual(self.names(plan.arms[0]), [100, 200])
        self.assertEqual(self.names(plan.arms[1]), [50, 120, 150])
        self.assertEqual(plan.contrast_key, "tags:a|b")

    def test_single_active_tag_uses_latest_concurrent_window(self) -> None:
        for minute in (0, 10):
            self.add("old", minute)
        for minute in (5, 100, 200):
            self.add("a", minute)
        for minute in (90, 210):
            self.add("b", minute)
        self.add("c", 300)
        plan = plan_arms(load_sessions(self.root), ("c",), ArmRequest())
        self.assertEqual(plan.mode, PlanMode.LATEST_CONCURRENT)
        self.assertEqual([arm.name for arm in plan.arms], ["a", "b"])
        self.assertEqual(plan.window.start, BASE_TIME + timedelta(minutes=90))
        self.assertEqual(plan.window.end, BASE_TIME + timedelta(minutes=200))

    def test_latest_concurrent_ignores_tags_without_sessions_inside_the_window(self) -> None:
        for minute in (0, 100):
            self.add("a", minute)
        for minute in (50, 150):
            self.add("b", minute)
        for minute in (-10, 200):
            self.add("c", minute)
        plan = plan_arms(load_sessions(self.root), ("c",), ArmRequest())
        self.assertEqual([arm.name for arm in plan.arms], ["a", "b"])
        self.assertEqual(plan.window.start, BASE_TIME + timedelta(minutes=50))
        self.assertEqual(plan.window.end, BASE_TIME + timedelta(minutes=100))

    def test_overrides_filter_tags_window_and_repo(self) -> None:
        for minute in (0, 50, 100):
            self.add("a", minute)
            self.add("b", minute + 1)
        self.add("a", 60, repo="other/repo")
        request = ArmRequest(
            tags=("b", "a"),
            since=BASE_TIME + timedelta(minutes=10),
            until=BASE_TIME + timedelta(minutes=80),
            repo="owner/repo",
        )
        plan = plan_arms(load_sessions(self.root), ("x",), request)
        self.assertEqual([arm.name for arm in plan.arms], ["b", "a"])
        self.assertEqual(self.names(plan.arms[0]), [51])
        self.assertEqual(self.names(plan.arms[1]), [50])
        self.assertEqual(plan.contrast_key, "tags:b|a@owner/repo")

    def test_revision_cutoff_assigns_each_session_by_its_own_revision(self) -> None:
        self.add("a", 0, session_extra={"skill_revision": "r1"})
        self.add("b", 10)
        self.add("a", 20, session_extra={"skill_revision": "r2"})
        self.add("a", 30, session_extra={"skill_revision": "r1"})
        self.add("a", 40, session_extra={"skill_revision": "r3"})
        self.add("b", 50)
        plan = plan_arms(load_sessions(self.root), ("a",), ArmRequest(cutoff="r2"))
        self.assertEqual(plan.mode, PlanMode.CUTOFF)
        self.assertEqual([arm.name for arm in plan.arms], ["before", "after"])
        self.assertEqual(self.names(plan.arms[0]), [0, 10, 30])
        self.assertEqual(self.names(plan.arms[1]), [20])
        self.assertEqual(plan.window.start, BASE_TIME)
        self.assertEqual(plan.window.end, BASE_TIME + timedelta(minutes=30))
        self.assertEqual(plan.contrast_key, "cutoff:r2")

    def test_iso_cutoff_respects_tag_filter(self) -> None:
        self.add("a", 0)
        self.add("b", 5)
        self.add("a", 60)
        cutoff = (BASE_TIME + timedelta(minutes=30)).isoformat()
        plan = plan_arms(load_sessions(self.root), ("a",), ArmRequest(cutoff=cutoff, tags=("a",)))
        self.assertEqual(self.names(plan.arms[0]), [0])
        self.assertEqual(self.names(plan.arms[1]), [60])

    def test_unknown_revision_fails(self) -> None:
        self.add("a", 0)
        with self.assertRaises(EvalError):
            plan_arms(load_sessions(self.root), ("a",), ArmRequest(cutoff="nope"))

    def test_disjoint_tags_fail(self) -> None:
        self.add("a", 0)
        self.add("b", 100)
        with self.assertRaises(EvalError):
            plan_arms(load_sessions(self.root), ("a", "b"), ArmRequest())

    def test_window_without_completed_session_in_every_arm_fails(self) -> None:
        for minute in (0, 100):
            self.add("a", minute)
        for minute in (40, 60):
            self.add("b", minute)
        with self.assertRaises(EvalError):
            plan_arms(load_sessions(self.root), ("a", "b"), ArmRequest())

    def test_duplicate_tags_fail(self) -> None:
        self.add("a", 0)
        self.add("a", 10)
        with self.assertRaises(EvalError):
            plan_arms(load_sessions(self.root), ("x",), ArmRequest(tags=("a", "a")))

    def test_single_explicit_tag_picks_the_latest_window_that_includes_it(self) -> None:
        self.add("a", 0)
        self.add("b", 5)
        self.add("a", 10)
        self.add("b", 50)
        self.add("c", 50)
        plan = plan_arms(load_sessions(self.root), ("x",), ArmRequest(tags=("a",)))
        self.assertIn("a", [arm.name for arm in plan.arms])

    def test_one_tag_without_concurrency_fails(self) -> None:
        self.add("a", 0)
        with self.assertRaises(EvalError):
            plan_arms(load_sessions(self.root), ("a",), ArmRequest())


if __name__ == "__main__":
    unittest.main()
