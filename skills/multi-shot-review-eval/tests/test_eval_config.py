from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import fixtures  # noqa: F401

from eval_config import load_eval_config
from eval_errors import EvalError


class EvalConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.cwd = self.home / "work" / "repo"
        self.cwd.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, directory: Path, text: str) -> Path:
        agents = directory / ".agents"
        agents.mkdir(exist_ok=True)
        path = agents / "multi-shot-review.toml"
        path.write_text(text)
        return path

    def test_active_tags_skip_zero_weights_in_declaration_order(self) -> None:
        self.write(
            self.home,
            'review_root = "~/revs"\n[variants]\ndefault = 0\nhigh = 1\nmedium = 2\n'
            "[variant.high]\n[variant.medium]\nshots = 1\n",
        )
        config = load_eval_config(self.cwd, home=self.home)
        self.assertEqual(config.active_tags, ("high", "medium"))
        self.assertEqual(config.review_root, self.home / "revs")

    def test_implicit_default_joins_the_draw(self) -> None:
        self.write(self.home, "[variant.alt]\nshots = 1\n")
        self.assertEqual(load_eval_config(self.cwd, home=self.home).active_tags, ("default", "alt"))

    def test_no_experiment_means_only_default(self) -> None:
        config = load_eval_config(self.cwd, home=self.home)
        self.assertEqual(config.active_tags, ("default",))
        self.assertEqual(config.review_root, self.home / ".reviews")

    def test_nearest_experiment_owns_tags_and_root_inherits(self) -> None:
        self.write(self.home, 'review_root = "/data/r"\n[variants]\nx = 1\n[variant.x]\n')
        self.write(self.cwd, "[variants]\ndefault = 1\ny = 1\n[variant.y]\n")
        config = load_eval_config(self.cwd, home=self.home)
        self.assertEqual(config.active_tags, ("default", "y"))
        self.assertEqual(config.review_root, Path("/data/r"))

    def test_explicit_config_bypasses_chain(self) -> None:
        self.write(self.home, "[variants]\nx = 1\n[variant.x]\n")
        explicit = Path(self.tmp.name) / "arm.toml"
        explicit.write_text("[variants]\ndefault = 0\np = 1\nq = 1\n[variant.p]\n[variant.q]\n")
        config = load_eval_config(self.cwd, home=self.home, explicit=explicit)
        self.assertEqual(config.active_tags, ("p", "q"))

    def test_missing_explicit_config_fails(self) -> None:
        with self.assertRaises(EvalError):
            load_eval_config(self.cwd, home=self.home, explicit=Path(self.tmp.name) / "nope.toml")


if __name__ == "__main__":
    unittest.main()
