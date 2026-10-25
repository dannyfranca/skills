from __future__ import annotations

import unittest

import fixtures  # noqa: F401

from contrast_stats import Equivalence, arm_summary, contrast
from metrics import Direction, Margin, MarginKind, MetricSpec, Ratio


def spec(direction: Direction = Direction.HIGHER, *, primary: bool = False, margin: float = 0.03) -> MetricSpec:
    return MetricSpec(
        name="m",
        direction=direction,
        margin=Margin(MarginKind.RELATIVE, margin),
        primary=primary,
    )


def constant(value: float, n: int) -> list[Ratio]:
    return [Ratio(value, 1)] * n


def noisy(values: list[float]) -> list[Ratio]:
    return [Ratio(value, 1) for value in values]


NOISY_A = noisy([3, 7, 1, 9, 4, 6, 2, 8, 5, 5, 0, 10])
NOISY_B = noisy([4, 6, 2, 9, 5, 7, 1, 8, 6, 3, 2, 9])


class ArmSummaryTests(unittest.TestCase):
    def test_ratio_of_sums(self) -> None:
        summary = arm_summary([Ratio(1, 2), Ratio(3, 2)], seed=1, resamples=200, key="k")
        self.assertEqual(summary.n, 2)
        self.assertAlmostEqual(summary.mean, 1.0)
        self.assertLessEqual(summary.ci95[0], summary.mean)
        self.assertGreaterEqual(summary.ci95[1], summary.mean)

    def test_empty_arm_has_no_mean(self) -> None:
        summary = arm_summary([], seed=1, resamples=200, key="k")
        self.assertEqual(summary.n, 0)
        self.assertIsNone(summary.mean)


class ContrastTests(unittest.TestCase):
    def test_identical_constant_arms_are_inside_margin(self) -> None:
        result = contrast(constant(10, 40), constant(10, 40), spec(), seed=7, resamples=2000, key="k")
        self.assertEqual(result.diff, 0)
        self.assertEqual(result.rel_diff, 0)
        self.assertEqual(result.ci95, (0, 0))
        self.assertEqual(result.equivalence, Equivalence.INSIDE)
        self.assertAlmostEqual(result.margin_abs, 0.3)

    def test_clear_improvement_is_outside_margin(self) -> None:
        result = contrast(constant(10, 40), constant(20, 40), spec(), seed=7, resamples=2000, key="k")
        self.assertEqual(result.diff, 10)
        self.assertAlmostEqual(result.rel_diff, 1.0)
        self.assertEqual(result.equivalence, Equivalence.OUTSIDE)
        self.assertEqual(result.p_worse, 0)
        self.assertFalse(result.worsening)

    def test_noisy_small_samples_are_undetermined_with_mde_and_needed_sessions(self) -> None:
        result = contrast(NOISY_A, NOISY_B, spec(), seed=7, resamples=2000, key="k")
        self.assertEqual(result.equivalence, Equivalence.UNDETERMINED)
        self.assertGreater(result.mde, result.margin_abs)
        self.assertGreater(result.sessions_needed, 12)
        self.assertLess(result.ci90[1] - result.ci90[0], result.ci95[1] - result.ci95[0])

    def test_sessions_needed_weights_each_arm_by_its_own_variance(self) -> None:
        # Only the 2-session baseline varies (bootstrap mean variance 0.5); balanced n = z^2 * 0.5 * 2 / 0.5^2 ≈ 31.
        absolute = MetricSpec("m", Direction.HIGHER, Margin(MarginKind.ABSOLUTE, 0.5))
        for baseline, treatment in ((noisy([0, 2]), constant(1, 100)), (constant(1, 100), noisy([0, 2]))):
            with self.subTest(n_baseline=len(baseline)):
                result = contrast(baseline, treatment, absolute, seed=7, resamples=4000, key="k")
                self.assertGreaterEqual(result.sessions_needed, 26)
                self.assertLessEqual(result.sessions_needed, 37)

    def test_seed_makes_results_reproducible(self) -> None:
        first = contrast(NOISY_A, NOISY_B, spec(), seed=3, resamples=2000, key="k")
        second = contrast(NOISY_A, NOISY_B, spec(), seed=3, resamples=2000, key="k")
        self.assertEqual(first, second)

    def test_secondary_worsening_flag(self) -> None:
        worse = contrast(constant(10, 30), constant(20, 30), spec(Direction.LOWER), seed=1, resamples=2000, key="k")
        self.assertEqual(worse.p_worse, 1)
        self.assertTrue(worse.worsening)

    def test_primary_and_neutral_metrics_never_flag_worsening(self) -> None:
        primary = contrast(constant(20, 30), constant(10, 30), spec(primary=True), seed=1, resamples=2000, key="k")
        self.assertEqual(primary.p_worse, 1)
        self.assertFalse(primary.worsening)
        neutral = contrast(constant(10, 30), constant(20, 30), spec(Direction.NEUTRAL), seed=1, resamples=2000, key="k")
        self.assertIsNone(neutral.p_worse)
        self.assertFalse(neutral.worsening)

    def test_zero_baseline_has_no_relative_values(self) -> None:
        result = contrast(constant(0, 10), constant(1, 10), spec(), seed=1, resamples=500, key="k")
        self.assertIsNone(result.rel_diff)
        self.assertEqual(result.margin_abs, 0)
        self.assertIsNone(result.sessions_needed)

    def test_zero_baseline_with_relative_margin_is_undetermined(self) -> None:
        result = contrast(constant(0, 10), constant(0, 10), spec(), seed=1, resamples=500, key="k")
        self.assertEqual(result.equivalence, Equivalence.UNDETERMINED)

    def test_singleton_arm_has_point_estimate_but_no_uncertainty(self) -> None:
        result = contrast(constant(10, 1), constant(10, 5), spec(), seed=1, resamples=500, key="k")
        self.assertEqual(result.diff, 0)
        self.assertIsNone(result.ci95)
        self.assertIsNone(result.ci90)
        self.assertIsNone(result.mde)
        self.assertIsNone(result.sessions_needed)
        self.assertIsNone(result.p_worse)
        self.assertEqual(result.equivalence, Equivalence.UNDETERMINED)

    def test_missing_arm_values_give_empty_contrast(self) -> None:
        result = contrast([], constant(1, 10), spec(), seed=1, resamples=500, key="k")
        self.assertIsNone(result.diff)
        self.assertEqual(result.equivalence, Equivalence.UNDETERMINED)


if __name__ == "__main__":
    unittest.main()
