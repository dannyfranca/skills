from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from review_config import load_review_config  # noqa: E402
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

    def test_rejects_unknown_non_string_empty_and_path_settings(self) -> None:
        invalid_configs = (
            "unknown = true\n",
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


if __name__ == "__main__":
    unittest.main()
