from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fixtures import write_session

from session_store import load_sessions


class LoadSessionsTests(unittest.TestCase):
    def test_loads_sessions_of_repositories_with_and_without_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_session(root, "20261001-0000-aaaaaaaa", repo="owner/hosted")
            write_session(root, "20261001-0001-bbbbbbbb", repo="local-only")
            (root / "_evals" / "20261025-1200-a-vs-b").mkdir(parents=True)

            sessions = load_sessions(root)

        self.assertEqual(
            [(session.id, session.repository) for session in sessions],
            [
                ("local-only/20261001-0001-bbbbbbbb", "local-only"),
                ("owner/hosted/20261001-0000-aaaaaaaa", "owner/hosted"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
