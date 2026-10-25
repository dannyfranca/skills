from __future__ import annotations

import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from fixtures import BASE_TIME, finding, run, slice_, write_logs, write_session

from diff_size import DiffSize
from eval_errors import EvalError
from metrics import (
    METRICS,
    Direction,
    Margin,
    MarginKind,
    Ratio,
    apply_margin_overrides,
    session_metrics,
)
from contrast_stats import contrast
from metric_values import FINDING_METRICS
from session_store import load_sessions
from usage import SessionUsage


class SessionMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def completed_session(self):
        write_session(
            self.root,
            "20261001-0000-aaaaaaaa",
            slices={
                "core": slice_(
                    run(
                        "r1",
                        1,
                        findings=[
                            finding("k1", "P1"),
                            finding("k2", "P3"),
                            finding("x1", outcome="rejected"),
                            finding("d1", outcome="duplicate"),
                            finding("o1", outcome="open"),
                        ],
                    ),
                    run("r2", 2, findings=[finding("k3", "P0")], start=5),
                    run("r3", 2, status="timeout", start=20, minutes=5),
                    judgements=2,
                ),
                "docs": slice_(run("r4", 1, start=1)),
            },
            classifications=["failed", "succeeded"],
        )
        return load_sessions(self.root)[0]

    def test_completed_session_metrics(self) -> None:
        values = session_metrics(
            self.completed_session(),
            usage=SessionUsage(logs=2, logs_with_tokens=2, logs_with_usd=2, tokens=500, usd=1.5),
            diff_size=DiffSize(files=2, added=150, deleted=50),
        )
        expected = {
            "kept_p0_p2": Ratio(2, 1),
            "kept_p0_p2_per_100_lines": Ratio(200, 200),
            "pass1_kept_share": Ratio(1, 2),
            "rejected_share": Ratio(1, 4),
            "untriaged_per_session": Ratio(1, 1),
            "duplicates_per_session": Ratio(1, 1),
            "waves": Ratio(2, 1),
            "slices_past_cap": Ratio(0, 1),
            "judged_slices": Ratio(1, 1),
            "judge_runs": Ratio(2, 1),
            "wall_minutes": Ratio(25, 1),
            "busy_minutes": Ratio(13, 1),
            "tokens": Ratio(500, 1),
            "usd": Ratio(1.5, 1),
            "failure_rate": Ratio(1, 4),
            "classifier_retries": Ratio(1, 1),
            "incomplete_rate": Ratio(0, 1),
        }
        self.assertEqual(set(values), set(expected))
        for name, ratio in expected.items():
            with self.subTest(metric=name):
                self.assertAlmostEqual(values[name].numerator, ratio.numerator)
                self.assertAlmostEqual(values[name].denominator, ratio.denominator)

    def test_partial_usage_and_unknown_size_are_omitted(self) -> None:
        values = session_metrics(
            self.completed_session(),
            usage=SessionUsage(logs=3, logs_with_tokens=2, logs_with_usd=1, tokens=500, usd=1.5),
            diff_size=None,
        )
        for name in ("tokens", "usd", "kept_p0_p2_per_100_lines"):
            self.assertNotIn(name, values)

    def test_missing_archive_drops_finding_metrics(self) -> None:
        directory = write_session(
            self.root,
            "20261001-0000-cccccccc",
            slices={"core": slice_(run("r1", 1, findings=[finding("k1", "P1")], archived=True))},
        )
        (directory / "history" / "r1.json").unlink()
        values = session_metrics(load_sessions(self.root)[0], usage=None, diff_size=DiffSize(1, 10, 0))
        for name in FINDING_METRICS:
            self.assertNotIn(name, values)
        self.assertIn("waves", values)

    def test_removed_slice_findings_are_not_kept(self) -> None:
        write_session(
            self.root,
            "20261001-0000-dddddddd",
            slices={"core": slice_(run("r1", 1, findings=[finding("k1", "P1"), finding("g1", "P1", outcome="removed")]))},
        )
        values = session_metrics(load_sessions(self.root)[0], usage=None, diff_size=None)
        self.assertEqual(values["kept_p0_p2"], Ratio(1, 1))
        self.assertEqual(values["rejected_share"], Ratio(0, 1))

    def test_slices_past_cap_count_runs_after_a_continue_judgement(self) -> None:
        write_session(
            self.root,
            "20261001-0000-eeeeeeee",
            slices={
                "past": slice_(run("r1", 1), run("r2", 2), run("r3", 3), judged=((2, "continue"),)),
                "stopped": slice_(run("r4", 1), run("r5", 2), judged=((2, "stop"),)),
                "plain": slice_(run("r6", 1)),
            },
        )
        values = session_metrics(load_sessions(self.root)[0], usage=None, diff_size=None)
        self.assertEqual(values["slices_past_cap"], Ratio(1, 1))

    def test_reactivated_slice_ignores_continue_verdicts_of_its_old_definition(self) -> None:
        directory = write_session(
            self.root,
            "20261001-0000-abababab",
            slices={"core": slice_(run("r1", 1), run("r2", 2), run("r3", 3), judged=((2, "continue"),))},
        )
        state = json.loads((directory / "_state.json").read_text())
        core = state["slices"]["core"]
        core.update(definition_version=2, pass_base=2)
        core["runs"][2]["definition_version"] = 2
        (directory / "_state.json").write_text(json.dumps(state))
        values = session_metrics(load_sessions(self.root)[0], usage=None, diff_size=None)
        self.assertEqual(values["slices_past_cap"], Ratio(0, 1))

    def test_earlier_definition_past_its_cap_still_counts_after_reactivation(self) -> None:
        directory = write_session(
            self.root,
            "20261001-0000-acacacac",
            slices={"core": slice_(run("r1", 1), run("r2", 2), run("r3", 3), run("r4", 4), judged=((2, "continue"),))},
        )
        state = json.loads((directory / "_state.json").read_text())
        core = state["slices"]["core"]
        core.update(definition_version=2, pass_base=3)
        core["runs"][3]["definition_version"] = 2
        (directory / "_state.json").write_text(json.dumps(state))
        values = session_metrics(load_sessions(self.root)[0], usage=None, diff_size=None)
        self.assertEqual(values["slices_past_cap"], Ratio(1, 1))

    def test_busy_minutes_include_judge_execution(self) -> None:
        directory = write_session(self.root, "20261001-0000-ffffffff", slices={"core": slice_(run("r1", 1, minutes=2))})
        write_logs(directory, "judge-20261001-0010-2-core-abc123", modified=BASE_TIME + timedelta(minutes=14))
        write_logs(directory, "judge-7", modified=BASE_TIME + timedelta(minutes=99))
        values = session_metrics(load_sessions(self.root)[0], usage=None, diff_size=None)
        self.assertAlmostEqual(values["busy_minutes"].numerator, 2 + 1 + 4)

    def test_incomplete_session_only_counts_toward_incomplete_rate(self) -> None:
        write_session(self.root, "20261001-0000-bbbbbbbb", completed=False)
        values = session_metrics(load_sessions(self.root)[0], usage=None, diff_size=None)
        self.assertEqual(values, {"incomplete_rate": Ratio(1, 1)})


