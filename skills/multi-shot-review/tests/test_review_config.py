from __future__ import annotations

import random
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from review_config import ReviewConfig, load_review_config  # noqa: E402
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

    def test_max_passes_and_judge_profile_are_loaded(self) -> None:
        self.write_config(
            self.root,
            'max_passes = 2\n'
            'shots = 3\n'
            'shot_passes = 2\n'
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
        self.assertEqual(config.shots, 3)
        self.assertEqual(config.shot_passes, 2)
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
        self.assertEqual(config.shots, 1)
        self.assertEqual(config.shot_passes, 1)
        self.assertIsNone(config.judge)
        self.assertEqual(config.judge_profile, config.classifier)

    def test_nearer_pass_budget_and_judge_profile_replace_parent_values(self) -> None:
        self.write_config(
            self.home,
            'max_passes = 5\n'
            'shots = 2\n'
            'shot_passes = "always"\n'
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

        self.assertEqual((config.max_passes, config.shots, config.shot_passes), (2, 2, "always"))
        self.assertEqual(config.judge_profile, HarnessProfile(harness="claude-code"))

    def test_rejects_unknown_non_string_empty_and_path_settings(self) -> None:
        invalid_configs = (
            "unknown = true\n",
            "max_passes = 0\n",
            "max_passes = true\n",
            'max_passes = "3"\n',
            "shots = 0\n",
            "shots = false\n",
            "shots = 1.5\n",
            "shot_passes = 0\n",
            "shot_passes = true\n",
            'shot_passes = "never"\n',
            'shot_passes = "1"\n',
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
        self.write_config(self.root, "shots = 2\n")

        self.assertEqual(self.drawn_tags(self.root), {"default"})
        self.assertEqual(load_review_config(self.root, home=self.home).shots, 2)

    def test_variant_bodies_without_weights_split_equally_with_default(self) -> None:
        self.write_config(
            self.root,
            "shots = 1\n"
            "[variant.a]\n"
            "shots = 2\n"
            "[variant.b]\n"
            "shots = 3\n",
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
            "shots = 2\n"
            "[variant.b]\n"
            "shots = 3\n"
            "[variant.c]\n"
            "shots = 4\n",
        )

        self.assertEqual(self.drawn_tags(self.root), {"a", "c"})

    def test_huge_integer_weights_draw_exactly(self) -> None:
        self.write_config(
            self.root,
            f"[variants]\ndefault = 1{'0' * 400}\na = 1\n[variant.a]\nshots = 2\n",
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
            "shots = 1\n"
            "[classifier]\n"
            'harness = "codex"\n'
            'model = "main-model"\n'
            'reasoning = "high"\n'
            "[variant.a]\n"
            "shots = 2\n"
            "[variant.a.classifier]\n"
            'harness = "claude-code"\n',
        )

        config = load_review_config(self.root, home=self.home, variant="a")

        self.assertEqual((config.variant, config.review_file, config.shots), ("a", "MAIN", 2))
        self.assertEqual(config.classifier, HarnessProfile(harness="claude-code"))
        default = load_review_config(self.root, home=self.home, variant="default")
        self.assertEqual((default.variant, default.shots), ("default", 1))

    def test_forced_variant_ignores_zero_weight_and_rejects_unknown_tags(self) -> None:
        self.write_config(self.root, "[variants]\nb = 0\n[variant.b]\nshots = 3\n")

        self.assertEqual(load_review_config(self.root, home=self.home, variant="b").shots, 3)
        with self.assertRaisesRegex(ReviewStateError, "unknown review config variant 'c'"):
            load_review_config(self.root, home=self.home, variant="c")

    def test_nearest_experiment_replaces_parent_experiment_while_settings_still_merge(self) -> None:
        self.write_config(self.home, "shots = 2\nmax_passes = 5\n[variant.a]\nshots = 4\n")
        self.write_config(self.root, "shots = 3\n[variant.b]\nmax_passes = 1\n")

        config = load_review_config(self.root, home=self.home, variant="b")

        self.assertEqual((config.shots, config.max_passes), (3, 1))
        self.assertEqual(self.drawn_tags(self.root), {"default", "b"})
        with self.assertRaises(ReviewStateError):
            load_review_config(self.root, home=self.home, variant="a")

    def test_rejects_invalid_experiments(self) -> None:
        invalid_configs = (
            "[variant.default]\nshots = 2\n",
            "[variants]\na = -1\n[variant.a]\n",
            "[variants]\na = 1.5\n[variant.a]\n",
            "[variants]\na = true\n[variant.a]\n",
            "[variants]\nc = 2\n",
            "[variants]\ndefault = 0\n",
            "[variants]\ndefault = 0\na = 0\n[variant.a]\n",
            "[variant.a]\nunknown = 1\n",
            "[variant.a]\nshots = 0\n",
            "[variant.a.variants]\nb = 1\n",
            "[variant.A]\nshots = 2\n",
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

    def test_snapshot_round_trips_the_effective_config(self) -> None:
        config = ReviewConfig(
            review_file="SECURITY",
            max_passes=2,
            shots=3,
            shot_passes="always",
            slice_default=HarnessProfile(harness="codex", model="m"),
            variant="a",
        )

        self.assertNotIn("variant", config.to_snapshot())
        self.assertEqual(ReviewConfig.from_snapshot(config.to_snapshot(), variant="a"), config)
        with self.assertRaises(ReviewStateError):
            ReviewConfig.from_snapshot({**config.to_snapshot(), "shots": 0}, variant="a")
        with self.assertRaises(ReviewStateError):
            ReviewConfig.from_snapshot(config.to_snapshot(), variant="Bad Tag")


if __name__ == "__main__":
    unittest.main()
