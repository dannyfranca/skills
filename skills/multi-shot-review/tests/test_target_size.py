from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from target_size import measure_target_size  # noqa: E402


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, text=True, capture_output=True).stdout.strip()


class TargetSizeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "test@example.com")
        git(self.root, "config", "user.name", "Test")
        (self.root / "a.txt").write_text("1\n2\n3\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "base")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def commit_change(self) -> str:
        (self.root / "a.txt").write_text("1\nchanged\n3\n4\n", encoding="utf-8")
        (self.root / "b.txt").write_text("new\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "change")
        return git(self.root, "rev-parse", "HEAD")

    def test_uncommitted_counts_staged_unstaged_and_untracked_changes(self) -> None:
        (self.root / "a.txt").write_text("1\nchanged\n3\n", encoding="utf-8")
        (self.root / "staged.txt").write_text("s\n", encoding="utf-8")
        git(self.root, "add", "staged.txt")
        (self.root / "untracked.txt").write_text("u1\nu2\n", encoding="utf-8")
        size = measure_target_size(self.root, {"kind": "uncommitted"})
        self.assertEqual(size, {"files": 3, "added": 4, "deleted": 1})

    def test_base_counts_branch_changes_since_the_merge_base(self) -> None:
        git(self.root, "checkout", "-q", "-b", "feature")
        self.commit_change()
        size = measure_target_size(self.root, {"kind": "base", "value": "main"})
        self.assertEqual(size, {"files": 2, "added": 3, "deleted": 1})

    def test_commit_counts_the_changes_of_that_commit(self) -> None:
        sha = self.commit_change()
        (self.root / "later.txt").write_text("later\n", encoding="utf-8")
        size = measure_target_size(self.root, {"kind": "commit", "value": sha})
        self.assertEqual(size, {"files": 2, "added": 3, "deleted": 1})

    def test_merge_commit_counts_changes_against_its_first_parent(self) -> None:
        git(self.root, "checkout", "-q", "-b", "feature")
        self.commit_change()
        git(self.root, "checkout", "-q", "main")
        git(self.root, "merge", "-q", "--no-ff", "-m", "merge", "feature")
        size = measure_target_size(self.root, {"kind": "commit", "value": "HEAD"})
        self.assertEqual(size, {"files": 2, "added": 3, "deleted": 1})

    def test_untracked_symlink_counts_its_link_text_as_one_line(self) -> None:
        (self.root / "link").symlink_to("a.txt")
        size = measure_target_size(self.root, {"kind": "uncommitted"})
        self.assertEqual(size, {"files": 1, "added": 1, "deleted": 0})

    def test_binary_file_counts_as_a_file_without_lines(self) -> None:
        (self.root / "blob.bin").write_bytes(b"\0\1\2")
        git(self.root, "add", "blob.bin")
        size = measure_target_size(self.root, {"kind": "uncommitted"})
        self.assertEqual(size, {"files": 1, "added": 0, "deleted": 0})

    def test_unresolvable_target_has_no_size(self) -> None:
        self.assertIsNone(measure_target_size(self.root, {"kind": "base", "value": "missing"}))


if __name__ == "__main__":
    unittest.main()