class MetricSpecTests(unittest.TestCase):
    def test_exactly_one_primary_metric(self) -> None:
        primary = [spec.name for spec in METRICS if spec.primary]
        self.assertEqual(primary, ["kept_p0_p2"])

    def test_default_margins(self) -> None:
        specs = {spec.name: spec for spec in METRICS}
        self.assertEqual(specs["kept_p0_p2"].margin, Margin(MarginKind.RELATIVE, 0.03))
        self.assertEqual(specs["rejected_share"].margin, Margin(MarginKind.ABSOLUTE, 0.02))
        self.assertEqual(specs["failure_rate"].margin, Margin(MarginKind.ABSOLUTE, 0.02))
        self.assertEqual(specs["usd"].margin, Margin(MarginKind.RELATIVE, 0.10))
        self.assertEqual(specs["rejected_share"].direction, Direction.LOWER)

    def test_overrides_keep_margin_kind(self) -> None:
        specs = {
            spec.name: spec
            for spec in apply_margin_overrides(METRICS, ["kept_p0_p2=5%", "rejected_share=0.03"])
        }
        self.assertEqual(specs["kept_p0_p2"].margin, Margin(MarginKind.RELATIVE, 0.05))
        self.assertEqual(specs["rejected_share"].margin, Margin(MarginKind.ABSOLUTE, 0.03))

    def test_bad_overrides_fail(self) -> None:
        for override in ("nope=1", "kept_p0_p2", "kept_p0_p2=-1", "kept_p0_p2=abc", "kept_p0_p2=nan", "kept_p0_p2=inf", "kept_p0_p2=inf%"):
            with self.subTest(override=override), self.assertRaises(EvalError):
                apply_margin_overrides(METRICS, [override])

    def test_increase_flags_worsening_only_for_lower_is_better_counts(self) -> None:
        for name, worsens in (("slices_past_cap", True), ("untriaged_per_session", True), ("duplicates_per_session", False)):
            with self.subTest(metric=name):
                spec = next(spec for spec in METRICS if spec.name == name)
                result = contrast([Ratio(0, 1)] * 10, [Ratio(1, 1)] * 10, spec, seed=1, resamples=500, key="k")
                self.assertEqual(result.worsening, worsens)

    def test_absolute_margin_value(self) -> None:
        self.assertAlmostEqual(Margin(MarginKind.RELATIVE, 0.1).absolute(-20), 2.0)
        self.assertAlmostEqual(Margin(MarginKind.ABSOLUTE, 0.02).absolute(5), 0.02)


if __name__ == "__main__":
    unittest.main()
