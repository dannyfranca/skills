from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import review_snapshot  # noqa: E402
from review_snapshot import SnapshotError, pin_reviewed_code, prune_expired_snapshots  # noqa: E402

# A session name with a fixed date would be older than the retention one month later.
SESSION = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M") + "-ab12cd34"
UNCOMMITTED = {"kind": "uncommitted"}


def git(root: Path, *args: str) -> str:
    # The fixture commits must not need the signing key of the developer.
    return subprocess.run(
        ["git", "-c", "commit.gpgSign=false", *args], cwd=root, check=True, text=True, capture_output=True
    ).stdout.strip()


def init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")


class SnapshotTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        init_repo(self.root)
        (self.root / "a.txt").write_text("base\n", encoding="utf-8")
        (self.root / "gone.txt").write_text("gone\n", encoding="utf-8")
        (self.root / ".gitignore").write_text("*.log\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "base")
        self.head = git(self.root, "rev-parse", "HEAD")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def files(self, commit: str) -> list[str]:
        return [name for name in git(self.root, "ls-tree", "-r", "--name-only", "-z", commit).split("\0") if name]

    def show(self, commit: str, path: str) -> str:
        return git(self.root, "show", f"{commit}:{path}")

    def parents(self, commit: str) -> list[str]:
        return git(self.root, "rev-list", "--parents", "-n", "1", commit).split()[1:]


class WorkingTreeSnapshotTest(SnapshotTestCase):
    def test_snapshot_holds_the_changed_staged_and_untracked_files_and_no_ignored_file(self) -> None:
        (self.root / "a.txt").write_text("changed\n", encoding="utf-8")
        (self.root / "staged.txt").write_text("staged\n", encoding="utf-8")
        git(self.root, "add", "staged.txt")
        (self.root / "staged.txt").write_text("staged, then edited\n", encoding="utf-8")
        (self.root / "new dir").mkdir()
        (self.root / "new dir" / "ünï*.txt").write_text("untracked\n", encoding="utf-8")
        (self.root / "debug.log").write_text("ignored\n", encoding="utf-8")
        (self.root / "gone.txt").unlink()

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertIsNotNone(snapshot)
        self.assertEqual(
            self.files(snapshot.commit), [".gitignore", "a.txt", "new dir/ünï*.txt", "staged.txt"]
        )
        self.assertEqual(self.show(snapshot.commit, "a.txt"), "changed")
        self.assertEqual(self.show(snapshot.commit, "staged.txt"), "staged, then edited")
        self.assertEqual(self.parents(snapshot.commit), [self.head, snapshot.staged])
        self.assertEqual(snapshot.ref, f"refs/multi-shot-review/{SESSION}/{snapshot.commit}")
        self.assertEqual(git(self.root, "rev-parse", snapshot.ref), snapshot.commit)
        self.assertEqual(Path(snapshot.git_dir), (self.root / ".git").resolve())

    def test_staging_area_with_other_content_has_its_own_commit(self) -> None:
        (self.root / "a.txt").write_text("staged\n", encoding="utf-8")
        git(self.root, "add", "a.txt")
        (self.root / "a.txt").write_text("staged, then edited\n", encoding="utf-8")
        (self.root / "untracked.txt").write_text("untracked\n", encoding="utf-8")

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(self.show(snapshot.staged, "a.txt"), "staged")
        self.assertEqual(self.files(snapshot.staged), [".gitignore", "a.txt", "gone.txt"])
        self.assertEqual(self.parents(snapshot.staged), [self.head])
        self.assertEqual(self.show(snapshot.commit, "a.txt"), "staged, then edited")

    def test_staged_change_that_the_working_copy_reverted_stays_readable(self) -> None:
        (self.root / "a.txt").write_text("staged\n", encoding="utf-8")
        git(self.root, "add", "a.txt")
        (self.root / "a.txt").write_text("base\n", encoding="utf-8")

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(self.show(snapshot.staged, "a.txt"), "staged")
        self.assertEqual(self.show(snapshot.commit, "a.txt"), "base")
        self.assertEqual(self.parents(snapshot.commit), [self.head, snapshot.staged])

    def test_staging_area_equal_to_the_working_copy_or_to_head_has_no_second_commit(self) -> None:
        (self.root / "a.txt").write_text("not staged\n", encoding="utf-8")
        not_staged = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)
        git(self.root, "add", "a.txt")
        all_staged = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        for snapshot in (not_staged, all_staged):
            with self.subTest(snapshot=snapshot):
                self.assertIsNone(snapshot.staged)
                self.assertEqual(self.parents(snapshot.commit), [self.head])
                self.assertEqual(self.show(snapshot.commit, "a.txt"), "not staged")

    def test_merge_conflict_keeps_the_staged_content_of_the_other_files(self) -> None:
        git(self.root, "checkout", "-q", "-b", "other")
        (self.root / "a.txt").write_text("theirs\n", encoding="utf-8")
        (self.root / "gone.txt").write_text("theirs changed it\n", encoding="utf-8")
        git(self.root, "commit", "-q", "-am", "theirs")
        git(self.root, "checkout", "-q", "main")
        (self.root / "a.txt").write_text("ours\n", encoding="utf-8")
        git(self.root, "rm", "-q", "gone.txt")
        git(self.root, "commit", "-q", "-am", "ours")
        head = git(self.root, "rev-parse", "HEAD")
        merge = subprocess.run(["git", "merge", "other"], cwd=self.root, capture_output=True, check=False)
        self.assertNotEqual(merge.returncode, 0)
        (self.root / "new.txt").write_text("staged\n", encoding="utf-8")
        git(self.root, "add", "new.txt")
        (self.root / "new.txt").write_text("edited after staging\n", encoding="utf-8")
        conflicts = git(self.root, "ls-files", "--unmerged")

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(self.show(snapshot.staged, "new.txt"), "staged")
        self.assertEqual(self.show(snapshot.commit, "new.txt"), "edited after staging")
        self.assertEqual(self.show(snapshot.staged, "a.txt"), "ours")
        self.assertIn("<<<<<<<", self.show(snapshot.commit, "a.txt"))
        # HEAD deleted this file, and the merge left the other side on the disk.
        self.assertNotIn("gone.txt", self.files(snapshot.staged))
        self.assertEqual(self.show(snapshot.commit, "gone.txt"), "theirs changed it")
        self.assertEqual(self.parents(snapshot.commit), [head, snapshot.staged])
        self.assertEqual(git(self.root, "ls-files", "--unmerged"), conflicts)

    def test_temporary_directory_in_the_working_tree_keeps_the_scratch_index_out_of_the_snapshot(self) -> None:
        (self.root / "tmp").mkdir()
        (self.root / "tmp" / "note.txt").write_text("a file of the author\n", encoding="utf-8")

        with mock.patch.object(tempfile, "tempdir", str(self.root / "tmp")):
            snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(
            [name for name in self.files(snapshot.commit) if name.startswith("tmp/")], ["tmp/note.txt"]
        )

    def test_edit_that_keeps_the_size_and_the_time_of_a_staged_file_is_in_the_snapshot(self) -> None:
        # Git cannot see such an edit in the file data, so it reads each file that is not older
        # than the index. The time of the status change is not the same on each run.
        git(self.root, "config", "core.trustctime", "false")
        staged_at = 1_500_000_000
        tracked = self.root / "a.txt"
        os.utime(tracked, (staged_at, staged_at))
        git(self.root, "update-index", "--refresh")
        tracked.write_text("edit\n", encoding="utf-8")
        os.utime(tracked, (staged_at, staged_at))
        os.utime(self.root / ".git" / "index", (staged_at, staged_at))
        self.assertEqual(git(self.root, "diff-files", "--name-only"), "a.txt")

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(self.show(snapshot.commit, "a.txt"), "edit")

    def test_split_index_of_the_author_keeps_its_shared_index_file(self) -> None:
        git(self.root, "config", "core.splitIndex", "true")
        git(self.root, "config", "splitIndex.sharedIndexExpire", "now")
        git(self.root, "update-index", "--split-index")
        # An added file makes Git write a new shared index file, and then Git deletes the old ones.
        (self.root / "new.txt").write_text("untracked\n", encoding="utf-8")
        staging_area = git(self.root, "ls-files", "-s")
        shared = sorted(path.name for path in (self.root / ".git").glob("sharedindex.*"))
        self.assertTrue(shared)

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(sorted(path.name for path in (self.root / ".git").glob("sharedindex.*")), shared)
        self.assertEqual(git(self.root, "ls-files", "-s"), staging_area)
        self.assertEqual(self.show(snapshot.commit, "new.txt"), "untracked")

    def test_working_tree_and_git_directory_with_a_space_at_the_end_of_the_name(self) -> None:
        root = Path(self.tmp.name) / "work "
        store = Path(self.tmp.name) / "store "
        root.mkdir()
        git(root, "init", "-q", "-b", "main", "--separate-git-dir", str(store))
        (root / "a.txt").write_text("untracked\n", encoding="utf-8")

        snapshot = pin_reviewed_code(root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(snapshot.git_dir, str(store.resolve()))
        self.assertEqual(git(store, "show", f"{snapshot.commit}:a.txt"), "untracked")

    def test_stash_conflict_keeps_the_earlier_staging_area_out_of_the_staged_content(self) -> None:
        (self.root / "a.txt").write_text("stashed\n", encoding="utf-8")
        git(self.root, "stash", "-q")
        (self.root / "a.txt").write_text("staged before the stash\n", encoding="utf-8")
        git(self.root, "add", "a.txt")
        apply = subprocess.run(["git", "stash", "apply"], cwd=self.root, capture_output=True, check=False)
        self.assertNotEqual(apply.returncode, 0)
        self.assertEqual(git(self.root, "show", ":2:a.txt"), "staged before the stash")
        (self.root / "new.txt").write_text("staged\n", encoding="utf-8")
        git(self.root, "add", "new.txt")
        conflicts = git(self.root, "ls-files", "--unmerged")

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(self.show(snapshot.staged, "a.txt"), git(self.root, "show", "HEAD:a.txt"))
        self.assertEqual(self.show(snapshot.staged, "new.txt"), "staged")
        self.assertIn("<<<<<<<", self.show(snapshot.commit, "a.txt"))
        self.assertEqual(git(self.root, "ls-files", "--unmerged"), conflicts)

    def test_merge_conflict_without_other_staged_content_gives_the_working_copy_only(self) -> None:
        git(self.root, "checkout", "-q", "-b", "other")
        (self.root / "a.txt").write_text("theirs\n", encoding="utf-8")
        git(self.root, "commit", "-q", "-am", "theirs")
        git(self.root, "checkout", "-q", "main")
        (self.root / "a.txt").write_text("ours\n", encoding="utf-8")
        git(self.root, "commit", "-q", "-am", "ours")
        merge = subprocess.run(["git", "merge", "other"], cwd=self.root, capture_output=True, check=False)
        self.assertNotEqual(merge.returncode, 0)
        conflicts = git(self.root, "ls-files", "--unmerged")

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertIsNone(snapshot.staged)
        self.assertIn("<<<<<<<", self.show(snapshot.commit, "a.txt"))
        self.assertEqual(git(self.root, "ls-files", "--unmerged"), conflicts)

    def test_session_root_in_a_subdirectory_snapshots_the_full_working_tree(self) -> None:
        (self.root / "sub").mkdir()
        (self.root / "sub" / "inner.txt").write_text("inner\n", encoding="utf-8")
        (self.root / "a.txt").write_text("changed above the root\n", encoding="utf-8")

        snapshot = pin_reviewed_code(self.root / "sub", UNCOMMITTED, session_name=SESSION)

        self.assertEqual(self.show(snapshot.commit, "a.txt"), "changed above the root")
        self.assertEqual(self.show(snapshot.commit, "sub/inner.txt"), "inner")
        self.assertEqual(Path(snapshot.git_dir), (self.root / ".git").resolve())

    def test_snapshot_leaves_the_index_the_working_tree_the_branch_and_the_stash_as_they_are(self) -> None:
        (self.root / "a.txt").write_text("stashed\n", encoding="utf-8")
        git(self.root, "stash")
        stash = git(self.root, "rev-parse", "refs/stash")
        (self.root / "a.txt").write_text("changed\n", encoding="utf-8")
        (self.root / "staged.txt").write_text("staged\n", encoding="utf-8")
        git(self.root, "add", "staged.txt")
        (self.root / "staged.txt").write_text("staged, then edited\n", encoding="utf-8")
        (self.root / "untracked.txt").write_text("untracked\n", encoding="utf-8")
        status = git(self.root, "status", "--porcelain")
        index = (self.root / ".git" / "index").read_bytes()
        working_copy = {name: (self.root / name).read_bytes() for name in ("a.txt", "staged.txt", "untracked.txt")}

        self.assertIsNotNone(pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION))

        self.assertEqual(git(self.root, "status", "--porcelain"), status)
        self.assertEqual((self.root / ".git" / "index").read_bytes(), index)
        self.assertEqual({name: (self.root / name).read_bytes() for name in working_copy}, working_copy)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), self.head)
        self.assertEqual(git(self.root, "rev-parse", "refs/stash"), stash)
        self.assertEqual(git(self.root, "stash", "list").count("\n"), 0)

    def test_review_without_changes_pins_the_head_commit(self) -> None:
        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(snapshot.commit, self.head)
        self.assertEqual(git(self.root, "rev-parse", snapshot.ref), self.head)

    def test_base_target_snapshots_the_working_tree(self) -> None:
        git(self.root, "checkout", "-q", "-b", "feature")
        (self.root / "a.txt").write_text("committed\n", encoding="utf-8")
        git(self.root, "commit", "-q", "-am", "feature")
        (self.root / "a.txt").write_text("not yet committed\n", encoding="utf-8")

        snapshot = pin_reviewed_code(self.root, {"kind": "base", "value": "main"}, session_name=SESSION)

        self.assertEqual(self.show(snapshot.commit, "a.txt"), "not yet committed")
        self.assertEqual(git(self.root, "rev-parse", f"{snapshot.commit}^"), git(self.root, "rev-parse", "HEAD"))

    def test_repository_without_a_commit_gets_a_root_snapshot(self) -> None:
        fresh = Path(self.tmp.name) / "fresh"
        init_repo(fresh)
        (fresh / "first.txt").write_text("first\n", encoding="utf-8")

        snapshot = pin_reviewed_code(fresh, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(git(fresh, "show", f"{snapshot.commit}:first.txt"), "first")
        self.assertEqual(git(fresh, "rev-list", "--count", snapshot.commit), "1")
        self.assertIsNone(snapshot.staged)

    def test_repository_without_a_commit_keeps_its_staging_area(self) -> None:
        fresh = Path(self.tmp.name) / "fresh"
        init_repo(fresh)
        (fresh / "first.txt").write_text("staged\n", encoding="utf-8")
        git(fresh, "add", "first.txt")
        (fresh / "first.txt").write_text("edited\n", encoding="utf-8")

        snapshot = pin_reviewed_code(fresh, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(git(fresh, "show", f"{snapshot.staged}:first.txt"), "staged")
        self.assertEqual(git(fresh, "show", f"{snapshot.commit}:first.txt"), "edited")
        self.assertEqual(git(fresh, "rev-list", "--count", snapshot.commit), "2")

    def test_nested_repositories_stay_out_and_the_other_files_stay_in(self) -> None:
        init_repo(self.root / "empty-nested")
        init_repo(self.root / "nested")
        (self.root / "nested" / "inner.txt").write_text("inner\n", encoding="utf-8")
        git(self.root / "nested", "add", ".")
        git(self.root / "nested", "commit", "-q", "-m", "inner")
        (self.root / "beside.txt").write_text("beside\n", encoding="utf-8")

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(self.files(snapshot.commit), [".gitignore", "a.txt", "beside.txt", "gone.txt"])

    def test_untracked_file_over_the_file_limit_stays_out(self) -> None:
        (self.root / "at-limit.txt").write_text("x" * 32, encoding="utf-8")
        (self.root / "over-limit.bin").write_text("x" * 33, encoding="utf-8")

        with mock.patch.object(review_snapshot, "UNTRACKED_FILE_LIMIT_BYTES", 32):
            snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(self.files(snapshot.commit), [".gitignore", "a.txt", "at-limit.txt", "gone.txt"])

    def test_smallest_untracked_files_get_the_total_budget(self) -> None:
        (self.root / "a-large.txt").write_text("x" * 7, encoding="utf-8")
        (self.root / "b-small.txt").write_text("x" * 5, encoding="utf-8")
        (self.root / "c-small.txt").write_text("x" * 5, encoding="utf-8")

        with mock.patch.object(review_snapshot, "UNTRACKED_TOTAL_LIMIT_BYTES", 10):
            snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(
            self.files(snapshot.commit), [".gitignore", "a.txt", "b-small.txt", "c-small.txt", "gone.txt"]
        )

    def test_large_tracked_file_stays_in(self) -> None:
        (self.root / "a.txt").write_text("x" * 64, encoding="utf-8")

        with mock.patch.object(review_snapshot, "UNTRACKED_FILE_LIMIT_BYTES", 32):
            snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(self.show(snapshot.commit, "a.txt"), "x" * 64)

    def test_untracked_file_outside_a_sparse_checkout_stays_in(self) -> None:
        (self.root / "kept").mkdir()
        (self.root / "kept" / "k.txt").write_text("kept\n", encoding="utf-8")
        (self.root / "hidden").mkdir()
        (self.root / "hidden" / "h.txt").write_text("hidden\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "dirs")
        git(self.root, "sparse-checkout", "set", "kept")
        (self.root / "hidden").mkdir(exist_ok=True)
        (self.root / "hidden" / "new.txt").write_text("new\n", encoding="utf-8")

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(self.show(snapshot.commit, "hidden/h.txt"), "hidden")
        self.assertEqual(self.show(snapshot.commit, "hidden/new.txt"), "new")

    def test_edit_of_a_tracked_file_restored_outside_a_sparse_checkout_is_in_the_snapshot(self) -> None:
        (self.root / "kept").mkdir()
        (self.root / "kept" / "k.txt").write_text("kept\n", encoding="utf-8")
        (self.root / "hidden").mkdir()
        (self.root / "hidden" / "h.txt").write_text("hidden\n", encoding="utf-8")
        (self.root / "hidden" / "absent.txt").write_text("absent\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "dirs")
        git(self.root, "sparse-checkout", "set", "kept")
        (self.root / "hidden").mkdir(exist_ok=True)
        (self.root / "hidden" / "h.txt").write_text("restored and edited\n", encoding="utf-8")

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(self.show(snapshot.commit, "hidden/h.txt"), "restored and edited")
        self.assertEqual(self.show(snapshot.commit, "hidden/absent.txt"), "absent")

    def test_snapshot_needs_no_signing_key_and_no_git_identity(self) -> None:
        git(self.root, "config", "commit.gpgSign", "true")
        git(self.root, "config", "gpg.program", "false")
        git(self.root, "config", "--unset", "user.name")
        git(self.root, "config", "--unset", "user.email")
        git(self.root, "config", "user.useConfigOnly", "true")
        (self.root / "a.txt").write_text("changed\n", encoding="utf-8")
        empty_home = Path(self.tmp.name) / "home"
        empty_home.mkdir()

        with mock.patch.dict(os.environ, {"HOME": str(empty_home), "GIT_CONFIG_NOSYSTEM": "1"}):
            snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(self.show(snapshot.commit, "a.txt"), "changed")

    def test_snapshot_survives_the_removal_of_its_worktree_and_branch(self) -> None:
        worktree = Path(self.tmp.name) / "linked"
        git(self.root, "worktree", "add", "-q", "-b", "task", str(worktree))
        (worktree / "a.txt").write_text("from the worktree\n", encoding="utf-8")
        (worktree / "untracked.txt").write_text("untracked\n", encoding="utf-8")

        snapshot = pin_reviewed_code(worktree, UNCOMMITTED, session_name=SESSION)
        git(self.root, "worktree", "remove", "--force", str(worktree))
        git(self.root, "branch", "-D", "task")
        git(self.root, "gc", "-q", "--prune=now")

        self.assertEqual(Path(snapshot.git_dir), (self.root / ".git").resolve())
        self.assertEqual(git(Path(snapshot.git_dir), "show", f"{snapshot.commit}:a.txt"), "from the worktree")
        self.assertEqual(git(Path(snapshot.git_dir), "show", f"{snapshot.commit}:untracked.txt"), "untracked")


class FileAndDirectoryExchangeTest(SnapshotTestCase):
    def setUp(self) -> None:
        super().setUp()
        (self.root / "kept").mkdir()
        (self.root / "kept" / "k.txt").write_text("kept\n", encoding="utf-8")
        (self.root / "hidden").mkdir()
        (self.root / "hidden" / "h.txt").write_text("hidden\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "dirs")

    def directory_replaces(self, name: str) -> None:
        (self.root / name).unlink(missing_ok=True)
        (self.root / name).mkdir(parents=True)
        (self.root / name / "impl.py").write_text("impl\n", encoding="utf-8")

    def file_replaces_the_directory(self, name: str) -> None:
        for file in (self.root / name).glob("*"):
            file.unlink()
        (self.root / name).rmdir()
        (self.root / name).write_text("now a file\n", encoding="utf-8")

    def conflict_in(self, name: str) -> None:
        git(self.root, "checkout", "-q", "-b", "other")
        (self.root / name).write_text("theirs\n", encoding="utf-8")
        git(self.root, "commit", "-q", "-am", "theirs")
        git(self.root, "checkout", "-q", "main")
        (self.root / name).write_text("ours\n", encoding="utf-8")
        git(self.root, "commit", "-q", "-am", "ours")
        merge = subprocess.run(["git", "merge", "other"], cwd=self.root, capture_output=True, check=False)
        self.assertNotEqual(merge.returncode, 0)

    def test_directory_replaces_a_tracked_file(self) -> None:
        self.directory_replaces("a.txt")

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(self.show(snapshot.commit, "a.txt/impl.py"), "impl")

    def test_file_replaces_a_tracked_directory(self) -> None:
        self.file_replaces_the_directory("kept")

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(self.show(snapshot.commit, "kept"), "now a file")

    def test_directory_replaces_a_file_with_a_merge_conflict(self) -> None:
        self.conflict_in("a.txt")
        self.directory_replaces("a.txt")
        (self.root / "gone.txt").write_text("other change\n", encoding="utf-8")

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(self.show(snapshot.commit, "a.txt/impl.py"), "impl")
        self.assertEqual(self.show(snapshot.commit, "gone.txt"), "other change")

    def test_file_replaces_the_directory_of_a_file_with_a_merge_conflict(self) -> None:
        self.conflict_in("kept/k.txt")
        self.file_replaces_the_directory("kept")

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(self.show(snapshot.commit, "kept"), "now a file")

    def test_link_replaces_the_directory_of_a_file_with_a_merge_conflict(self) -> None:
        self.conflict_in("kept/k.txt")
        (self.root / "elsewhere").mkdir()
        (self.root / "elsewhere" / "k.txt").write_text("through the link\n", encoding="utf-8")
        (self.root / "kept" / "k.txt").unlink()
        (self.root / "kept").rmdir()
        (self.root / "kept").symlink_to("elsewhere")
        (self.root / "gone.txt").write_text("other change\n", encoding="utf-8")

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(git(self.root, "ls-tree", "--format=%(objectmode)", snapshot.commit, "kept"), "120000")
        self.assertEqual(self.show(snapshot.commit, "kept"), "elsewhere")
        self.assertEqual(self.show(snapshot.commit, "gone.txt"), "other change")

    def test_directory_replaces_a_file_that_git_assumes_unchanged(self) -> None:
        git(self.root, "update-index", "--assume-unchanged", "a.txt")
        self.directory_replaces("a.txt")

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(self.show(snapshot.commit, "a.txt/impl.py"), "impl")

    def test_directory_replaces_a_file_that_git_does_not_check_out(self) -> None:
        git(self.root, "update-index", "--skip-worktree", "a.txt")
        self.directory_replaces("a.txt")

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(self.show(snapshot.commit, "a.txt/impl.py"), "impl")

    def test_file_replaces_a_directory_outside_a_sparse_checkout(self) -> None:
        git(self.root, "sparse-checkout", "set", "kept")
        (self.root / "hidden").write_text("now a file\n", encoding="utf-8")

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(self.show(snapshot.commit, "hidden"), "now a file")
        self.assertEqual(self.show(snapshot.commit, "kept/k.txt"), "kept")

    def test_directory_replaces_a_file_outside_a_sparse_checkout(self) -> None:
        git(self.root, "sparse-checkout", "set", "kept")
        self.directory_replaces("hidden/h.txt")

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(self.show(snapshot.commit, "hidden/h.txt/impl.py"), "impl")


@unittest.skipIf(os.geteuid() == 0, "the superuser can read each file")
class FileThatGitCannotStoreTest(SnapshotTestCase):
    def setUp(self) -> None:
        super().setUp()
        (self.root / "gone.txt").write_text("other change\n", encoding="utf-8")

    def without_read_permission(self, name: str) -> None:
        (self.root / name).write_text("secret\n", encoding="utf-8")
        (self.root / name).chmod(0)

    def snapshot_files(self) -> list[str]:
        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)
        self.assertEqual(self.show(snapshot.commit, "gone.txt"), "other change")
        return self.files(snapshot.commit)

    def test_untracked_file_without_read_permission_is_not_in_the_snapshot(self) -> None:
        self.without_read_permission("new.txt")

        self.assertEqual(self.snapshot_files(), [".gitignore", "a.txt", "gone.txt"])

    def test_tracked_file_without_read_permission_is_not_in_the_snapshot(self) -> None:
        self.without_read_permission("a.txt")

        self.assertEqual(self.snapshot_files(), [".gitignore", "gone.txt"])

    def test_file_without_read_permission_that_git_assumes_unchanged_is_not_in_the_snapshot(self) -> None:
        git(self.root, "update-index", "--assume-unchanged", "a.txt")
        self.without_read_permission("a.txt")

        self.assertEqual(self.snapshot_files(), [".gitignore", "gone.txt"])

    def test_pipe_that_replaces_a_tracked_file_is_not_in_the_snapshot(self) -> None:
        (self.root / "a.txt").unlink()
        os.mkfifo(self.root / "a.txt")

        self.assertEqual(self.snapshot_files(), [".gitignore", "gone.txt"])

    def test_tracked_file_without_read_permission_is_left_out_and_not_deleted(self) -> None:
        self.without_read_permission("a.txt")

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(snapshot.omitted, ("a.txt",))


class FileLeftOutOfTheSnapshotTest(SnapshotTestCase):
    def no_longer_tracked_and_ignored(self, name: str) -> None:
        git(self.root, "rm", "-q", "--cached", name)
        (self.root / ".gitignore").write_text(f"*.log\n{name}\n", encoding="utf-8")

    def test_deleted_file_is_not_a_file_that_the_snapshot_left_out(self) -> None:
        (self.root / "gone.txt").unlink()

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(snapshot.omitted, ())

    def test_ignored_file_that_git_tracked_before_the_change(self) -> None:
        self.no_longer_tracked_and_ignored("gone.txt")

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertNotIn("gone.txt", self.files(snapshot.commit))
        self.assertEqual(snapshot.omitted, ("gone.txt",))

    def test_ignored_file_in_a_branch_without_uncommitted_changes(self) -> None:
        git(self.root, "checkout", "-q", "-b", "feature")
        self.no_longer_tracked_and_ignored("gone.txt")
        git(self.root, "commit", "-q", "-a", "-m", "stop tracking the file")

        snapshot = pin_reviewed_code(self.root, {"kind": "base", "value": "main"}, session_name=SESSION)

        self.assertEqual(snapshot.commit, git(self.root, "rev-parse", "HEAD"))
        self.assertEqual(snapshot.omitted, ("gone.txt",))

    def test_nested_repository_that_replaces_tracked_files(self) -> None:
        (self.root / "vendor").mkdir()
        (self.root / "vendor" / "lib.py").write_text("vendored\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "vendored files")
        git(self.root, "rm", "-q", "-r", "--cached", "vendor")
        init_repo(self.root / "vendor")
        (self.root / "vendor" / "lib.py").write_text("newer version\n", encoding="utf-8")

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertNotIn("vendor/lib.py", self.files(snapshot.commit))
        self.assertEqual(snapshot.omitted, ("vendor/lib.py",))

    def test_staged_file_that_git_cannot_store_from_the_working_copy(self) -> None:
        (self.root / "new.txt").write_text("staged\n", encoding="utf-8")
        git(self.root, "add", "new.txt")
        (self.root / "new.txt").unlink()
        os.mkfifo(self.root / "new.txt")

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(self.show(snapshot.staged, "new.txt"), "staged")
        self.assertEqual(snapshot.omitted, ("new.txt",))

    def test_directory_that_replaces_a_file_is_not_a_file_that_the_snapshot_left_out(self) -> None:
        (self.root / "gone.txt").unlink()
        (self.root / "gone.txt").mkdir()
        (self.root / "gone.txt" / "inner.txt").write_text("inner\n", encoding="utf-8")

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(snapshot.omitted, ())

    def test_commit_target_has_no_file_that_the_snapshot_left_out(self) -> None:
        git(self.root, "rm", "-q", "gone.txt")
        git(self.root, "commit", "-q", "-m", "delete the file")
        (self.root / "gone.txt").write_text("back on the disk\n", encoding="utf-8")

        snapshot = pin_reviewed_code(self.root, {"kind": "commit", "value": "HEAD"}, session_name=SESSION)

        self.assertEqual(snapshot.omitted, ())

    def test_more_files_than_the_limit_give_no_list(self) -> None:
        self.no_longer_tracked_and_ignored("gone.txt")

        with mock.patch.object(review_snapshot, "OMITTED_LIMIT", 0):
            snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertIsNone(snapshot.omitted)


class IndexFlagTest(SnapshotTestCase):
    def test_file_that_git_assumes_unchanged_is_in_the_snapshot_as_it_is_on_the_disk(self) -> None:
        for flag, name in (("--assume-unchanged", "a.txt"), ("--skip-worktree", "gone.txt")):
            git(self.root, "update-index", flag, name)
            (self.root / name).write_text(f"edited behind {flag}\n", encoding="utf-8")
        flags_before = git(self.root, "ls-files", "-v")

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(self.show(snapshot.commit, "a.txt"), "edited behind --assume-unchanged")
        self.assertEqual(self.show(snapshot.commit, "gone.txt"), "edited behind --skip-worktree")
        self.assertEqual(git(self.root, "ls-files", "-v"), flags_before)
        self.assertIn("h a.txt", flags_before)

    def test_file_with_the_two_flags_is_in_the_snapshot_as_it_is_on_the_disk(self) -> None:
        git(self.root, "update-index", "--assume-unchanged", "a.txt")
        git(self.root, "update-index", "--skip-worktree", "a.txt")
        (self.root / "a.txt").write_text("edited behind the two flags\n", encoding="utf-8")

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(self.show(snapshot.commit, "a.txt"), "edited behind the two flags")

    def test_deleted_file_that_git_assumes_unchanged_is_not_in_the_snapshot(self) -> None:
        git(self.root, "update-index", "--assume-unchanged", "gone.txt")
        (self.root / "gone.txt").unlink()

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(self.files(snapshot.commit), [".gitignore", "a.txt"])

    def test_file_that_a_sparse_checkout_left_out_stays_in_the_snapshot(self) -> None:
        git(self.root, "update-index", "--skip-worktree", "gone.txt")
        (self.root / "gone.txt").unlink()
        (self.root / "a.txt").write_text("changed\n", encoding="utf-8")

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(self.show(snapshot.commit, "gone.txt"), "gone")
        self.assertEqual(self.show(snapshot.commit, "a.txt"), "changed")


class ChangeStartTest(SnapshotTestCase):
    def commit(self, message: str) -> str:
        git(self.root, "commit", "-q", "--allow-empty", "-m", message)
        return git(self.root, "rev-parse", "HEAD")

    def test_uncommitted_change_starts_from_head(self) -> None:
        (self.root / "gone.txt").unlink()

        snapshot = pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

        self.assertEqual(snapshot.base, self.head)
        self.assertEqual(self.show(snapshot.base, "gone.txt"), "gone")
        self.assertNotIn("gone.txt", self.files(snapshot.commit))

    def test_base_target_starts_from_the_commit_that_the_branch_left_the_base_at(self) -> None:
        git(self.root, "checkout", "-q", "-b", "feature")
        git(self.root, "rm", "-q", "gone.txt")
        self.commit("delete a file")
        self.commit("later commit of the branch")
        git(self.root, "checkout", "-q", "main")
        self.commit("the base moves on")
        git(self.root, "checkout", "-q", "feature")

        snapshot = pin_reviewed_code(self.root, {"kind": "base", "value": "main"}, session_name=SESSION)

        self.assertEqual(snapshot.base, self.head)
        self.assertEqual(git(self.root, "merge-base", "--is-ancestor", snapshot.base, snapshot.commit), "")

    def test_commit_target_starts_from_its_first_parent(self) -> None:
        second = self.commit("second")

        snapshot = pin_reviewed_code(self.root, {"kind": "commit", "value": second}, session_name=SESSION)

        self.assertEqual(snapshot.base, self.head)

    def test_change_without_a_start_still_gets_its_snapshot(self) -> None:
        fresh = Path(self.tmp.name) / "fresh"
        init_repo(fresh)
        (fresh / "first.txt").write_text("first\n", encoding="utf-8")
        git(self.root, "checkout", "-q", "--orphan", "lone")
        self.commit("no shared history with main")
        cases = {
            "first commit as the target": (self.root, {"kind": "commit", "value": self.head}),
            "repository without a commit": (fresh, UNCOMMITTED),
            "base without a shared history": (self.root, {"kind": "base", "value": "main"}),
            "base that does not resolve": (self.root, {"kind": "base", "value": "no-such-branch"}),
        }
        for name, (root, target) in cases.items():
            with self.subTest(name):
                snapshot = pin_reviewed_code(root, target, session_name=SESSION)

                self.assertIsNone(snapshot.base)
                self.assertEqual(git(root, "rev-parse", snapshot.ref), snapshot.commit)


class CommitTargetSnapshotTest(SnapshotTestCase):
    def test_commit_target_pins_the_commit_and_not_the_working_tree(self) -> None:
        (self.root / "a.txt").write_text("later edit\n", encoding="utf-8")

        snapshot = pin_reviewed_code(
            self.root, {"kind": "commit", "value": self.head[:12]}, session_name=SESSION
        )

        self.assertEqual(snapshot.commit, self.head)
        self.assertEqual(git(self.root, "rev-parse", snapshot.ref), self.head)

    def test_commit_target_that_does_not_resolve_is_a_snapshot_error(self) -> None:
        with self.assertRaisesRegex(SnapshotError, "does not resolve"):
            pin_reviewed_code(self.root, {"kind": "commit", "value": "0" * 40}, session_name=SESSION)
        self.assertEqual(git(self.root, "for-each-ref", "refs/multi-shot-review"), "")

    def test_commit_target_works_in_a_bare_repository(self) -> None:
        bare = Path(self.tmp.name) / "bare.git"
        git(self.root, "clone", "-q", "--bare", str(self.root), str(bare))

        snapshot = pin_reviewed_code(bare, {"kind": "commit", "value": self.head}, session_name=SESSION)

        self.assertEqual(snapshot.commit, self.head)
        self.assertEqual(Path(snapshot.git_dir), bare.resolve())


class NoSnapshotTest(SnapshotTestCase):
    def test_directory_outside_git_has_no_snapshot(self) -> None:
        plain = Path(self.tmp.name) / "plain"
        plain.mkdir()
        (plain / "notes.txt").write_text("notes\n", encoding="utf-8")

        for target in (UNCOMMITTED, {"kind": "base", "value": "main"}, {"kind": "commit", "value": "HEAD"}):
            with self.subTest(target=target):
                self.assertIsNone(pin_reviewed_code(plain, target, session_name=SESSION))

    def test_root_that_is_gone_has_no_snapshot(self) -> None:
        self.assertIsNone(
            pin_reviewed_code(Path(self.tmp.name) / "missing", UNCOMMITTED, session_name=SESSION)
        )

    def test_missing_git_program_has_no_snapshot(self) -> None:
        with mock.patch.dict(os.environ, {"PATH": str(Path(self.tmp.name) / "no-bin")}):
            self.assertIsNone(pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION))

    def test_working_tree_target_in_a_bare_repository_has_no_snapshot(self) -> None:
        bare = Path(self.tmp.name) / "bare.git"
        git(self.root, "clone", "-q", "--bare", str(self.root), str(bare))

        self.assertIsNone(pin_reviewed_code(bare, UNCOMMITTED, session_name=SESSION))
        self.assertEqual(git(bare, "for-each-ref", "refs/multi-shot-review"), "")



class SnapshotErrorTest(SnapshotTestCase):
    def test_review_directory_with_another_name_is_a_snapshot_error(self) -> None:
        for name in ("my-review", "../escape", f"{SESSION}/extra", ""):
            with self.subTest(name=name), self.assertRaisesRegex(SnapshotError, "no session time"):
                pin_reviewed_code(self.root, UNCOMMITTED, session_name=name)
        self.assertEqual(git(self.root, "for-each-ref", "refs/multi-shot-review"), "")

    def test_session_older_than_the_retention_is_a_snapshot_error_and_gets_no_ref(self) -> None:
        now = datetime(2026, 11, 22, tzinfo=timezone.utc)

        with self.assertRaisesRegex(SnapshotError, "older than 30 days"):
            pin_reviewed_code(self.root, UNCOMMITTED, session_name="20261022-2359-ab12cd34", now=now)
        self.assertEqual(git(self.root, "for-each-ref", "refs/multi-shot-review"), "")

        at_the_limit = pin_reviewed_code(self.root, UNCOMMITTED, session_name="20261023-0000-ab12cd34", now=now)
        self.assertEqual(prune_expired_snapshots(self.root, now=now), [])
        self.assertEqual(git(self.root, "rev-parse", at_the_limit.ref), self.head)

    def test_repository_that_git_cannot_read_is_a_snapshot_error_and_not_a_missing_repository(self) -> None:
        (self.root / ".git" / "config").write_text("not a config line\n", encoding="utf-8")

        with self.assertRaisesRegex(SnapshotError, "git rev-parse failed: .*bad config"):
            pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)
        with self.assertRaisesRegex(SnapshotError, "git for-each-ref failed: .*bad config"):
            prune_expired_snapshots(self.root)

    def test_git_messages_in_another_language_do_not_change_the_result(self) -> None:
        plain = Path(self.tmp.name) / "plain"
        plain.mkdir()

        with mock.patch.dict(os.environ, {"LC_ALL": "de_DE.UTF-8", "LANGUAGE": "de"}):
            self.assertIsNone(pin_reviewed_code(plain, UNCOMMITTED, session_name=SESSION))

    def test_ref_that_git_refuses_is_a_snapshot_error_with_the_git_message(self) -> None:
        hook = self.root / ".git" / "hooks" / "reference-transaction"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)

        with self.assertRaisesRegex(SnapshotError, "git update-ref failed: .*reference-transaction"):
            pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)

    def test_git_that_does_not_finish_is_a_snapshot_error(self) -> None:
        timeout = subprocess.TimeoutExpired(["git"], review_snapshot.GIT_TIMEOUT_SECONDS)

        with (
            mock.patch.object(review_snapshot.subprocess, "run", side_effect=timeout),
            self.assertRaisesRegex(SnapshotError, "git rev-parse did not finish in 120 seconds"),
        ):
            pin_reviewed_code(self.root, UNCOMMITTED, session_name=SESSION)


class PruneTest(SnapshotTestCase):
    def test_prune_deletes_only_the_refs_of_sessions_older_than_the_retention(self) -> None:
        refs = {
            "expired": "refs/multi-shot-review/20261001-0000-aaaaaaaa/" + self.head,
            "recent": "refs/multi-shot-review/20261120-0000-bbbbbbbb/" + self.head,
            "foreign": "refs/multi-shot-review/keep-me/1",
            "bad-date": "refs/multi-shot-review/20269999-0000-cccccccc/1",
            "branch": "refs/heads/20261001-0000-aaaaaaaa/1",
        }
        for ref in refs.values():
            git(self.root, "update-ref", ref, self.head)

        deleted = prune_expired_snapshots(self.root, now=datetime(2026, 11, 22, tzinfo=timezone.utc))

        self.assertEqual(deleted, [refs["expired"]])
        remaining = git(self.root, "for-each-ref", "--format=%(refname)").splitlines()
        self.assertNotIn(refs["expired"], remaining)
        for kept in ("recent", "foreign", "bad-date", "branch"):
            self.assertIn(refs[kept], remaining)

    def test_ref_expires_one_minute_after_the_retention(self) -> None:
        now = datetime(2026, 11, 22, 12, 30, tzinfo=timezone.utc)
        refs = {
            minutes: f"refs/multi-shot-review/{(now - review_snapshot.RETENTION - timedelta(minutes=minutes)):%Y%m%d-%H%M}-aaaaaaaa/{self.head}"
            for minutes in (0, 1)
        }
        for ref in refs.values():
            git(self.root, "update-ref", ref, self.head)

        self.assertEqual(prune_expired_snapshots(self.root, now=now), [refs[1]])
        self.assertEqual(git(self.root, "for-each-ref", "--format=%(refname)", "refs/multi-shot-review"), refs[0])

    def test_prune_outside_git_does_nothing(self) -> None:
        plain = Path(self.tmp.name) / "plain"
        plain.mkdir()

        self.assertEqual(prune_expired_snapshots(plain), [])

    def test_prune_that_git_refuses_is_a_snapshot_error(self) -> None:
        git(self.root, "update-ref", "refs/multi-shot-review/20261001-0000-aaaaaaaa/" + self.head, self.head)
        hook = self.root / ".git" / "hooks" / "reference-transaction"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)

        with self.assertRaisesRegex(SnapshotError, "git update-ref failed"):
            prune_expired_snapshots(self.root, now=datetime(2026, 11, 22, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
