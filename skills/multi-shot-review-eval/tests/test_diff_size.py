from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from fixtures import write_session

from diff_size import DiffSize, resolve_diff_size, target_resolves
from sampling import Judgeability, judgeability
from session_store import load_sessions


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class DiffSizeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.reviews = base / "reviews"
        self.repo = base / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "a.txt").write_text("one\ntwo\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "base")
        self.base_sha = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "a.txt").write_text("one\nTWO\nthree\n")
        (self.repo / "b.txt").write_text("new\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "change")
        self.head_sha = git(self.repo, "rev-parse", "HEAD")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def session(self, name: str, **kwargs):
        write_session(self.reviews, name, **kwargs)
        return next(s for s in load_sessions(self.reviews) if s.id.endswith(name))

    def test_recorded_target_size_wins(self) -> None:
        session = self.session(
            "20261001-0000-aaaaaaaa",
            session_extra={"target_size": {"files": 1, "added": 5, "deleted": 5}},
        )
        self.assertEqual(resolve_diff_size(session), DiffSize(files=1, added=5, deleted=5))

    def test_base_target_without_recorded_size_has_no_size_and_approximate_judgeability(self) -> None:
        session = self.session(
            "20261001-0000-bbbbbbbb",
            root=self.repo,
            target={"kind": "base", "value": self.base_sha},
        )
        self.assertIsNone(resolve_diff_size(session))
        self.assertFalse(target_resolves(session))
        self.assertEqual(judgeability(session), Judgeability.APPROXIMATE)

    def test_commit_target_uses_first_parent(self) -> None:
        session = self.session(
            "20261001-0000-cccccccc",
            root=self.repo,
            target={"kind": "commit", "value": self.head_sha},
        )
        self.assertEqual(resolve_diff_size(session), DiffSize(files=2, added=3, deleted=1))
        self.assertEqual(judgeability(session), Judgeability.EXACT)

    def test_root_commit_target_diffs_against_empty_tree(self) -> None:
        session = self.session(
            "20261001-0000-c0c0c0c0",
            root=self.repo,
            target={"kind": "commit", "value": self.base_sha},
        )
        self.assertEqual(resolve_diff_size(session), DiffSize(files=1, added=2, deleted=0))

    def test_merge_commit_target_uses_first_parent(self) -> None:
        git(self.repo, "checkout", "-qb", "side", self.base_sha)
        (self.repo / "c.txt").write_text("x\ny\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "side")
        git(self.repo, "checkout", "-q", "main")
        git(self.repo, "merge", "-q", "--no-ff", "-m", "merge", "side")
        session = self.session(
            "20261001-0000-c1c1c1c1",
            root=self.repo,
            target={"kind": "commit", "value": git(self.repo, "rev-parse", "HEAD")},
        )
        self.assertEqual(resolve_diff_size(session), DiffSize(files=1, added=2, deleted=0))

    def test_unresolvable_targets_have_no_size(self) -> None:
        cases = {
            "20261001-0000-dddddddd": {"root": self.repo, "target": {"kind": "uncommitted"}},
            "20261001-0000-eeeeeeee": {"target": {"kind": "base", "value": "main"}},
            "20261001-0000-ffffffff": {"root": self.repo, "target": {"kind": "base", "value": "nope"}},
        }
        for name, kwargs in cases.items():
            with self.subTest(name=name):
                session = self.session(name, **kwargs)
                self.assertIsNone(resolve_diff_size(session))
                self.assertFalse(target_resolves(session))


if __name__ == "__main__":
    unittest.main()
