from __future__ import annotations

import datetime as dt
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "worktree_janitor.py"
SPEC = importlib.util.spec_from_file_location("worktree_janitor", SCRIPT)
janitor_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = janitor_module
SPEC.loader.exec_module(janitor_module)


def run_git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


class JanitorTests(unittest.TestCase):
    NOW = dt.datetime(2026, 8, 5, 12, 0, tzinfo=dt.timezone.utc)

    def setUp(self) -> None:
        if not shutil.which("git"):
            self.skipTest("git unavailable")
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "worktrees"
        self.root.mkdir()
        self.cache = self.base / "cache"
        self.repo = self.base / "source repo"
        run_git("init", "-q", str(self.repo))
        run_git("config", "user.name", "Test User", cwd=self.repo)
        run_git("config", "user.email", "test@example.invalid", cwd=self.repo)
        (self.repo / "tracked.txt").write_text("base\n", encoding="utf-8")
        run_git("add", "tracked.txt", cwd=self.repo)
        run_git("commit", "-qm", "chore: initial", cwd=self.repo)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add_worktree(self, name: str = "candidate", *, age: dt.timedelta = dt.timedelta(days=8)) -> Path:
        path = self.root / name
        run_git("worktree", "add", "-q", "--detach", str(path), "HEAD", cwd=self.repo)
        stamp = (self.NOW - age).timestamp()
        os.utime(path / ".git", (stamp, stamp))
        return path

    def janitor(self, **kwargs):
        return janitor_module.Janitor(
            self.root,
            self.cache,
            janitor_module.Policy(),
            now=self.NOW,
            active_paths=kwargs.get("active_paths", lambda: set()),
        )

    def expired_record(self, ref: str, oid: str) -> dict:
        return {
            "id": "expired",
            "repository": str(self.repo / ".git"),
            "ref": ref,
            "oid": oid,
            "head": oid,
            "kind": "dirty-snapshot",
            "original_path": str(self.root / "gone"),
            "created_at": "2026-06-01T00:00:00Z",
            "removed_at": "2026-06-01T00:00:00Z",
            "expires_at": "2026-07-01T00:00:00Z",
        }

    def test_dry_run_is_immutable_and_clean_apply_removes_ignored_files(self) -> None:
        worktree = self.add_worktree(age=dt.timedelta(hours=48))
        (worktree / ".gitignore").write_text("ignored.bin\n", encoding="utf-8")
        run_git("add", ".gitignore", cwd=worktree)
        run_git("commit", "-qm", "chore: ignore artifact", cwd=worktree)
        (worktree / "ignored.bin").write_text("large disposable data", encoding="utf-8")

        report, code = self.janitor().sweep(False)
        self.assertEqual(code, 0)
        self.assertEqual(report["planned"]["clean_worktrees_removed"], 1)
        self.assertTrue(worktree.exists())
        self.assertFalse((self.cache / "state.json").exists())

        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 0, report)
        self.assertFalse(worktree.exists())
        self.assertEqual(report["applied"]["clean_worktrees_removed"], 1)

    def test_dry_run_preserves_dirty_worktree_target_refs_and_state(self) -> None:
        dirty = self.add_worktree("dirty-expired", age=dt.timedelta(days=8))
        (dirty / "tracked.txt").write_text("unfinished\n", encoding="utf-8")
        grace = self.add_worktree("dirty-grace", age=dt.timedelta(days=3))
        (grace / "unfinished.txt").write_text("keep", encoding="utf-8")
        target = grace / "target"
        target.mkdir()
        (target / ".rustc_info.json").write_text("{}", encoding="utf-8")
        refs_before = run_git("for-each-ref", "--format=%(refname) %(objectname)", janitor_module.ARCHIVE_PREFIX, cwd=self.repo)

        report, code = self.janitor().sweep(False)
        self.assertEqual(code, 0, report)
        self.assertTrue(dirty.exists())
        self.assertTrue(target.exists())
        self.assertFalse((self.cache / "state.json").exists())
        self.assertEqual(
            run_git("for-each-ref", "--format=%(refname) %(objectname)", janitor_module.ARCHIVE_PREFIX, cwd=self.repo),
            refs_before,
        )

    def test_dirty_grace_removes_only_verified_cargo_target(self) -> None:
        worktree = self.add_worktree(age=dt.timedelta(days=3))
        (worktree / "unfinished.txt").write_text("keep", encoding="utf-8")
        target = worktree / "target"
        target.mkdir()
        (target / ".rustc_info.json").write_text("{}", encoding="utf-8")
        unverified = worktree / "nested" / "target"
        unverified.mkdir(parents=True)
        (unverified / "payload").write_text("keep", encoding="utf-8")

        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 0, report)
        self.assertTrue(worktree.exists())
        self.assertFalse(target.exists())
        self.assertTrue(unverified.exists())
        self.assertEqual(report["skipped"]["dirty_grace"], 1)

    def test_symlinked_cargo_evidence_does_not_authorize_removal(self) -> None:
        worktree = self.add_worktree(age=dt.timedelta(days=3))
        (worktree / "unfinished.txt").write_text("keep", encoding="utf-8")
        evidence = self.base / "evidence"
        evidence.mkdir()
        marker = evidence / "marker"
        marker.write_text(f"{janitor_module.CARGO_CACHE_SIGNATURE}\n", encoding="utf-8")
        manifest = evidence / "Cargo.toml"
        manifest.write_text("[package]\nname='outside'\nversion='0.1.0'\n", encoding="utf-8")
        targets = []
        for parent, name, source in (
            ("rustc", ".rustc_info.json", marker),
            ("tag", "CACHEDIR.TAG", marker),
        ):
            target = worktree / parent / "target"
            target.mkdir(parents=True)
            (target / "valuable").write_text("keep", encoding="utf-8")
            (target / name).symlink_to(source)
            targets.append(target)
        manifest_parent = worktree / "manifest"
        target = manifest_parent / "target"
        target.mkdir(parents=True)
        (target / "valuable").write_text("keep", encoding="utf-8")
        (manifest_parent / "Cargo.toml").symlink_to(manifest)
        targets.append(target)

        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["planned"].get("target_directories_removed", 0), 0)
        self.assertTrue(all(target.exists() for target in targets))

    def test_dirty_snapshot_removal_and_detached_restore_with_spaces(self) -> None:
        worktree = self.add_worktree("dirty worktree", age=dt.timedelta(days=7))
        (worktree / "tracked.txt").write_text("changed\n", encoding="utf-8")
        (worktree / "new file.txt").write_text("recover me\n", encoding="utf-8")

        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 0, report)
        self.assertFalse(worktree.exists())
        archive_id = report["events"][-1]["archive_id"]

        destination = self.root / "restored worktree"
        restored, code = self.janitor().restore(archive_id, destination)
        self.assertEqual(code, 0, restored)
        self.assertEqual((destination / "tracked.txt").read_text(), "changed\n")
        self.assertEqual((destination / "new file.txt").read_text(), "recover me\n")
        self.assertNotEqual(
            subprocess.run(
                ["git", "symbolic-ref", "-q", "HEAD"],
                cwd=destination,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode,
            0,
        )

    def test_retry_creates_a_fresh_dirty_snapshot(self) -> None:
        worktree = self.add_worktree(age=dt.timedelta(days=8))
        (worktree / "tracked.txt").write_text("first change\n", encoding="utf-8")
        first_janitor = self.janitor()
        first_janitor.state.load()
        first = first_janitor.create_archive(first_janitor.inspect(worktree), "dirty-snapshot")
        (worktree / "tracked.txt").write_text("latest change\n", encoding="utf-8")
        (worktree / "latest.txt").write_text("latest untracked\n", encoding="utf-8")

        second_janitor = janitor_module.Janitor(
            self.root,
            self.cache,
            janitor_module.Policy(),
            now=self.NOW + dt.timedelta(minutes=1),
            active_paths=lambda: set(),
        )
        report, code = second_janitor.sweep(True)
        self.assertEqual(code, 0, report)
        second_id = next(event["archive_id"] for event in report["events"] if "archive_id" in event)
        self.assertNotEqual(first["id"], second_id)
        destination = self.root / "retry restored"
        restored, code = second_janitor.restore(second_id, destination)
        self.assertEqual(code, 0, restored)
        self.assertEqual((destination / "tracked.txt").read_text(), "latest change\n")
        self.assertEqual((destination / "latest.txt").read_text(), "latest untracked\n")
        state = json.loads((self.cache / "state.json").read_text())
        self.assertTrue(all(record["expires_at"] is not None for record in state["archives"]))

    def test_same_porcelain_status_content_race_preserves_worktree(self) -> None:
        worktree = self.add_worktree(age=dt.timedelta(days=8))
        (worktree / "tracked.txt").write_text("audited change\n", encoding="utf-8")

        class RacingJanitor(janitor_module.Janitor):
            def create_archive(self, candidate, kind):
                record = super().create_archive(candidate, kind)
                if kind == "dirty-snapshot":
                    (candidate.path / "tracked.txt").write_text("late change\n", encoding="utf-8")
                return record

        report, code = RacingJanitor(
            self.root,
            self.cache,
            janitor_module.Policy(),
            now=self.NOW,
            active_paths=lambda: set(),
        ).sweep(True)
        self.assertEqual(code, 2, report)
        self.assertTrue(worktree.exists())
        self.assertIn("content changed", report["failures"][0]["error"])

    def test_assume_unchanged_edit_is_archived(self) -> None:
        worktree = self.add_worktree("assume-unchanged", age=dt.timedelta(days=8))
        run_git("update-index", "--assume-unchanged", "tracked.txt", cwd=worktree)
        (worktree / "tracked.txt").write_text("hidden edit\n", encoding="utf-8")

        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 0, report)
        self.assertFalse(worktree.exists())
        archive_id = next(event["archive_id"] for event in report["events"] if "archive_id" in event)
        destination = self.root / "restored-assume-unchanged"
        restored, code = self.janitor().restore(archive_id, destination)
        self.assertEqual(code, 0, restored)
        self.assertEqual((destination / "tracked.txt").read_text(), "hidden edit\n")

    def test_sparse_snapshot_keeps_excluded_head_paths(self) -> None:
        (self.repo / "included").mkdir()
        (self.repo / "included" / "visible.txt").write_text("base\n", encoding="utf-8")
        (self.repo / "excluded").mkdir()
        (self.repo / "excluded" / "preserved.txt").write_text("from head\n", encoding="utf-8")
        run_git("add", "included", "excluded", cwd=self.repo)
        run_git("commit", "-qm", "chore: sparse fixture", cwd=self.repo)
        worktree = self.add_worktree("sparse", age=dt.timedelta(days=8))
        run_git("sparse-checkout", "init", "--cone", cwd=worktree)
        run_git("sparse-checkout", "set", "included", cwd=worktree)
        (worktree / "included" / "visible.txt").write_text("changed\n", encoding="utf-8")

        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 0, report)
        archive_id = next(event["archive_id"] for event in report["events"] if "archive_id" in event)
        destination = self.root / "restored-sparse"
        restored, code = self.janitor().restore(archive_id, destination)
        self.assertEqual(code, 0, restored)
        self.assertEqual((destination / "included" / "visible.txt").read_text(), "changed\n")
        self.assertEqual((destination / "excluded" / "preserved.txt").read_text(), "from head\n")

    def test_split_index_dirty_snapshot_restores(self) -> None:
        worktree = self.add_worktree("split-index", age=dt.timedelta(days=8))
        run_git("update-index", "--split-index", cwd=worktree)
        (worktree / "tracked.txt").write_text("split-index edit\n", encoding="utf-8")

        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 0, report)
        archive_id = next(event["archive_id"] for event in report["events"] if "archive_id" in event)
        destination = self.root / "restored-split-index"
        restored, code = self.janitor().restore(archive_id, destination)
        self.assertEqual(code, 0, restored)
        self.assertEqual((destination / "tracked.txt").read_text(), "split-index edit\n")

    def test_worktree_locked_after_scan_is_preserved(self) -> None:
        worktree = self.add_worktree(age=dt.timedelta(days=8))

        class LockingJanitor(janitor_module.Janitor):
            validations = 0

            def revalidate_identity(self, candidate):
                self.validations += 1
                if self.validations == 2:
                    run_git("worktree", "lock", str(candidate.path), cwd=self.repo)
                return super().revalidate_identity(candidate)

        candidate = LockingJanitor(
            self.root,
            self.cache,
            janitor_module.Policy(),
            now=self.NOW,
            active_paths=lambda: set(),
        )
        candidate.repo = self.repo
        report, code = candidate.sweep(True)
        self.assertEqual(code, 2, report)
        self.assertTrue(worktree.exists())
        self.assertEqual(report["applied"]["archives_created"], 1)
        self.assertIn("became locked", report["failures"][0]["error"])

    def test_recreated_worktree_generation_is_preserved(self) -> None:
        worktree = self.add_worktree(age=dt.timedelta(days=8))

        class RecreatingJanitor(janitor_module.Janitor):
            recreated = False

            def revalidate_identity(self, candidate):
                if not self.recreated:
                    self.recreated = True
                    run_git("worktree", "remove", "--force", str(candidate.path), cwd=self.repo)
                    run_git("worktree", "add", "-q", "--detach", str(candidate.path), candidate.head, cwd=self.repo)
                    stamp = (self.now - dt.timedelta(days=8)).timestamp()
                    os.utime(candidate.path / ".git", (stamp, stamp))
                return super().revalidate_identity(candidate)

        janitor = RecreatingJanitor(
            self.root,
            self.cache,
            janitor_module.Policy(),
            now=self.NOW,
            active_paths=lambda: set(),
        )
        janitor.repo = self.repo
        report, code = janitor.sweep(True)
        self.assertEqual(code, 0, report)
        self.assertTrue(worktree.exists())
        self.assertEqual(report["skipped"]["changed_after_scan"], 1)

    def test_worktree_active_after_scan_keeps_cargo_target(self) -> None:
        worktree = self.add_worktree(age=dt.timedelta(days=3))
        (worktree / "unfinished.txt").write_text("keep", encoding="utf-8")
        target = worktree / "target"
        target.mkdir()
        (target / ".rustc_info.json").write_text("{}", encoding="utf-8")
        calls = 0

        def active_paths():
            nonlocal calls
            calls += 1
            return set() if calls == 1 else {worktree}

        report, code = self.janitor(active_paths=active_paths).sweep(True)
        self.assertEqual(code, 0, report)
        self.assertTrue(target.exists())
        self.assertEqual(report["skipped"]["active"], 1)

    def test_activity_audit_failure_aborts_remaining_apply(self) -> None:
        first = self.add_worktree("a-first")
        second = self.add_worktree("b-second")
        third = self.add_worktree("c-third")
        calls = 0

        def activity_audit():
            nonlocal calls
            calls += 1
            if calls >= 4:
                raise janitor_module.JanitorError("activity audit unavailable")
            return set()

        report, code = self.janitor(active_paths=activity_audit).sweep(True)
        self.assertEqual(code, 2, report)
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())
        self.assertTrue(third.exists())
        self.assertEqual(report["applied"]["clean_worktrees_removed"], 1)

    def test_target_redirected_to_another_worktree_is_preserved(self) -> None:
        worktree = self.add_worktree("cleanup-candidate", age=dt.timedelta(days=3))
        (worktree / "unfinished.txt").write_text("keep", encoding="utf-8")
        original_parent = worktree / "nested"
        original_target = original_parent / "target"
        original_target.mkdir(parents=True)
        (original_target / ".rustc_info.json").write_text("{}", encoding="utf-8")
        other = self.add_worktree("other-worktree", age=dt.timedelta(hours=1))
        other_parent = other / "nested"
        other_target = other_parent / "target"
        other_target.mkdir(parents=True)
        (other_target / ".rustc_info.json").write_text("{}", encoding="utf-8")
        (other_target / "valuable").write_text("keep", encoding="utf-8")

        class RedirectingJanitor(janitor_module.Janitor):
            redirected = False

            def ensure_inactive(self, candidate):
                super().ensure_inactive(candidate)
                if candidate.path == worktree.resolve() and not self.redirected:
                    self.redirected = True
                    shutil.rmtree(original_parent)
                    original_parent.symlink_to(other_parent, target_is_directory=True)

        report, code = RedirectingJanitor(
            self.root,
            self.cache,
            janitor_module.Policy(),
            now=self.NOW,
            active_paths=lambda: set(),
        ).sweep(True)
        self.assertEqual(code, 0, report)
        self.assertTrue(other_target.exists())
        self.assertTrue((other_target / "valuable").exists())
        self.assertEqual(report["skipped"]["changed_after_scan"], 1)

    def test_untracked_nested_repository_is_safety_held(self) -> None:
        worktree = self.add_worktree(age=dt.timedelta(days=30))
        (worktree / ".gitignore").write_text("nested-repo/\n", encoding="utf-8")
        run_git("add", ".gitignore", cwd=worktree)
        run_git("commit", "-qm", "chore: ignore nested repository", cwd=worktree)
        nested = worktree / "nested-repo"
        run_git("init", "-q", str(nested))
        run_git("config", "user.name", "Test User", cwd=nested)
        run_git("config", "user.email", "test@example.invalid", cwd=nested)
        (nested / "valuable.txt").write_text("not in parent snapshot\n", encoding="utf-8")

        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 0, report)
        self.assertTrue(worktree.exists())
        self.assertEqual(report["skipped"]["embedded_repository"], 1)

    def test_ignored_nested_bare_repository_is_safety_held(self) -> None:
        worktree = self.add_worktree("nested-bare", age=dt.timedelta(days=30))
        (worktree / ".gitignore").write_text("nested.git/\n", encoding="utf-8")
        run_git("add", ".gitignore", cwd=worktree)
        run_git("commit", "-qm", "chore: ignore nested bare repository", cwd=worktree)
        nested = worktree / "nested.git"
        run_git("init", "--bare", "-q", str(nested))

        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 0, report)
        self.assertTrue(nested.exists())
        self.assertEqual(report["skipped"]["embedded_repository"], 1)

    def test_unreferenced_clean_head_is_archived(self) -> None:
        worktree = self.add_worktree()
        (worktree / "tracked.txt").write_text("unique\n", encoding="utf-8")
        run_git("add", "tracked.txt", cwd=worktree)
        run_git("commit", "-qm", "chore: unique detached commit", cwd=worktree)

        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 0, report)
        self.assertFalse(worktree.exists())
        self.assertEqual(report["applied"]["archives_created"], 1)
        state = json.loads((self.cache / "state.json").read_text())
        self.assertEqual(state["archives"][0]["kind"], "detached-head")
        self.assertIsNotNone(state["archives"][0]["expires_at"])
        destination = self.root / "restored-detached"
        restored, code = self.janitor().restore(state["archives"][0]["id"], destination)
        self.assertEqual(code, 0, restored)
        self.assertEqual((destination / "tracked.txt").read_text(), "unique\n")

    def test_recreated_path_gets_a_fresh_recovery_window(self) -> None:
        worktree = self.add_worktree("recreated-path")
        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 0, report)
        first = json.loads((self.cache / "state.json").read_text())["archives"][0]
        later_now = self.NOW + dt.timedelta(days=29)
        run_git("worktree", "add", "-q", "--detach", str(worktree), first["head"], cwd=self.repo)
        stamp = (later_now - dt.timedelta(days=8)).timestamp()
        os.utime(worktree / ".git", (stamp, stamp))

        later = janitor_module.Janitor(
            self.root,
            self.cache,
            janitor_module.Policy(),
            now=later_now,
            active_paths=lambda: set(),
        )
        report, code = later.sweep(True)
        self.assertEqual(code, 0, report)
        records = json.loads((self.cache / "state.json").read_text())["archives"]
        self.assertEqual(len(records), 2)
        self.assertEqual(janitor_module.parse_time(records[1]["expires_at"]), later_now + dt.timedelta(days=30))

    def test_post_removal_state_failure_reports_the_completed_removal(self) -> None:
        worktree = self.add_worktree("state-save-failure")
        (worktree / "tracked.txt").write_text("unique\n", encoding="utf-8")
        run_git("commit", "-qam", "chore: unique detached commit", cwd=worktree)
        candidate = self.janitor()
        original_save = candidate.state.save
        saves = 0

        def fail_second_save():
            nonlocal saves
            saves += 1
            if saves == 2:
                raise OSError("disk full")
            original_save()

        candidate.state.save = fail_second_save
        report, code = candidate.sweep(True)
        self.assertEqual(code, 2, report)
        self.assertFalse(worktree.exists())
        self.assertEqual(report["applied"]["clean_worktrees_removed"], 1)
        self.assertEqual(report["failures"][0]["operation"], "save_archive_removal_state")
        record = json.loads((self.cache / "state.json").read_text())["archives"][0]
        self.assertIsNone(record["expires_at"])
        self.assertTrue(janitor_module.ref_matches(Path(record["repository"]), record["ref"], record["oid"]))
        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["applied"]["archives_finalized"], 1)
        record = json.loads((self.cache / "state.json").read_text())["archives"][0]
        self.assertIsNotNone(record["expires_at"])

    def test_archive_creation_failure_rolls_back_and_preserves_worktree(self) -> None:
        worktree = self.add_worktree("archive-failure")
        (worktree / "tracked.txt").write_text("unique\n", encoding="utf-8")
        run_git("commit", "-qam", "chore: unique detached commit", cwd=worktree)
        candidate = self.janitor()
        candidate.state.save = mock.Mock(side_effect=OSError("disk full"))

        report, code = candidate.sweep(True)
        self.assertEqual(code, 1, report)
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["failures"][0]["operation"], "create_archive")
        self.assertTrue(worktree.exists())
        self.assertEqual(
            run_git("for-each-ref", "--format=%(refname)", janitor_module.ARCHIVE_PREFIX, cwd=self.repo),
            "",
        )

    def test_worktree_removal_failure_keeps_archive_and_retries(self) -> None:
        worktree = self.add_worktree("remove-failure")

        class FailingRemovalJanitor(janitor_module.Janitor):
            def remove_worktree(self, candidate, **kwargs):
                raise janitor_module.MutationError("injected remove failure")

        candidate = FailingRemovalJanitor(
            self.root,
            self.cache,
            janitor_module.Policy(),
            now=self.NOW,
            active_paths=lambda: set(),
        )
        report, code = candidate.sweep(True)
        self.assertEqual(code, 2, report)
        self.assertTrue(worktree.exists())
        record = json.loads((self.cache / "state.json").read_text())["archives"][0]
        self.assertIsNone(record["expires_at"])
        self.assertTrue(janitor_module.ref_matches(Path(record["repository"]), record["ref"], record["oid"]))

        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 0, report)
        self.assertFalse(worktree.exists())
        record = json.loads((self.cache / "state.json").read_text())["archives"][0]
        self.assertIsNotNone(record["expires_at"])

    def orphan_worktree(self, name="orphan", *, age=dt.timedelta(days=7)):
        path = self.add_worktree(name, age=age)
        admin = janitor_module.parse_git_file(path / ".git")
        shutil.rmtree(admin)
        return path, admin

    def test_old_orphan_is_reported_then_removed_without_git_archive(self) -> None:
        path, _ = self.orphan_worktree()
        (path / "unfinished.txt").write_text("abandoned", encoding="utf-8")
        (path / ".review" / "session" / ".git").mkdir(parents=True)
        report, code = self.janitor().sweep(False)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["planned"].get("orphan_worktrees_removed"), 1)
        self.assertTrue(path.exists())
        self.assertFalse(self.cache.exists())
        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 0, report)
        self.assertFalse(path.exists())
        self.assertEqual(report["applied"]["orphan_worktrees_removed"], 1)
        event = next(e for e in report["events"] if e.get("action") == "remove_orphan_worktree")
        self.assertFalse(event["recoverable"])
        self.assertFalse((self.cache / "state.json").exists())

    def test_deleted_source_repository_orphan_is_removed(self) -> None:
        path, _ = self.orphan_worktree()
        shutil.rmtree(self.repo)
        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 0, report)
        self.assertFalse(path.exists())
        self.assertEqual(report["applied"]["orphan_worktrees_removed"], 1)

    def test_nonstandard_missing_pointer_is_not_an_orphan(self) -> None:
        path = self.root / "ordinary-directory"
        path.mkdir()
        (path / ".git").write_text("gitdir: /tmp/worktrees/gone\n", encoding="utf-8")
        stamp = (self.NOW - dt.timedelta(days=30)).timestamp()
        os.utime(path / ".git", (stamp, stamp))
        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 0, report)
        self.assertTrue(path.exists())
        self.assertEqual(report["skipped"]["invalid"], 1)

    def test_recent_active_and_nested_repository_orphans_are_preserved(self) -> None:
        recent, _ = self.orphan_worktree("recent-orphan", age=dt.timedelta(days=7, seconds=-1))
        active, _ = self.orphan_worktree("active-orphan")
        nested, _ = self.orphan_worktree("nested-orphan")
        run_git("init", "-q", str(nested / "repository"))
        report, code = self.janitor(active_paths=lambda: {active}).sweep(True)
        self.assertEqual(code, 0, report)
        for path in (recent, active, nested):
            self.assertTrue(path.exists())
        self.assertEqual(report["skipped"]["orphan_grace"], 1)
        self.assertEqual(report["skipped"]["active"], 1)
        self.assertEqual(report["skipped"]["embedded_repository"], 1)

    def test_orphan_repaired_after_scan_is_preserved(self) -> None:
        path, admin = self.orphan_worktree()
        candidate = self.janitor()
        original_scan = candidate.scan
        def repair_after_scan():
            result = original_scan()
            admin.mkdir()
            return result
        candidate.scan = repair_after_scan
        report, code = candidate.sweep(True)
        self.assertEqual(code, 0, report)
        self.assertTrue(path.exists())
        self.assertEqual(report["skipped"]["changed_after_scan"], 1)

    def test_orphan_active_after_scan_is_preserved(self) -> None:
        path, _ = self.orphan_worktree()
        calls = 0
        def active_paths():
            nonlocal calls
            calls += 1
            return set() if calls == 1 else {path}
        report, code = self.janitor(active_paths=active_paths).sweep(True)
        self.assertEqual(code, 0, report)
        self.assertTrue(path.exists())
        self.assertEqual(report["skipped"]["active"], 1)

    def test_orphan_pointer_replaced_after_scan_is_preserved(self) -> None:
        path, _ = self.orphan_worktree()
        candidate = self.janitor()
        original_scan = candidate.scan
        def replace_after_scan():
            result = original_scan()
            (path / ".git").write_text("gitdir: /another/worktrees/missing\n", encoding="utf-8")
            return result
        candidate.scan = replace_after_scan
        report, code = candidate.sweep(True)
        self.assertEqual(code, 0, report)
        self.assertTrue(path.exists())
        self.assertEqual(report["skipped"]["changed_after_scan"], 1)

    def test_orphan_deletion_failure_is_partial(self) -> None:
        path, _ = self.orphan_worktree()
        with mock.patch.object(janitor_module.shutil, "rmtree", side_effect=OSError("cannot delete")):
            report, code = self.janitor().sweep(True)
        self.assertEqual(code, 2, report)
        self.assertTrue(path.exists())
        self.assertEqual(report["failures"][0]["operation"], "remove_orphan_worktree")

    def test_orphan_mount_point_is_preserved(self) -> None:
        path, _ = self.orphan_worktree()
        mount = path / "mounted"
        mount.mkdir()
        with mock.patch.object(janitor_module.os.path, "ismount", side_effect=lambda p: p == mount):
            report, code = self.janitor().sweep(True)
        self.assertEqual(code, 0, report)
        self.assertTrue(path.exists())
        self.assertEqual(report["skipped"]["mount_point"], 1)

    def test_orphan_symlink_does_not_remove_external_files(self) -> None:
        path, _ = self.orphan_worktree()
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "valuable").write_text("keep", encoding="utf-8")
        (path / "linked").symlink_to(outside, target_is_directory=True)
        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 0, report)
        self.assertFalse(path.exists())
        self.assertEqual((outside / "valuable").read_text(), "keep")

    def test_active_locked_and_invalid_worktrees_are_preserved(self) -> None:
        active = self.add_worktree("active")
        locked = self.add_worktree("locked")
        run_git("worktree", "lock", str(locked), cwd=self.repo)
        invalid = self.root / "invalid"
        invalid.mkdir()
        (invalid / ".git").write_text("gitdir: /does/not/exist\n", encoding="utf-8")

        scan = self.janitor(active_paths=lambda: {active / "subdir"}).scan()
        self.assertEqual(scan.skipped["active"], 1)
        self.assertEqual(scan.skipped["locked"], 1)
        self.assertEqual(scan.skipped["invalid"], 1)
        self.assertTrue(active.exists())
        self.assertTrue(locked.exists())
        self.assertTrue(invalid.exists())

    def test_dirty_submodule_and_its_worktree_are_fully_preserved(self) -> None:
        subrepo = self.base / "subrepo"
        run_git("init", "-q", str(subrepo))
        run_git("config", "user.name", "Test User", cwd=subrepo)
        run_git("config", "user.email", "test@example.invalid", cwd=subrepo)
        (subrepo / "sub.txt").write_text("base\n", encoding="utf-8")
        run_git("add", "sub.txt", cwd=subrepo)
        run_git("commit", "-qm", "chore: submodule initial", cwd=subrepo)
        run_git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(subrepo), "module", cwd=self.repo)
        run_git("commit", "-qam", "chore: add submodule", cwd=self.repo)
        worktree = self.add_worktree("submodule dirty", age=dt.timedelta(days=60))
        run_git("-c", "protocol.file.allow=always", "submodule", "update", "--init", "-q", cwd=worktree)
        (worktree / "module" / "sub.txt").write_text("dirty\n", encoding="utf-8")
        target = worktree / "target"
        target.mkdir()
        (target / "CACHEDIR.TAG").write_text(
            f"{janitor_module.CARGO_CACHE_SIGNATURE}\n", encoding="utf-8"
        )

        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 0, report)
        self.assertTrue(worktree.exists())
        self.assertTrue(target.exists())
        self.assertEqual(report["skipped"]["dirty_submodule"], 1)

        (worktree / ".gitmodules").unlink()
        report, code = self.janitor().sweep(False)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["skipped"]["dirty_submodule"], 1)
        self.assertTrue(worktree.exists())

    def test_submodule_at_divergent_local_commit_is_preserved(self) -> None:
        subrepo = self.base / "divergent-subrepo"
        run_git("init", "-q", str(subrepo))
        run_git("config", "user.name", "Test User", cwd=subrepo)
        run_git("config", "user.email", "test@example.invalid", cwd=subrepo)
        (subrepo / "sub.txt").write_text("base\n", encoding="utf-8")
        run_git("add", "sub.txt", cwd=subrepo)
        run_git("commit", "-qm", "chore: submodule initial", cwd=subrepo)
        run_git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(subrepo), "module", cwd=self.repo)
        run_git("commit", "-qam", "chore: add submodule", cwd=self.repo)
        worktree = self.add_worktree("divergent-submodule", age=dt.timedelta(days=60))
        run_git("-c", "protocol.file.allow=always", "submodule", "update", "--init", "-q", cwd=worktree)
        run_git("config", "user.name", "Test User", cwd=worktree / "module")
        run_git("config", "user.email", "test@example.invalid", cwd=worktree / "module")
        (worktree / "module" / "sub.txt").write_text("local commit\n", encoding="utf-8")
        run_git("add", "sub.txt", cwd=worktree / "module")
        run_git("commit", "-qm", "wip: local-only submodule commit", cwd=worktree / "module")
        local_oid = run_git("rev-parse", "HEAD", cwd=worktree / "module")

        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 0, report)
        self.assertTrue(worktree.exists())
        self.assertEqual(report["skipped"]["dirty_submodule"], 1)
        self.assertEqual(run_git("rev-parse", "HEAD", cwd=worktree / "module"), local_oid)

    def test_clean_parent_with_local_only_submodule_commit_is_preserved(self) -> None:
        subrepo = self.base / "clean-local-subrepo"
        run_git("init", "-q", str(subrepo))
        run_git("config", "user.name", "Test User", cwd=subrepo)
        run_git("config", "user.email", "test@example.invalid", cwd=subrepo)
        (subrepo / "sub.txt").write_text("base\n", encoding="utf-8")
        run_git("add", "sub.txt", cwd=subrepo)
        run_git("commit", "-qm", "chore: submodule initial", cwd=subrepo)
        run_git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(subrepo), "module", cwd=self.repo)
        run_git("commit", "-qam", "chore: add submodule", cwd=self.repo)
        worktree = self.add_worktree("clean-local-submodule", age=dt.timedelta(days=60))
        run_git("-c", "protocol.file.allow=always", "submodule", "update", "--init", "-q", cwd=worktree)
        run_git("config", "user.name", "Test User", cwd=worktree / "module")
        run_git("config", "user.email", "test@example.invalid", cwd=worktree / "module")
        (worktree / "module" / "sub.txt").write_text("local commit\n", encoding="utf-8")
        run_git("commit", "-qam", "wip: local-only submodule commit", cwd=worktree / "module")
        run_git("add", "module", cwd=worktree)
        run_git("commit", "-qm", "wip: record local submodule commit", cwd=worktree)

        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 0, report)
        self.assertTrue(worktree.exists())
        self.assertEqual(report["skipped"]["dirty_submodule"], 1)

    def test_archive_expiry_deletes_only_owned_ref_and_state(self) -> None:
        oid = run_git("rev-parse", "HEAD", cwd=self.repo)
        ref = f"{janitor_module.ARCHIVE_PREFIX}/old/test"
        run_git("update-ref", ref, oid, cwd=self.repo)
        self.cache.mkdir()
        state = {"version": janitor_module.STATE_VERSION, "archives": [self.expired_record(ref, oid)]}
        (self.cache / "state.json").write_text(json.dumps(state), encoding="utf-8")

        report, code = self.janitor().sweep(False)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["planned"]["archives_expired"], 1)
        self.assertEqual(run_git("show-ref", "--verify", ref, cwd=self.repo), f"{oid} {ref}")

        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 0, report)
        self.assertNotEqual(
            subprocess.run(["git", "show-ref", "--verify", "--quiet", ref], cwd=self.repo).returncode,
            0,
        )
        self.assertEqual(json.loads((self.cache / "state.json").read_text())["archives"], [])

    def test_expiry_state_save_failure_is_partial(self) -> None:
        oid = run_git("rev-parse", "HEAD", cwd=self.repo)
        ref = f"{janitor_module.ARCHIVE_PREFIX}/old/save-failure"
        run_git("update-ref", ref, oid, cwd=self.repo)
        self.cache.mkdir()
        state = {"version": janitor_module.STATE_VERSION, "archives": [self.expired_record(ref, oid)]}
        (self.cache / "state.json").write_text(json.dumps(state), encoding="utf-8")
        candidate = self.janitor()
        candidate.state.save = mock.Mock(side_effect=OSError("disk full"))

        report, code = candidate.sweep(True)
        self.assertEqual(code, 2, report)
        self.assertEqual(report["failures"][0]["operation"], "save_archive_expiry_state")
        self.assertNotEqual(
            subprocess.run(["git", "show-ref", "--verify", "--quiet", ref], cwd=self.repo).returncode,
            0,
        )
        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 0, report)
        self.assertEqual(json.loads((self.cache / "state.json").read_text())["archives"], [])

    def test_expiry_preserves_a_repointed_archive_ref(self) -> None:
        recorded_oid = run_git("rev-parse", "HEAD", cwd=self.repo)
        (self.repo / "tracked.txt").write_text("new\n", encoding="utf-8")
        run_git("commit", "-qam", "chore: new object", cwd=self.repo)
        replacement = run_git("rev-parse", "HEAD", cwd=self.repo)
        ref = f"{janitor_module.ARCHIVE_PREFIX}/old/repointed"
        run_git("update-ref", ref, replacement, cwd=self.repo)
        self.cache.mkdir()
        state = {
            "version": janitor_module.STATE_VERSION,
            "archives": [self.expired_record(ref, recorded_oid)],
        }
        (self.cache / "state.json").write_text(json.dumps(state), encoding="utf-8")

        self.assertFalse(self.janitor().archives()["archives"][0]["available"])
        with self.assertRaises(janitor_module.JanitorError):
            self.janitor().restore("expired", self.root / "must-not-restore")
        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 1, report)
        self.assertEqual(run_git("rev-parse", ref, cwd=self.repo), replacement)
        self.assertEqual(len(json.loads((self.cache / "state.json").read_text())["archives"]), 1)

    def test_expiry_ref_inspection_failure_keeps_state(self) -> None:
        oid = run_git("rev-parse", "HEAD", cwd=self.repo)
        ref = f"{janitor_module.ARCHIVE_PREFIX}/old/inspection-failure"
        run_git("update-ref", ref, oid, cwd=self.repo)
        self.cache.mkdir()
        state = {"version": janitor_module.STATE_VERSION, "archives": [self.expired_record(ref, oid)]}
        (self.cache / "state.json").write_text(json.dumps(state), encoding="utf-8")

        with mock.patch.object(
            janitor_module,
            "ref_oid",
            side_effect=janitor_module.JanitorError("repository unreadable"),
        ):
            report, code = self.janitor().sweep(True)
        self.assertEqual(code, 1, report)
        self.assertTrue(janitor_module.ref_exists(self.repo / ".git", ref))
        self.assertEqual(len(json.loads((self.cache / "state.json").read_text())["archives"]), 1)

    def test_expiry_rejects_symbolic_archive_ref(self) -> None:
        oid = run_git("rev-parse", "HEAD", cwd=self.repo)
        branch = run_git("symbolic-ref", "HEAD", cwd=self.repo)
        ref = f"{janitor_module.ARCHIVE_PREFIX}/old/symbolic"
        run_git("symbolic-ref", ref, branch, cwd=self.repo)
        self.cache.mkdir()
        state = {"version": janitor_module.STATE_VERSION, "archives": [self.expired_record(ref, oid)]}
        (self.cache / "state.json").write_text(json.dumps(state), encoding="utf-8")

        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 1, report)
        self.assertEqual(run_git("rev-parse", branch, cwd=self.repo), oid)
        self.assertEqual(run_git("symbolic-ref", ref, cwd=self.repo), branch)
        self.assertEqual(len(json.loads((self.cache / "state.json").read_text())["archives"]), 1)

    def test_archive_expires_at_exact_boundary_not_before(self) -> None:
        oid = run_git("rev-parse", "HEAD", cwd=self.repo)
        exact_ref = f"{janitor_module.ARCHIVE_PREFIX}/boundary/exact"
        later_ref = f"{janitor_module.ARCHIVE_PREFIX}/boundary/later"
        run_git("update-ref", exact_ref, oid, cwd=self.repo)
        run_git("update-ref", later_ref, oid, cwd=self.repo)
        exact = self.expired_record(exact_ref, oid)
        exact.update({"id": "exact", "expires_at": janitor_module.iso(self.NOW)})
        later = self.expired_record(later_ref, oid)
        later.update({"id": "later", "expires_at": janitor_module.iso(self.NOW + dt.timedelta(seconds=1))})
        self.cache.mkdir()
        (self.cache / "state.json").write_text(
            json.dumps({"version": janitor_module.STATE_VERSION, "archives": [exact, later]}),
            encoding="utf-8",
        )

        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 0, report)
        self.assertFalse(janitor_module.ref_exists(self.repo / ".git", exact_ref))
        self.assertTrue(janitor_module.ref_exists(self.repo / ".git", later_ref))
        self.assertEqual(
            [record["id"] for record in json.loads((self.cache / "state.json").read_text())["archives"]],
            ["later"],
        )

    def test_state_rejects_refs_outside_owned_namespace(self) -> None:
        oid = run_git("rev-parse", "HEAD", cwd=self.repo)
        self.cache.mkdir()
        record = self.expired_record("refs/heads/main", oid)
        (self.cache / "state.json").write_text(
            json.dumps({"version": janitor_module.STATE_VERSION, "archives": [record]}),
            encoding="utf-8",
        )
        with self.assertRaises(janitor_module.JanitorError):
            self.janitor().sweep(True)
        self.assertEqual(run_git("rev-parse", "refs/heads/main", cwd=self.repo), oid)

    def test_cli_reports_non_object_state_as_json(self) -> None:
        home = self.base / "isolated-home"
        cache = home / ".cache" / "worktree-janitor"
        cache.mkdir(parents=True)
        (cache / "state.json").write_text("[]\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "archives"],
            env={**os.environ, "HOME": str(home)},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 1, result)
        self.assertEqual(json.loads(result.stdout)["status"], "error")
        self.assertEqual(result.stderr, "")

    def test_cli_success_and_already_running_are_json(self) -> None:
        home = self.base / "cli-home"
        (home / ".worktrees").mkdir(parents=True)
        environment = {**os.environ, "HOME": str(home)}
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "sweep"],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result)
        self.assertEqual(json.loads(result.stdout)["status"], "ok")
        lock = janitor_module.ProcessLock(home / ".cache" / "worktree-janitor")
        self.assertTrue(lock.acquire())
        try:
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "archives"],
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        finally:
            lock.close()
        self.assertEqual(result.returncode, 0, result)
        self.assertEqual(json.loads(result.stdout)["status"], "already_running")

    def test_applied_cli_archives_removes_and_restores(self) -> None:
        home = self.base / "applied-cli-home"
        root = home / ".worktrees"
        root.mkdir(parents=True)
        worktree = root / "expired"
        run_git("worktree", "add", "-q", "--detach", str(worktree), "HEAD", cwd=self.repo)
        stamp = (self.NOW - dt.timedelta(days=8)).timestamp()
        os.utime(worktree / ".git", (stamp, stamp))
        environment = {**os.environ, "HOME": str(home)}

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "sweep", "--apply"],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["applied"]["clean_worktrees_removed"], 1)
        self.assertFalse(worktree.exists())
        archives = subprocess.run(
            [sys.executable, str(SCRIPT), "archives"],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(archives.returncode, 0, archives)
        archive_id = json.loads(archives.stdout)["archives"][0]["id"]
        restored = root / "restored"
        restore = subprocess.run(
            [sys.executable, str(SCRIPT), "restore", archive_id, "--path", str(restored)],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(restore.returncode, 0, restore)
        self.assertTrue((restored / "tracked.txt").is_file())

    def test_main_preserves_partial_exit_code_and_json(self) -> None:
        stub = mock.Mock()
        stub.cache_dir = self.cache
        stub.root = self.root
        stub.sweep.return_value = (
            {
                "status": "partial",
                "planned": {"clean_worktrees_removed": 1},
                "applied": {"archives_created": 1},
                "failures": [{"operation": "remove_clean_worktree", "error": "injected"}],
            },
            2,
        )
        output = io.StringIO()
        with mock.patch.object(janitor_module, "Janitor", return_value=stub), mock.patch.object(
            janitor_module.sys, "stdout", output
        ):
            code = janitor_module.main(["sweep", "--apply"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "partial")

    def test_cli_rejects_root_override_with_json_error(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.base), "sweep", "--apply"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 1, result)
        self.assertEqual(json.loads(result.stdout)["status"], "error")
        self.assertEqual(result.stderr, "")

    def test_just_below_age_boundaries_is_preserved(self) -> None:
        recent_clean = self.add_worktree("recent-clean", age=dt.timedelta(hours=47, minutes=59))
        dirty_grace = self.add_worktree("dirty-grace", age=dt.timedelta(days=6, hours=23, minutes=59))
        (dirty_grace / "unfinished.txt").write_text("keep", encoding="utf-8")

        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 0, report)
        self.assertTrue(recent_clean.exists())
        self.assertTrue(dirty_grace.exists())
        self.assertEqual(report["skipped"]["recent"], 1)
        self.assertEqual(report["skipped"]["dirty_grace"], 1)

    def test_symlink_root_and_symlinked_target_fail_closed(self) -> None:
        linked_root = self.base / "linked-root"
        linked_root.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(janitor_module.JanitorError):
            janitor_module.Janitor(
                linked_root,
                self.cache,
                janitor_module.Policy(),
                now=self.NOW,
                active_paths=lambda: set(),
            ).scan()

        worktree = self.add_worktree(age=dt.timedelta(days=3))
        (worktree / "dirty").write_text("x", encoding="utf-8")
        outside = self.base / "outside-target"
        outside.mkdir()
        (outside / ".rustc_info.json").write_text("{}", encoding="utf-8")
        (worktree / "target").symlink_to(outside, target_is_directory=True)
        report, code = self.janitor().sweep(True)
        self.assertEqual(code, 0, report)
        self.assertTrue(outside.exists())

    def test_darwin_activity_uses_effective_user(self) -> None:
        active = self.base / "active-cwd"
        active.mkdir()
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=f"p123\nn{active}\n",
            stderr="",
        )
        account = mock.Mock(pw_name="effective-user")
        with (
            mock.patch.object(janitor_module.shutil, "which", return_value="/usr/bin/lsof"),
            mock.patch.object(janitor_module.os, "geteuid", return_value=123),
            mock.patch.object(janitor_module.pwd, "getpwuid", return_value=account) as getpwuid,
            mock.patch.object(janitor_module.subprocess, "run", return_value=completed) as run,
        ):
            paths = janitor_module.darwin_active_paths()
        self.assertEqual(paths, {active.resolve()})
        getpwuid.assert_called_once_with(123)
        self.assertIn("effective-user", run.call_args.args[0])

    def test_activity_audit_failure_preserves_worktree(self) -> None:
        worktree = self.add_worktree("active-audit-failure")
        with (
            mock.patch.object(janitor_module.platform, "system", return_value="Darwin"),
            mock.patch.object(janitor_module.shutil, "which", return_value=None),
            self.assertRaises(janitor_module.JanitorError),
        ):
            self.janitor(active_paths=janitor_module.default_active_paths).sweep(True)
        self.assertTrue(worktree.exists())

    def test_linux_activity_reads_effective_user_processes(self) -> None:
        proc = self.base / "proc"
        own = proc / "101"
        other = proc / "202"
        own.mkdir(parents=True)
        other.mkdir()
        active = self.base / "linux-active"
        active.mkdir()
        (own / "status").write_text("Name:\ttest\nUid:\t7\t42\t42\t42\n", encoding="utf-8")
        (other / "status").write_text("Name:\tother\nUid:\t42\t7\t7\t7\n", encoding="utf-8")
        (own / "cwd").symlink_to(active, target_is_directory=True)
        (other / "cwd").symlink_to(self.base, target_is_directory=True)

        self.assertEqual(janitor_module.linux_active_paths(proc, uid=42), {active.resolve()})

    def test_linux_activity_skips_same_user_process_with_unreadable_cwd(self) -> None:
        proc = self.base / "proc"
        readable = proc / "101"
        blocked = proc / "202"
        readable.mkdir(parents=True)
        blocked.mkdir()
        active = self.base / "linux-active"
        active.mkdir()
        for process in (readable, blocked):
            (process / "status").write_text("Name:\ttest\nUid:\t7\t42\t42\t42\n", encoding="utf-8")
        (readable / "cwd").symlink_to(active, target_is_directory=True)
        (blocked / "cwd").symlink_to(self.base, target_is_directory=True)
        original_resolve = Path.resolve

        def resolve(path, *args, **kwargs):
            if path == blocked / "cwd":
                raise PermissionError("injected procfs restriction")
            return original_resolve(path, *args, **kwargs)

        with mock.patch.object(Path, "resolve", autospec=True, side_effect=resolve):
            paths = janitor_module.linux_active_paths(proc, uid=42)
        self.assertEqual(paths, {active.resolve()})

    def test_linux_activity_failure_preserves_worktree(self) -> None:
        worktree = self.add_worktree("linux-audit-failure")
        missing_proc = self.base / "missing-proc"

        def failed_audit():
            return janitor_module.linux_active_paths(missing_proc, uid=42)

        with self.assertRaises(janitor_module.JanitorError):
            self.janitor(active_paths=failed_audit).sweep(True)
        self.assertTrue(worktree.exists())

    def test_lock_is_kernel_released_after_holder_closes(self) -> None:
        first = janitor_module.ProcessLock(self.cache)
        second = janitor_module.ProcessLock(self.cache)
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        first.close()
        self.assertTrue(second.acquire())
        second.close()


if __name__ == "__main__":
    unittest.main()
