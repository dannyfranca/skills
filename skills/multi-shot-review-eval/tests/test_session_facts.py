from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fixtures import PRICES, finding, run, slice_, write_session

from prices import load_price_table
from session_facts import FactsCache
from session_store import load_sessions


class FactsCacheTests(unittest.TestCase):
    def test_partial_findings_contribute_no_finding_classes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = write_session(
                Path(tmp),
                "20261001-0000-aaaaaaaa",
                slices={
                    "core": slice_(
                        run("r1", 1, findings=[finding("lost", "P1")], archived=True),
                        run("r2", 2, findings=[finding("readable", "P1")]),
                    )
                },
            )
            (directory / "history" / "r1.json").unlink()
            facts = FactsCache(load_price_table(PRICES)).get(load_sessions(Path(tmp))[0])
        self.assertFalse(facts.findings_complete)
        self.assertEqual(facts.finding_classes, {})


if __name__ == "__main__":
    unittest.main()
