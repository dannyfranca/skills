from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fixtures import codex_stderr, run, slice_, write_logs, write_session

from diff_size import DiffSize
from metrics import Ratio
from session_store import load_sessions
from strata import Dimension, stratify


class StrataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def sessions(self):
        specs = [
            ("a", "owner/one", "codex", "gpt-6-sol"),
            ("a", "owner/one", "codex", "gpt-6-sol"),
            ("a", "owner/two", "codex", "gpt-6-sol"),
            ("b", "owner/one", "claude-code", "claude-opus-5-5"),
            ("b", "owner/one", "codex", "gpt-6-sol"),
            ("b", "owner/one", "codex", "gpt-6-sol"),
        ]
        for index, (variant, repo, harness, model) in enumerate(specs):
            write_session(
                self.root,
                f"20261001-0000-{index:08x}",
                variant=variant,
                repo=repo,
                slices={"core": slice_(run(f"r{index}"), harness=harness, model=model)},
            )
        loaded = load_sessions(self.root)
        return {"a": [s for s in loaded if s.variant == "a"], "b": [s for s in loaded if s.variant == "b"]}

    def test_counts_and_imbalance_per_dimension(self) -> None:
        arms = self.sessions()
        sizes = {}
        for size, session in zip((10, 20, 30, 40, 50, None), arms["a"] + arms["b"]):
            sizes[session.id] = None if size is None else DiffSize(files=1, added=size, deleted=0)
        strata = stratify(arms, sizes, {})

        repository = {row.value: row for row in strata[Dimension.REPOSITORY]}
        self.assertEqual(repository["owner/one"].counts, {"a": 2, "b": 3})
        self.assertEqual(repository["owner/two"].counts, {"a": 1, "b": 0})
        self.assertTrue(repository["owner/two"].imbalanced)
        self.assertTrue(repository["owner/one"].imbalanced)

        models = {row.value: row for row in strata[Dimension.HARNESS_MODEL]}
        self.assertEqual(models["codex/gpt-6-sol"].counts, {"a": 3, "b": 2})
        self.assertEqual(models["claude-code/claude-opus-5-5"].counts, {"a": 0, "b": 1})

        terciles = {row.value: row for row in strata[Dimension.DIFF_SIZE]}
        self.assertEqual(sum(row.counts["a"] + row.counts["b"] for row in terciles.values()), 6)
        self.assertEqual(terciles["unknown"].counts, {"a": 0, "b": 1})
        self.assertEqual(terciles["small"].counts["a"], 2)

    def test_each_stratum_reports_primary_metric_per_arm(self) -> None:
        arms = self.sessions()
        values = dict(
            zip(
                [session.id for session in arms["a"] + arms["b"]],
                [Ratio(1, 1), Ratio(3, 1), Ratio(9, 1), Ratio(4, 1), Ratio(6, 1)],
            )
        )
        strata = stratify(arms, {}, values)

        repository = {row.value: row.primary for row in strata[Dimension.REPOSITORY]}
        self.assertEqual(repository["owner/one"].n, {"a": 2, "b": 2})
        self.assertEqual(repository["owner/one"].means, {"a": 2.0, "b": 5.0})
        self.assertEqual(repository["owner/one"].diff, {"b": 3.0})
        self.assertEqual(repository["owner/two"].n, {"a": 1, "b": 0})
        self.assertEqual(repository["owner/two"].means, {"a": 9.0, "b": None})
        self.assertEqual(repository["owner/two"].diff, {"b": None})

    def test_balanced_strata_are_not_flagged(self) -> None:
        arms = self.sessions()
        arms["b"] = arms["b"][1:]
        arms["a"] = arms["a"][:2]
        strata = stratify(arms, {}, {})
        repository = {row.value: row for row in strata[Dimension.REPOSITORY]}
        self.assertFalse(repository["owner/one"].imbalanced)
        self.assertAlmostEqual(repository["owner/one"].shares["a"], 1.0)

    def test_harness_model_comes_from_run_history_not_the_redefined_slice(self) -> None:
        directory = write_session(
            self.root,
            "20261001-0000-aaaaaaaa",
            slices={
                "core": slice_(run("r1", 1), run("r2", 2), run("r3", 3), model="gpt-6-astra"),
                "gone": slice_(run("r4", 1), harness="claude-code", model="claude-opus-5-5", removed=True),
            },
        )
        state = json.loads((directory / "_state.json").read_text())
        runs = state["slices"]["core"]["runs"]
        runs[0]["model"] = None
        runs[1]["model"] = None
        runs[2]["model"] = "gpt-6-luna"
        (directory / "_state.json").write_text(json.dumps(state))
        write_logs(directory, "r1-1-core", stderr=codex_stderr(model="gpt-6-sol"))
        session = load_sessions(self.root)[0]
        strata = stratify({"a": [session], "b": []}, {}, {})
        values = {row.value for row in strata[Dimension.HARNESS_MODEL]}
        self.assertEqual(
            values, {"claude-code/claude-opus-5-5+codex/default+codex/gpt-6-luna+codex/gpt-6-sol"}
        )

if __name__ == "__main__":
    unittest.main()
