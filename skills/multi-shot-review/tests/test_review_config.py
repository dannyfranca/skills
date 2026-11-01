from __future__ import annotations

import os
import random
import sys
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from review_config import (  # noqa: E402
    ReviewConfig,
    ReviewerLane,
    load_explicit_review_config,
    load_review_config,
    load_reviewers_file,
    parse_max_parallel,
    resolve_max_parallel,
)
from harnesses import HarnessProfile  # noqa: E402
from review_state import ReviewStateError  # noqa: E402


class ReviewConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.root = self.home / "work" / "repo"
        self.root.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_config(self, directory: Path, text: str) -> None:
        agents = directory / ".agents"
        agents.mkdir()
        (agents / "multi-shot-review.toml").write_text(text, encoding="utf-8")

    def test_explicit_file_bypasses_live_chain(self) -> None:
        self.write_config(self.home, 'max_passes = 9\n')
        explicit = self.home / 'arm.toml'
        explicit.write_text('max_passes = 2\n')
        self.assertEqual(load_explicit_review_config(explicit).max_passes, 2)
        self.assertEqual(load_review_config(self.root, home=self.home).max_passes, 9)
        with self.assertRaises(ReviewStateError):
            load_explicit_review_config(self.home / 'missing.toml')

    def test_defaults_every_setting_when_chain_is_empty(self) -> None:
        config = load_review_config(self.root, home=self.home)

        self.assertEqual(config.review_file, "REVIEW")
        self.assertIsNone(config.classifier)
        self.assertIsNone(config.slice_default)

    def test_nearest_atomic_profile_replaces_parent_profile(self) -> None:
        self.write_config(
            self.home,
            'review_file = "HOME_REVIEW"\n'
            '[classifier]\n'
            'harness = "codex"\n'
            'model = "global-classifier"\n'
            'reasoning = "medium"\n',
        )
        self.write_config(
            self.home / "work",
            '[classifier]\n'
            'harness = "claude-code"\n'
            'model = "work-classifier"\n'
            '[slice_default]\n'
            'harness = "codex"\n'
            'model = "work-slice"\n'
            'reasoning = "high"\n',
        )
        self.write_config(
            self.root,
            'review_file = "REPO_REVIEW"\n',
        )

        config = load_review_config(self.root, home=self.home)

        self.assertEqual(config.review_file, "REPO_REVIEW")
        self.assertEqual(
            config.classifier,
            HarnessProfile(harness="claude-code", model="work-classifier"),
        )
        self.assertEqual(
            config.slice_default,
            HarnessProfile(harness="codex", model="work-slice", reasoning="high"),
        )

    def test_review_root_defaults_to_reviews_under_home(self) -> None:
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            config = load_review_config(self.root, home=self.home)

        self.assertEqual(config.review_root, self.home / ".reviews")

    def test_review_root_expands_home_and_requires_an_absolute_path(self) -> None:
        self.write_config(self.home, 'review_root = "~/reviews"\n')

        config = load_review_config(self.root, home=self.home)

        self.assertEqual(config.review_root, Path("~/reviews").expanduser())
        self.assertNotIn("review_root", config.to_snapshot())
        for text in ('review_root = "reviews"\n', 'review_root = ""\n', "review_root = 1\n"):
            with self.subTest(text=text):
                (self.home / ".agents" / "multi-shot-review.toml").write_text(text, encoding="utf-8")
                with self.assertRaises(ReviewStateError):
                    load_review_config(self.root, home=self.home)

    def test_max_passes_and_judge_profile_are_loaded(self) -> None:
        self.write_config(
            self.root,
            'max_passes = 2\n'
            '[classifier]\n'
            'harness = "codex"\n'
            'model = "classifier-model"\n'
            '[judge]\n'
            'harness = "claude-code"\n'
            'model = "judge-model"\n'
            'reasoning = "low"\n',
        )

        config = load_review_config(self.root, home=self.home)

        self.assertEqual(config.max_passes, 2)
        self.assertEqual(
            config.judge_profile,
            HarnessProfile(harness="claude-code", model="judge-model", reasoning="low"),
        )

    def test_judge_profile_falls_back_to_classifier_profile(self) -> None:
        self.write_config(
            self.root,
            '[classifier]\n'
            'harness = "codex"\n'
            'model = "classifier-model"\n'
            'reasoning = "medium"\n',
        )

        config = load_review_config(self.root, home=self.home)

        self.assertEqual(config.max_passes, 3)
        self.assertEqual(config.reviewers, ())
        self.assertIsNone(config.judge)
        self.assertEqual(config.judge_profile, config.classifier)

    def test_nearer_pass_budget_and_judge_profile_replace_parent_values(self) -> None:
        self.write_config(
            self.home,
            'max_passes = 5\n'
            '[judge]\n'
            'harness = "codex"\n'
            'model = "global-judge"\n'
            'reasoning = "high"\n',
        )
        self.write_config(
            self.root,
            'max_passes = 2\n'
            '[judge]\n'
            'harness = "claude-code"\n',
        )

        config = load_review_config(self.root, home=self.home)

        self.assertEqual(config.max_passes, 2)
        self.assertEqual(config.judge_profile, HarnessProfile(harness="claude-code"))

    def test_eval_table_is_left_to_the_eval_skill(self) -> None:
        self.write_config(self.root, 'max_passes = 2\n[[eval.analysts]]\nharness = "codex"\nmodel = "m"\n')

        self.assertEqual(load_review_config(self.root, home=self.home).max_passes, 2)

    def test_rejects_unknown_non_string_empty_and_path_settings(self) -> None:
        invalid_configs = (
            "unknown = true\n",
            "max_passes = 0\n",
            "max_passes = true\n",
            'max_passes = "3"\n',
            "reviewers = 1\n",
            '[reviewers]\nharness = "codex"\n',
            "[[reviewers]]\n",
            '[[reviewers]]\nharness = "unknown"\n',
            '[[reviewers]]\nharness = "codex"\nunknown = 1\n',
            '[[reviewers]]\nharness = "codex"\nmax_passes = 0\n',
            '[[reviewers]]\nharness = "codex"\npasses = false\n',
            '[[reviewers]]\nharness = "codex"\nfrom_pass = 1.5\n',
            '[[reviewers]]\nharness = "codex"\nrequired = "yes"\n',
            '[[reviewers]]\nharness = "codex"\nretire_when_clean = 1\n',
            '[[reviewers]]\nharness = "codex"\nname = ""\n',
            '[[reviewers]]\nharness = "codex"\nfrom_pass = 2\n',
            '[judge]\nharness = ""\n',
            '[judge]\nharness = "codex"\nunknown = true\n',
            "classifier_model = 5\n",
            'classifier = "codex"\n',
            '[slice_default]\nharness = ""\n',
            '[classifier]\nharness = "unknown"\n',
            '[classifier]\nharness = "codex"\nunknown = true\n',
            'review_file = "REVIEW.md"\n',
            'review_file = "../REVIEW"\n',
        )
        for index, text in enumerate(invalid_configs):
            with self.subTest(text=text):
                directory = self.root / str(index)
                directory.mkdir()
                self.write_config(directory, text)
                with self.assertRaises(ReviewStateError):
                    load_review_config(directory, home=self.home)

    def test_rejects_invalid_utf8_as_a_config_error(self) -> None:
        agents = self.root / ".agents"
        agents.mkdir()
        (agents / "multi-shot-review.toml").write_bytes(b"\xff")

        with self.assertRaisesRegex(ReviewStateError, "could not read review config"):
            load_review_config(self.root, home=self.home)

    def drawn_tags(self, directory: Path, draws: int = 400) -> set[str]:
        rng = random.Random(7)
        return {
            load_review_config(directory, home=self.home, rng=rng).variant for _ in range(draws)
        }

    def test_without_variants_every_session_is_the_default_variant(self) -> None:
        self.write_config(self.root, "max_passes = 2\n")

        self.assertEqual(self.drawn_tags(self.root), {"default"})
        self.assertEqual(load_review_config(self.root, home=self.home).max_passes, 2)

    def test_variant_bodies_without_weights_split_equally_with_default(self) -> None:
        self.write_config(
            self.root,
            "max_passes = 1\n"
            "[variant.a]\n"
            "max_passes = 2\n"
            "[variant.b]\n"
            "max_passes = 3\n",
        )
        rng = random.Random(7)
        counts = {"default": 0, "a": 0, "b": 0}
        for _ in range(3000):
            counts[load_review_config(self.root, home=self.home, rng=rng).variant] += 1

        for tag, count in counts.items():
            with self.subTest(tag=tag):
                self.assertGreater(count, 850)
                self.assertLess(count, 1150)

    def test_integer_weights_are_relative_and_zero_excludes_a_variant_from_the_draw(self) -> None:
        self.write_config(
            self.root,
            "[variants]\n"
            "default = 0\n"
            "a = 3\n"
            "b = 0\n"
            "[variant.a]\n"
            "max_passes = 2\n"
            "[variant.b]\n"
            "max_passes = 3\n"
            "[variant.c]\n"
            "max_passes = 4\n",
        )

        self.assertEqual(self.drawn_tags(self.root), {"a", "c"})

    def test_huge_integer_weights_draw_exactly(self) -> None:
        self.write_config(
            self.root,
            f"[variants]\ndefault = 1{'0' * 400}\na = 1\n[variant.a]\nmax_passes = 2\n",
        )

        self.assertEqual(self.drawn_tags(self.root, draws=50), {"default"})

    def test_weight_beyond_the_integer_digit_limit_is_a_config_error(self) -> None:
        self.write_config(self.root, f"[variants]\na = 1{'0' * 5000}\n[variant.a]\n")

        with self.assertRaisesRegex(ReviewStateError, "could not read review config"):
            load_review_config(self.root, home=self.home)

    def test_variant_body_overrides_main_settings_and_replaces_whole_profiles(self) -> None:
        self.write_config(
            self.root,
            'review_file = "MAIN"\n'
            "max_passes = 1\n"
            "[classifier]\n"
            'harness = "codex"\n'
            'model = "main-model"\n'
            'reasoning = "high"\n'
            "[variant.a]\n"
            "max_passes = 2\n"
            "[variant.a.classifier]\n"
            'harness = "claude-code"\n',
        )

        config = load_review_config(self.root, home=self.home, variant="a")

        self.assertEqual((config.variant, config.review_file, config.max_passes), ("a", "MAIN", 2))
        self.assertEqual(config.classifier, HarnessProfile(harness="claude-code"))
        default = load_review_config(self.root, home=self.home, variant="default")
        self.assertEqual((default.variant, default.max_passes), ("default", 1))

    def test_forced_variant_ignores_zero_weight_and_rejects_unknown_tags(self) -> None:
        self.write_config(self.root, "[variants]\nb = 0\n[variant.b]\nmax_passes = 3\n")

        self.assertEqual(load_review_config(self.root, home=self.home, variant="b").max_passes, 3)
        with self.assertRaisesRegex(ReviewStateError, "unknown review config variant 'c'"):
            load_review_config(self.root, home=self.home, variant="c")

    def test_nearest_experiment_replaces_parent_experiment_while_settings_still_merge(self) -> None:
        self.write_config(self.home, 'review_file = "HOME"\nmax_passes = 5\n[variant.a]\nmax_passes = 4\n')
        self.write_config(self.root, 'review_file = "REPO"\n[variant.b]\nmax_passes = 1\n')

        config = load_review_config(self.root, home=self.home, variant="b")

        self.assertEqual((config.review_file, config.max_passes), ("REPO", 1))
        self.assertEqual(self.drawn_tags(self.root), {"default", "b"})
        with self.assertRaises(ReviewStateError):
            load_review_config(self.root, home=self.home, variant="a")

    def test_rejects_invalid_experiments(self) -> None:
        invalid_configs = (
            "[variant.default]\nmax_passes = 2\n",
            "[variants]\na = -1\n[variant.a]\n",
            "[variants]\na = 1.5\n[variant.a]\n",
            "[variants]\na = true\n[variant.a]\n",
            "[variants]\nc = 2\n",
            "[variants]\ndefault = 0\n",
            "[variants]\ndefault = 0\na = 0\n[variant.a]\n",
            "[variant.a]\nunknown = 1\n",
            "[variant.a]\nmax_passes = 0\n",
            "[variant.a.variants]\nb = 1\n",
            "[variant.A]\nmax_passes = 2\n",
            'variants = "a"\n',
            'variant = "a"\n',
        )
        for index, text in enumerate(invalid_configs):
            with self.subTest(text=text):
                directory = self.root / f"experiment-{index}"
                directory.mkdir()
                self.write_config(directory, text)
                with self.assertRaises(ReviewStateError):
                    load_review_config(directory, home=self.home)

    def test_max_parallel_defaults_to_auto(self) -> None:
        self.assertEqual(load_review_config(self.root, home=self.home).max_parallel, "auto")

    def test_max_parallel_accepts_a_positive_integer_or_auto_from_the_chain(self) -> None:
        self.write_config(self.home, "max_parallel = 4\n")
        self.assertEqual(load_review_config(self.root, home=self.home).max_parallel, 4)
        self.write_config(self.root, 'max_parallel = "auto"\n')
        self.assertEqual(load_review_config(self.root, home=self.home).max_parallel, "auto")

    def test_max_parallel_works_in_a_variant_body(self) -> None:
        self.write_config(self.root, "[variants]\ndefault = 0\n[variant.small]\nmax_parallel = 2\n")
        self.assertEqual(load_review_config(self.root, home=self.home).max_parallel, 2)

    def test_max_parallel_rejects_invalid_values(self) -> None:
        for index, text in enumerate(("max_parallel = 0\n", "max_parallel = true\n", 'max_parallel = "many"\n')):
            with self.subTest(text=text):
                directory = self.root / f"invalid-{index}"
                directory.mkdir()
                self.write_config(directory, text)
                with self.assertRaises(ReviewStateError):
                    load_review_config(directory, home=self.home)

    def test_parse_max_parallel_uses_the_config_rules(self) -> None:
        self.assertEqual(parse_max_parallel("3"), 3)
        self.assertEqual(parse_max_parallel("auto"), "auto")
        for value in ("0", "-1", "many", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_max_parallel(value)

    def test_auto_max_parallel_is_bounded_by_cores_and_available_memory(self) -> None:
        gib = 1024**3
        cases = (
            (16, 50 * gib, 16),
            (16, 9 * gib, 4),
            (16, gib, 1),
            (4, None, 4),
        )
        for cpus, memory, expected in cases:
            with self.subTest(cpus=cpus, memory=memory), \
                    mock.patch("review_config.os.process_cpu_count", return_value=cpus, create=True), \
                    mock.patch("review_config._available_memory_bytes", return_value=memory):
                self.assertEqual(resolve_max_parallel("auto"), expected)
        self.assertEqual(resolve_max_parallel(5), 5)

    def test_reviewer_lanes_load_every_key_with_defaults(self) -> None:
        self.write_config(
            self.root,
            '[[reviewers]]\n'
            'harness = "codex"\n'
            'model = "m1"\n'
            'reasoning = "high"\n'
            'shots = 2\n'
            'passes = 1\n'
            '[[reviewers]]\n'
            'harness = "claude-code"\n'
            'name = "second"\n'
            'from_pass = 2\n'
            'required = true\n'
            'retire_when_clean = false\n',
        )

        config = load_review_config(self.root, home=self.home)

        self.assertEqual(
            config.reviewers,
            (
                ReviewerLane(harness="codex", model="m1", reasoning="high", shots=2, passes=1),
                ReviewerLane(
                    harness="claude-code",
                    name="second",
                    from_pass=2,
                    required=True,
                    retire_when_clean=False,
                ),
            ),
        )
        self.assertEqual(config.reviewers[0].profile, HarnessProfile(harness="codex", model="m1", reasoning="high"))

    def test_reviewer_lane_list_is_replaced_as_a_whole_across_the_chain(self) -> None:
        self.write_config(
            self.home,
            '[[reviewers]]\nharness = "codex"\nshots = 3\n'
            '[[reviewers]]\nharness = "claude-code"\nfrom_pass = 2\n',
        )
        self.write_config(self.root, '[[reviewers]]\nharness = "claude-code"\n')

        config = load_review_config(self.root, home=self.home)

        self.assertEqual(config.reviewers, (ReviewerLane(harness="claude-code"),))

    def test_variant_lane_list_replaces_the_main_lane_list(self) -> None:
        self.write_config(
            self.root,
            '[variants]\ndefault = 0\na = 1\n'
            '[[reviewers]]\nharness = "codex"\nshots = 3\n'
            '[[variant.a.reviewers]]\nharness = "claude-code"\nshots = 2\n',
        )

        config = load_review_config(self.root, home=self.home)

        self.assertEqual(config.variant, "a")
        self.assertEqual(config.reviewers, (ReviewerLane(harness="claude-code", shots=2),))

    def legacy_error(self, repo_text: str, home_text: str | None = None) -> str:
        if home_text is not None:
            self.write_config(self.home, home_text)
        self.write_config(self.root, repo_text)
        with self.assertRaises(ReviewStateError) as raised:
            load_review_config(self.root, home=self.home, variant="default")
        return str(raised.exception)

    def test_legacy_shot_settings_fail_with_the_equivalent_lane_form(self) -> None:
        no_lane = "No lane is necessary: one shot per pass is the default lane."
        cases = (
            ("shots = 3\nshot_passes = 2\n", '[[reviewers]]\nharness = "codex"\nshots = 3\npasses = 2'),
            ('shots = 2\nshot_passes = "always"\n', '[[reviewers]]\nharness = "codex"\nshots = 2'),
            ("shots = 1\n", no_lane),
            ("shot_passes = 2\n", no_lane),
            ('shot_passes = "always"\n', no_lane),
            ("[variant.a]\nshots = 4\n", '[[variant.a.reviewers]]\nharness = "codex"\nshots = 4\npasses = 1'),
            (
                'shots = 2\n[slice_default]\nharness = "claude-code"\n',
                '[[reviewers]]\nharness = "claude-code"\nshots = 2\npasses = 1',
            ),
            (
                '[slice_default]\nharness = "claude-code"\nmodel = "opus"\n[variant.a]\nshots = 2\n',
                '[[variant.a.reviewers]]\nharness = "claude-code"\nmodel = "opus"\nshots = 2\npasses = 1',
            ),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                shutil.rmtree(self.root / ".agents", ignore_errors=True)
                message = self.legacy_error(text)
                self.assertIn("were replaced by [[reviewers]] lanes", message)
                self.assertIn(str(self.root / ".agents" / "multi-shot-review.toml"), message)
                self.assertIn(expected, message)

    def test_legacy_shot_migration_uses_the_profile_of_the_merged_chain(self) -> None:
        message = self.legacy_error(
            '[slice_default]\nharness = "claude-code"\nmodel = "opus"\n',
            home_text='shots = 2\n[slice_default]\nharness = "codex"\nmodel = "m"\n',
        )

        self.assertIn(f"- shots in {self.home / '.agents' / 'multi-shot-review.toml'}", message)
        self.assertIn('[[reviewers]]\nharness = "claude-code"\nmodel = "opus"\nshots = 2', message)
        self.assertNotIn('model = "m"', message)

    def test_legacy_shot_migration_keeps_each_variant_profile(self) -> None:
        message = self.legacy_error(
            'shots = 2\n[variant.a.slice_default]\nharness = "claude-code"\n'
            '[variant.b]\nshots = 1\n[variant.c]\nmax_passes = 5\n'
        )

        self.assertIn('[[reviewers]]\nharness = "codex"\nshots = 2\npasses = 1', message)
        self.assertIn('[[variant.a.reviewers]]\nharness = "claude-code"\nshots = 2', message)
        self.assertIn('[[variant.b.reviewers]]\nharness = "codex"\n\n', message + "\n\n")
        self.assertNotIn("variant.c", message)

    def test_legacy_shot_migration_puts_variant_lanes_in_the_file_that_owns_the_experiment(self) -> None:
        message = self.legacy_error(
            '[variant.a.slice_default]\nharness = "claude-code"\n', home_text="shots = 2\n"
        )
        home_config = self.home / ".agents" / "multi-shot-review.toml"
        repo_config = self.root / ".agents" / "multi-shot-review.toml"

        main_section, variant_section = message.split("To keep the same reviews, add to ")[1:]
        self.assertTrue(main_section.startswith(f"{home_config}:\n\n[[reviewers]]\n"))
        self.assertTrue(variant_section.startswith(f"{repo_config}:\n\n[[variant.a.reviewers]]\n"))
        self.assertIn('harness = "claude-code"\nshots = 2\npasses = 1', variant_section)

    def test_legacy_shot_migration_after_lanes_exist_only_removes_keys(self) -> None:
        message = self.legacy_error('[[reviewers]]\nharness = "codex"\n', home_text="shots = 3\n")

        self.assertIn("- shots in", message)
        self.assertIn("No lane is necessary: the configured [[reviewers]] lanes apply.", message)

    def test_legacy_shot_settings_with_invalid_values_fail_as_invalid(self) -> None:
        for text in ("shots = 0\n", "shots = true\n", 'shot_passes = "bogus"\n', "[variant.a]\nshot_passes = 0\n"):
            with self.subTest(text=text):
                shutil.rmtree(self.root / ".agents", ignore_errors=True)
                self.assertRegex(self.legacy_error(text), "must be a positive integer")

    def test_invalid_reviewer_lanes_fail_through_the_config_chain(self) -> None:
        cases = (
            '[[reviewers]]\nharness = "codex"\nshot = 2\n',
            "[[reviewers]]\nmodel = \"m\"\n",
            '[[reviewers]]\nharness = "no-such-harness"\n',
            '[[reviewers]]\nharness = "codex"\nmodel = " "\n',
            '[[reviewers]]\nharness = "codex"\nname = ""\n',
            '[[reviewers]]\nharness = "codex"\nfrom_pass = 0\n',
            '[[reviewers]]\nharness = "codex"\npasses = true\n',
            '[[reviewers]]\nharness = "codex"\nrequired = "yes"\n',
            '[[reviewers]]\nharness = "codex"\nretire_when_clean = 1\n',
            '[[reviewers]]\nharness = "codex"\nfrom_pass = 2\n',
            '[[variant.a.reviewers]]\nharness = "codex"\nshots = 0\n',
            '[[variant.a.reviewers]]\nharness = "codex"\nlane = "x"\n',
        )
        for text in cases:
            with self.subTest(text=text):
                shutil.rmtree(self.root / ".agents", ignore_errors=True)
                self.write_config(self.root, text)
                with self.assertRaises(ReviewStateError):
                    load_review_config(self.root, home=self.home, variant="default")

    def test_legacy_shot_snapshot_loads_as_one_lane(self) -> None:
        snapshot = ReviewConfig().to_snapshot()
        del snapshot["reviewers"]

        cases = (
            ({"shots": 1, "shot_passes": 1}, ()),
            ({"shots": 3, "shot_passes": 2}, (ReviewerLane(harness="codex", shots=3, passes=2),)),
            ({"shots": 2, "shot_passes": "always"}, (ReviewerLane(harness="codex", shots=2),)),
        )
        for legacy, lanes in cases:
            with self.subTest(legacy=legacy):
                config = ReviewConfig.from_snapshot({**snapshot, **legacy}, variant="default")
                self.assertEqual(config.reviewers, lanes)

        for legacy in (
            {"shots": True, "shot_passes": 1},
            {"shots": 1.0},
            {"shots": 1, "shot_passes": "bogus"},
            {"shots": 2, "shot_passes": 0},
        ):
            with self.subTest(legacy=legacy), self.assertRaisesRegex(ReviewStateError, "snapshot"):
                ReviewConfig.from_snapshot({**snapshot, **legacy}, variant="default")

        profiled = ReviewConfig.from_snapshot(
            {**snapshot, "slice_default": {"harness": "claude-code", "model": "m"}, "shots": 2, "shot_passes": 1},
            variant="default",
        )
        self.assertEqual(profiled.reviewers, (ReviewerLane(harness="claude-code", model="m", shots=2, passes=1),))

    def test_snapshot_lanes_reject_null_required_fields(self) -> None:
        snapshot = ReviewConfig(reviewers=(ReviewerLane(harness="codex", shots=2),)).to_snapshot()
        config = ReviewConfig.from_snapshot(snapshot, variant="default")
        self.assertEqual(config.reviewers, (ReviewerLane(harness="codex", shots=2),))

        for key in ("harness", "from_pass", "shots", "required", "retire_when_clean"):
            lane = {**snapshot["reviewers"][0], key: None}
            with self.subTest(key=key), self.assertRaises(ReviewStateError):
                ReviewConfig.from_snapshot({**snapshot, "reviewers": [lane]}, variant="default")

    def test_reviewers_file_accepts_only_a_nonempty_lane_list(self) -> None:
        path = self.home / "lanes.toml"
        path.write_text('[[reviewers]]\nharness = "codex"\nname = "a"\n', encoding="utf-8")
        self.assertEqual(load_reviewers_file(path), (ReviewerLane(harness="codex", name="a"),))

        for text in ("", "max_passes = 2\n", "reviewers = []\n", '[[reviewers]]\nharness = "codex"\nshots = 0\n'):
            with self.subTest(text=text):
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(ReviewStateError):
                    load_reviewers_file(path)
        with self.assertRaises(ReviewStateError):
            load_reviewers_file(self.home / "missing.toml")

    def test_snapshot_without_max_parallel_loads_with_the_default(self) -> None:
        snapshot = ReviewConfig().to_snapshot()
        del snapshot["max_parallel"]
        self.assertEqual(ReviewConfig.from_snapshot(snapshot, variant="default").max_parallel, "auto")

    def test_snapshot_round_trips_the_effective_config(self) -> None:
        config = ReviewConfig(
            review_file="SECURITY",
            max_passes=2,
            reviewers=(
                ReviewerLane(harness="codex", model="m", shots=2, passes=1),
                ReviewerLane(harness="claude-code", from_pass=2, required=True, retire_when_clean=False),
            ),
            max_parallel=6,
            slice_default=HarnessProfile(harness="codex", model="m"),
            variant="a",
        )

        self.assertNotIn("variant", config.to_snapshot())
        self.assertEqual(ReviewConfig.from_snapshot(config.to_snapshot(), variant="a"), config)
        with self.assertRaises(ReviewStateError):
            ReviewConfig.from_snapshot({**config.to_snapshot(), "reviewers": [{"shots": 2}]}, variant="a")
        with self.assertRaises(ReviewStateError):
            ReviewConfig.from_snapshot(config.to_snapshot(), variant="Bad Tag")


if __name__ == "__main__":
    unittest.main()
