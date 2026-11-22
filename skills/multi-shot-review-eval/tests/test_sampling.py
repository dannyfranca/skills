from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from typing import Sequence
from unittest import mock

from fixtures import finding, run, slice_, write_session

from eval_errors import EvalError
import git_objects
from findings import load_findings
from sampling import Judgeability, ReviewedCode, SampleQuota, draw_sample, reviewed_code
from second_round import sample_document
from session_store import load_sessions


def NO_CODE(session, finding) -> ReviewedCode:
    return ReviewedCode(Judgeability.UNJUDGEABLE)


class SamplingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for index, variant in enumerate(("a", "a", "b")):
            kept = [finding(f"k{index}-{n}", severity) for n, severity in enumerate(["P1", "P3"] * 5)]
            rejected = [finding(f"x{index}-{n}", outcome="rejected") for n in range(4)]
            write_session(
                self.root,
                f"20261001-0000-{index:08x}",
                variant=variant,
                slices={"core": slice_(run(f"r{index}", findings=kept + rejected))},
            )
        sessions = load_sessions(self.root)
        self.arms = {"a": [s for s in sessions if s.variant == "a"], "b": [s for s in sessions if s.variant == "b"]}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def draw(self, seed: int = 1, exclude=frozenset(), quota=SampleQuota(kept=4, rejected=2)):
        return draw_sample(self.arms, quota=quota, seed=seed, exclude=exclude, code_of=NO_CODE)

    def test_negative_quota_is_rejected(self) -> None:
        for kept, rejected in ((-1, 0), (0, -1)):
            with self.subTest(kept=kept, rejected=rejected), self.assertRaises(EvalError):
                SampleQuota(kept=kept, rejected=rejected)

    def test_report_path_resolves_inside_the_session_directory(self) -> None:
        for session in self.arms["a"] + self.arms["b"]:
            for report in ("r0.md", "r1.md", "r2.md"):
                (session.directory / report).write_text("report")
        self.assertTrue(all(Path(entry.report_path).parent.name.startswith("2026") for entry in self.draw()))

    def test_quota_per_arm_and_class(self) -> None:
        sample = self.draw()
        counts = Counter((entry.arm, entry.finding_class.value) for entry in sample)
        self.assertEqual(counts, {("a", "kept"): 4, ("a", "rejected"): 2, ("b", "kept"): 4, ("b", "rejected"): 2})
        entry = sample[0]
        self.assertEqual(entry.slice_name, "core")
        self.assertIsNone(entry.report_path)
        self.assertEqual(entry.location, "src/lib.rs:1-2")
        self.assertTrue(entry.content)
        self.assertEqual(entry.judgeability, Judgeability.UNJUDGEABLE)

    def test_seeded_and_excludes_prior_entries(self) -> None:
        first = self.draw(seed=5)
        self.assertEqual(first, self.draw(seed=5))
        excluded = frozenset((entry.session_id, entry.finding_id) for entry in first)
        second = self.draw(seed=5, exclude=excluded)
        self.assertFalse(excluded & {(entry.session_id, entry.finding_id) for entry in second})

    def test_small_pools_return_what_exists(self) -> None:
        sample = self.draw(quota=SampleQuota(kept=50, rejected=50))
        counts = Counter((entry.arm, entry.finding_class.value) for entry in sample)
        self.assertEqual(counts[("b", "kept")], 10)
        self.assertEqual(counts[("b", "rejected")], 4)

    def test_severity_bias_prefers_p1(self) -> None:
        severities: Counter[str] = Counter()
        for seed in range(200):
            for entry in self.draw(seed=seed, quota=SampleQuota(kept=2, rejected=0)):
                severities[entry.severity] += 1
        self.assertGreater(severities["P1"], severities["P3"] * 2)


class IncompleteFindingsTests(unittest.TestCase):
    def test_sessions_with_missing_archives_are_not_sampled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            broken = write_session(
                root,
                "20261001-0000-00000001",
                slices={
                    "core": slice_(
                        run("r1", 1, findings=[finding("lost", "P1")], archived=True),
                        run("r2", 2, findings=[finding("readable", "P1"), finding("gone", outcome="rejected")]),
                    )
                },
            )
            (broken / "history" / "r1.json").unlink()
            write_session(
                root, "20261001-0000-00000002", slices={"core": slice_(run("r3", findings=[finding("ok", "P1")]))}
            )
            sample = draw_sample(
                {"a": load_sessions(root)},
                quota=SampleQuota(kept=10, rejected=10),
                seed=1,
                exclude=frozenset(),
                code_of=NO_CODE,
            )
        self.assertEqual([entry.finding_id for entry in sample], ["ok"])


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgSign=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class ReviewedCodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.reviews = base / "reviews"
        self.repo = base / "repo"
        self.git_dir = self.repo / ".git"
        (self.repo / "pkg").mkdir(parents=True)
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "a.txt").write_text("reviewed\n")
        (self.repo / "pkg" / "mod.py").write_text("module\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "reviewed")
        self.reviewed = git(self.repo, "rev-parse", "HEAD")
        self.worktree = base / "worktree"
        git(self.repo, "worktree", "add", "-q", "--detach", str(self.worktree))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def record(self, commit: str, *, staged: str | None = None) -> dict[str, str]:
        record = {"commit": commit, "created_at": "2026-10-01T00:00:00Z", "git_dir": str(self.git_dir), "ref": "refs/x"}
        return {**record, **({"staged": staged} if staged else {})}

    def session(
        self,
        name: str,
        *,
        snapshot: str | None,
        base: str | None = None,
        path: str | None = "a.txt",
        omitted: Sequence[str] | None = (),
        **kwargs,
    ):
        """A session with one finding.

        `base` is the commit that the reviewed change starts from. `path` is the file from the
        session root, as the review recorded it. `omitted` are the files that the snapshot left out.
        """

        reported = finding("k1", "P1", path=str(self.worktree / (path or "../outside.py")), root_path=path)
        wave = run("r1", findings=[reported], snapshot=snapshot, snapshot_base=base, snapshot_omitted=omitted)
        write_session(self.reviews, name, slices={"core": slice_(wave)}, **kwargs)
        return next(s for s in load_sessions(self.reviews) if s.id.endswith(name))

    def code(self, session) -> ReviewedCode:
        return reviewed_code(session, load_findings(session).findings[0])

    def sampled(self, session, kept: int = 1):
        return draw_sample({"a": [session]}, quota=SampleQuota(kept=kept, rejected=0), seed=1, exclude=frozenset())

    def commit(self, message: str) -> str:
        git(self.repo, "add", "--all")
        git(self.repo, "commit", "-qm", message)
        return git(self.repo, "rev-parse", "HEAD")

    def version(self, message: str, changes: dict[str, str | None]) -> str:
        """A commit on top of the reviewed commit, as the snapshot of a working copy or of a staging area."""

        git(self.repo, "reset", "-q", "--hard", self.reviewed)
        for path, text in changes.items():
            if text is None:
                (self.repo / path).unlink()
            else:
                (self.repo / path).write_text(text)
        commit = self.commit(message)
        git(self.repo, "reset", "-q", "--hard", self.reviewed)
        return commit

    def show(self, commit: str, path: str) -> str | None:
        shown = subprocess.run(
            ["git", "show", f"{commit}:{path}"], cwd=self.git_dir, capture_output=True, text=True, check=False
        )
        return shown.stdout.strip() if shown.returncode == 0 else None

    def test_snapshot_of_a_removed_worktree_is_exact_in_the_shared_repository(self) -> None:
        session = self.session(
            "20261001-0000-aaaaaaaa",
            snapshot=self.reviewed,
            base=self.reviewed,
            root=self.worktree,
            snapshots=[self.record(self.reviewed)],
        )
        git(self.repo, "worktree", "remove", "--force", str(self.worktree))

        # Analysts get the sample as a file, so the file must carry what `git show` needs.
        (entry,) = json.loads(json.dumps(sample_document(self.sampled(session))))["entries"]

        self.assertFalse(self.worktree.exists())
        self.assertEqual(
            {key: entry[key] for key in ("judgeability", "repository", "commit", "path", "staged_commit", "base_commit")},
            {
                "judgeability": "exact",
                "repository": str(self.git_dir),
                "commit": self.reviewed,
                "path": "a.txt",
                "staged_commit": None,
                "base_commit": None,
            },
        )
        self.assertEqual(git(Path(entry["repository"]), "show", f"{entry['commit']}:{entry['path']}"), "reviewed")

    def test_each_wave_of_a_session_is_read_from_its_own_snapshot(self) -> None:
        (self.repo / "a.txt").write_text("after the fixes\n")
        second = self.commit("fixes")
        waves = [
            run("r1", 1, findings=[finding("first", "P1", root_path="a.txt")], snapshot=self.reviewed),
            run("r2", 2, findings=[finding("second", "P1", root_path="a.txt")], snapshot=second),
        ]
        write_session(
            self.reviews,
            "20261001-0000-abababab",
            root=self.repo,
            slices={"core": slice_(*waves)},
            snapshots=[self.record(self.reviewed), self.record(second)],
        )
        (session,) = load_sessions(self.reviews)

        read = {entry.finding_id: self.show(entry.commit, entry.path) for entry in self.sampled(session, kept=2)}

        self.assertEqual(read, {"first": "reviewed", "second": "after the fixes"})

    def test_file_that_no_reviewed_version_holds_is_not_exact(self) -> None:
        git(self.repo, "update-index", "--add", "--cacheinfo", f"160000,{self.reviewed},sub")
        git(self.repo, "commit", "-qm", "a submodule")
        snapshot = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "left-out-of-the-snapshot.bin").write_text("too large for a snapshot\n")
        (self.repo / "sub").mkdir()
        (self.repo / "sub" / "a.txt").write_text("content of the submodule\n")
        (self.repo / "dangling-link").symlink_to("no-such-file")
        on_the_disk = {
            "20261001-0000-60000000": "left-out-of-the-snapshot.bin",
            "20261001-0000-63000000": "sub/a.txt",
            "20261001-0000-68000000": "./a.txt",
            # The review recorded no path: the file is outside the session root.
            "20261001-0000-65000000": None,
        }
        no_file_on_the_disk = {
            "20261001-0000-61000000": "pkg",
            "20261001-0000-62000000": "sub",
            "20261001-0000-69000000": "pkg/",
            "20261001-0000-6b000000": "dangling-link",
            "20261001-0000-64000000": "a.txt/not-a-directory",
            # Git reads these as patterns for `a.txt`, and `git show` reads a file name.
            "20261001-0000-66000000": ":a.txt",
            "20261001-0000-67000000": "a*.txt",
        }
        for name, path in no_file_on_the_disk.items():
            with self.subTest(path=path):
                session = self.session(
                    name,
                    snapshot=snapshot,
                    base=self.reviewed,
                    root=self.repo,
                    path=path,
                    snapshots=[self.record(snapshot, staged=self.reviewed)],
                )

                self.assertEqual(self.code(session), ReviewedCode(Judgeability.UNJUDGEABLE))
        for name, path in on_the_disk.items():
            with self.subTest(path=path):
                session = self.session(
                    name,
                    snapshot=snapshot,
                    base=self.reviewed,
                    root=self.repo,
                    path=path,
                    snapshots=[self.record(snapshot, staged=self.reviewed)],
                )

                self.assertEqual(self.code(session), ReviewedCode(Judgeability.APPROXIMATE, self.repo, None, path))

    def test_file_name_with_pattern_characters_is_read_as_a_name(self) -> None:
        name = ":odd*[name]?.py"
        (self.repo / name).write_text("odd\n")
        snapshot = self.commit("a file name that Git can read as a pattern")
        session = self.session(
            "20261001-0000-6a000000", snapshot=snapshot, root=self.repo, path=name, snapshots=[self.record(snapshot)]
        )

        code = self.code(session)

        self.assertEqual(code, ReviewedCode(Judgeability.EXACT, self.git_dir, snapshot, name))
        self.assertEqual(self.show(code.commit, code.path), "odd")

    def test_snapshot_without_a_record_is_read_from_the_session_root(self) -> None:
        session = self.session("20261001-0000-bbbbbbbb", snapshot=self.reviewed, root=self.repo)

        self.assertEqual(self.code(session), ReviewedCode(Judgeability.EXACT, self.repo, self.reviewed, "a.txt"))

    def test_snapshot_that_git_collected_falls_back_to_the_current_files(self) -> None:
        gone = "0" * 39 + "1"
        session = self.session("20261001-0000-cccccccc", snapshot=gone, root=self.repo, snapshots=[self.record(gone)])

        (entry,) = self.sampled(session)

        self.assertEqual(
            (entry.judgeability, entry.repository, entry.commit, entry.path),
            (Judgeability.APPROXIMATE, str(self.repo), None, "a.txt"),
        )

    def test_collected_snapshot_of_a_file_that_a_later_change_deleted_is_unjudgeable(self) -> None:
        gone = "0" * 39 + "1"
        session = self.session("20261001-0000-cdcdcdcd", snapshot=gone, root=self.repo, snapshots=[self.record(gone)])
        (self.repo / "a.txt").unlink()

        self.assertEqual(self.code(session), ReviewedCode(Judgeability.UNJUDGEABLE))

    def test_commit_target_that_moved_does_not_replace_a_collected_snapshot(self) -> None:
        gone = "0" * 39 + "1"
        session = self.session(
            "20261001-0000-c0c0c0c0",
            snapshot=gone,
            root=self.repo,
            target={"kind": "commit", "value": "HEAD"},
            snapshots=[self.record(gone)],
        )

        self.assertEqual(self.code(session), ReviewedCode(Judgeability.APPROXIMATE, self.repo, None, "a.txt"))

    def test_collected_snapshot_in_a_bare_repository_is_unjudgeable(self) -> None:
        bare = self.repo.parent / "bare.git"
        git(self.repo, "clone", "-q", "--bare", str(self.repo), str(bare))
        gone = "0" * 39 + "1"
        record = {**self.record(gone), "git_dir": str(bare)}
        for index, target in enumerate(({"kind": "commit", "value": "HEAD"}, {"kind": "base", "value": "main"})):
            with self.subTest(target=target):
                session = self.session(
                    f"20261001-0000-c{index}b0b0b0", snapshot=gone, root=bare, target=target, snapshots=[record]
                )

                self.assertEqual(self.code(session), ReviewedCode(Judgeability.UNJUDGEABLE))

    def test_snapshot_in_a_deleted_repository_is_unjudgeable(self) -> None:
        session = self.session(
            "20261001-0000-dddddddd", snapshot=self.reviewed, root=self.worktree, snapshots=[self.record(self.reviewed)]
        )
        shutil.rmtree(self.worktree)
        shutil.rmtree(self.repo)

        (entry,) = self.sampled(session)

        self.assertEqual(
            (entry.judgeability, entry.repository, entry.commit, entry.path),
            (Judgeability.UNJUDGEABLE, None, None, None),
        )

    def test_run_without_a_snapshot_is_judged_from_the_session_target(self) -> None:
        cases = {
            "20261001-0000-e0000000": (
                {"root": self.repo, "target": {"kind": "commit", "value": self.reviewed}},
                ReviewedCode(Judgeability.EXACT, self.repo, self.reviewed, "a.txt"),
            ),
            # Sessions from before the snapshots recorded no path from the root.
            "20261001-0000-e0100000": (
                {"root": self.repo, "target": {"kind": "commit", "value": self.reviewed}, "path": None},
                ReviewedCode(Judgeability.EXACT, self.repo, self.reviewed, None),
            ),
            "20261001-0000-e0200000": (
                {"root": self.repo, "target": {"kind": "commit", "value": self.reviewed}, "path": "never-in-git.py"},
                ReviewedCode(Judgeability.EXACT, self.repo, self.reviewed, "never-in-git.py"),
            ),
            "20261001-0000-e1000000": (
                {"root": self.repo, "target": {"kind": "commit", "value": "0" * 40}},
                ReviewedCode(Judgeability.UNJUDGEABLE),
            ),
            "20261001-0000-e2000000": (
                {"root": self.repo, "target": {"kind": "base", "value": "main"}},
                ReviewedCode(Judgeability.APPROXIMATE, self.repo, None, "a.txt"),
            ),
            "20261001-0000-e3000000": (
                {"root": self.repo},
                ReviewedCode(Judgeability.APPROXIMATE, self.repo, None, "a.txt"),
            ),
            # A review outside Git.
            "20261001-0000-e4000000": (
                {"root": self.reviews},
                ReviewedCode(Judgeability.APPROXIMATE, self.reviews, None, "a.txt"),
            ),
            "20261001-0000-e5000000": ({}, ReviewedCode(Judgeability.UNJUDGEABLE)),
        }
        for name, (kwargs, expected) in cases.items():
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.code(self.session(name, snapshot=None, **kwargs)), expected)

    def test_commit_target_by_name_is_stored_as_the_commit_id(self) -> None:
        session = self.session(
            "20261001-0000-d3000000", snapshot=None, root=self.repo, target={"kind": "commit", "value": "HEAD"}
        )

        code = self.code(session)
        (self.repo / "a.txt").write_text("a later commit\n")
        self.commit("later")

        self.assertEqual(code, ReviewedCode(Judgeability.EXACT, self.repo, self.reviewed, "a.txt"))
        self.assertEqual(self.show(code.commit, code.path), "reviewed")

    def test_entry_names_the_staging_area_commit_only_when_it_has_another_version_of_the_file(self) -> None:
        working_copy = self.version("working copy", {"a.txt": "working copy\n", "new.txt": "not staged\n"})
        staged = self.version("staging area", {"a.txt": "staged\n", "gone.txt": "staged, then deleted\n"})
        unstaged = self.version("staging area without the file", {"a.txt": None})
        other_file = self.version("staging area with a change of another file", {"pkg/mod.py": "staged\n"})
        no_commit_yet = git(self.repo, "commit-tree", "-m", "staging area of a new repository", f"{staged}^{{tree}}")
        cases = {
            "another version": ("a.txt", staged, staged, None),
            "same version": ("pkg/mod.py", staged, None, None),
            # The staging-area commit has the `HEAD` version, which no reviewer read: the working
            # copy changed the file without a staged change, or the file has a merge conflict.
            "no staged change of a file that the working copy changed": ("a.txt", other_file, None, None),
            "repository without a commit, where each staged file is a staged change": (
                "a.txt",
                no_commit_yet,
                no_commit_yet,
                None,
            ),
            "new file that the author did not stage": ("new.txt", staged, None, None),
            "staged, then deleted from the working copy": ("gone.txt", staged, staged, None),
            # The analyst must read what the staged change deleted.
            "deleted from the staging area, and still on the disk": ("a.txt", unstaged, unstaged, self.reviewed),
        }
        for index, (name, (path, staging_area, staged_commit, base_commit)) in enumerate(cases.items()):
            with self.subTest(name):
                session = self.session(
                    f"20261001-0000-c{index}000000",
                    snapshot=working_copy,
                    base=self.reviewed,
                    root=self.repo,
                    path=path,
                    snapshots=[self.record(working_copy, staged=staging_area)],
                )

                (entry,) = self.sampled(session)

                self.assertEqual(
                    (entry.judgeability, entry.repository, entry.commit, entry.path, entry.staged_commit, entry.base_commit),
                    (Judgeability.EXACT, str(self.git_dir), working_copy, path, staged_commit, base_commit),
                )

    def test_staging_area_with_another_file_mode_is_a_second_version(self) -> None:
        git(self.repo, "update-index", "--chmod=+x", "a.txt")
        git(self.repo, "commit", "-qm", "staged executable bit")
        staged = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "reset", "-q", "--hard", self.reviewed)
        session = self.session(
            "20261001-0000-c6000000",
            snapshot=self.reviewed,
            base=self.reviewed,
            root=self.repo,
            snapshots=[self.record(self.reviewed, staged=staged)],
        )

        (entry,) = self.sampled(session)

        self.assertEqual(self.show(entry.staged_commit, entry.path), self.show(entry.commit, entry.path))
        self.assertEqual((entry.judgeability, entry.staged_commit, entry.base_commit), (Judgeability.EXACT, staged, None))

    def test_file_that_was_staged_and_then_deleted_is_read_from_the_staging_area_commit(self) -> None:
        staged = self.version("staging area", {"gone.txt": "staged, then deleted\n"})
        session = self.session(
            "20261001-0000-c7000000",
            snapshot=self.reviewed,
            base=self.reviewed,
            root=self.repo,
            path="gone.txt",
            snapshots=[self.record(self.reviewed, staged=staged)],
        )

        (entry,) = self.sampled(session)

        self.assertIsNone(self.show(entry.commit, entry.path))
        self.assertEqual(self.show(entry.staged_commit, entry.path), "staged, then deleted")

    def test_file_that_the_reviewed_change_deleted_is_read_from_the_commit_that_the_change_starts_from(self) -> None:
        (self.repo / "a.txt").unlink()
        deletion = self.commit("delete the file")
        (self.repo / "pkg" / "mod.py").write_text("a later commit of the branch\n")
        later = self.commit("later commit of the branch")
        working_copy = self.version("working copy", {"a.txt": None})
        cases = {
            "uncommitted deletion": ({}, working_copy),
            "deletion in an earlier commit of a base range": ({"target": {"kind": "base", "value": "main"}}, later),
            "deletion in the reviewed commit": ({"target": {"kind": "commit", "value": deletion}}, deletion),
        }
        for index, (name, (kwargs, snapshot)) in enumerate(cases.items()):
            with self.subTest(name):
                session = self.session(
                    f"20261001-0000-d{index}000000",
                    snapshot=snapshot,
                    base=self.reviewed,
                    root=self.repo,
                    snapshots=[self.record(snapshot)],
                    **kwargs,
                )

                (entry,) = json.loads(json.dumps(sample_document(self.sampled(session))))["entries"]

                self.assertEqual(
                    (entry["judgeability"], entry["commit"], entry["path"], entry["staged_commit"], entry["base_commit"]),
                    ("exact", snapshot, "a.txt", None, self.reviewed),
                )
                self.assertIsNone(self.show(entry["commit"], entry["path"]))
                self.assertEqual(self.show(entry["base_commit"], entry["path"]), "reviewed")

    def test_deleted_file_has_both_commits_when_the_staging_area_had_another_version(self) -> None:
        staged = self.version("staging area", {"a.txt": "staged\n"})
        working_copy = self.version("working copy", {"a.txt": None})
        session = self.session(
            "20261001-0000-d7000000",
            snapshot=working_copy,
            base=self.reviewed,
            root=self.repo,
            snapshots=[self.record(working_copy, staged=staged)],
        )

        self.assertEqual(
            self.code(session),
            ReviewedCode(Judgeability.EXACT, self.git_dir, working_copy, "a.txt", staged, self.reviewed),
        )

    def test_waves_that_pin_the_same_commit_keep_their_own_start_of_the_change(self) -> None:
        (self.repo / "a.txt").unlink()
        deletion = self.commit("lower commit of the branch: delete the file")
        (self.repo / "pkg" / "mod.py").write_text("upper commit of the branch\n")
        head = self.commit("upper commit of the branch")
        # The base branch took the lower commit between the waves, so the second wave did not review the deletion.
        waves = [
            run("r1", 1, findings=[finding("first", "P1", root_path="a.txt")], snapshot=head, snapshot_base=self.reviewed),
            run("r2", 2, findings=[finding("second", "P1", root_path="a.txt")], snapshot=head, snapshot_base=deletion),
        ]
        write_session(
            self.reviews,
            "20261001-0000-d9000000",
            root=self.repo,
            target={"kind": "base", "value": "main"},
            slices={"core": slice_(*waves)},
            snapshots=[self.record(head)],
        )
        (session,) = load_sessions(self.reviews)

        read = {entry.finding_id: (entry.judgeability, entry.base_commit) for entry in self.sampled(session, kept=2)}

        self.assertEqual(
            read,
            # No reviewed version of the second wave has the file, and the disk does not have it.
            {"first": (Judgeability.EXACT, self.reviewed), "second": (Judgeability.UNJUDGEABLE, None)},
        )

    def test_reviewed_commit_that_deleted_the_file_is_exact_without_a_snapshot(self) -> None:
        (self.repo / "a.txt").unlink()
        deletion = self.commit("delete the file")
        session = self.session(
            "20261001-0000-d8000000", snapshot=None, root=self.repo, target={"kind": "commit", "value": deletion}
        )

        self.assertEqual(self.code(session), ReviewedCode(Judgeability.EXACT, self.repo, deletion, "a.txt"))

    def test_malformed_snapshot_records_are_ignored(self) -> None:
        session = self.session(
            "20261001-0000-88888888",
            snapshot=self.reviewed,
            root=self.repo,
            snapshots=[
                "text",
                {"commit": self.reviewed},
                {"commit": self.reviewed, "git_dir": 7},
                {"commit": self.reviewed, "git_dir": str(self.git_dir), "staged": 7},
            ],
        )

        self.assertEqual(self.code(session), ReviewedCode(Judgeability.EXACT, self.git_dir, self.reviewed, "a.txt"))

    def test_failed_lookup_in_the_snapshot_is_not_a_deleted_file(self) -> None:
        working_copy = self.version("working copy", {"a.txt": "working copy\n"})
        session = self.session(
            "20261001-0000-fa11fa11",
            snapshot=working_copy,
            base=self.reviewed,
            root=self.repo,
            snapshots=[self.record(working_copy)],
        )
        run_git = git_objects.git

        def git_that_cannot_list_the_snapshot(repository: Path, *args: str) -> str | None:
            return None if "ls-tree" in args and working_copy in args else run_git(repository, *args)

        with mock.patch.object(git_objects, "git", git_that_cannot_list_the_snapshot):
            code = self.code(session)

        self.assertEqual(code, ReviewedCode(Judgeability.APPROXIMATE, self.repo, None, "a.txt"))

    def test_file_that_the_snapshot_left_out_is_not_a_deleted_file(self) -> None:
        snapshot = self.version("snapshot without the file", {"pkg/mod.py": None})
        cases = {"the run names the file": ["pkg/mod.py"], "the run has too many files to name": None}
        for index, (name, omitted) in enumerate(cases.items()):
            with self.subTest(name):
                session = self.session(
                    f"20261001-0000-0b17{index}000",
                    snapshot=snapshot,
                    base=self.reviewed,
                    path="pkg/mod.py",
                    omitted=omitted,
                    root=self.repo,
                    snapshots=[self.record(snapshot)],
                )

                self.assertEqual(
                    self.code(session), ReviewedCode(Judgeability.APPROXIMATE, self.repo, None, "pkg/mod.py")
                )

    def test_staged_version_of_a_file_that_the_snapshot_left_out_is_exact(self) -> None:
        snapshot = self.version("snapshot without the file", {"a.txt": None})
        staged = self.version("staging area", {"a.txt": "staged\n"})
        session = self.session(
            "20261001-0000-0b175000",
            snapshot=snapshot,
            base=self.reviewed,
            omitted=["a.txt"],
            root=self.repo,
            snapshots=[self.record(snapshot, staged=staged)],
        )

        self.assertEqual(
            self.code(session),
            ReviewedCode(
                Judgeability.EXACT, self.git_dir, snapshot, "a.txt", staged_commit=staged, working_copy_left_out=True
            ),
        )
        self.assertTrue(self.sampled(session)[0].working_copy_left_out)

    def test_staged_deletion_of_a_file_that_the_snapshot_left_out_is_not_a_deleted_file(self) -> None:
        snapshot = self.version("snapshot without the file", {"a.txt": None})
        staged = self.version("staging area without the file", {"a.txt": None})
        session = self.session(
            "20261001-0000-0b175d00",
            snapshot=snapshot,
            base=self.reviewed,
            omitted=["a.txt"],
            root=self.repo,
            snapshots=[self.record(snapshot, staged=staged)],
        )

        self.assertEqual(self.code(session), ReviewedCode(Judgeability.APPROXIMATE, self.repo, None, "a.txt"))

    def test_staging_area_at_a_shallow_boundary_compares_with_its_recorded_parent(self) -> None:
        working_copy = self.version("working copy", {"a.txt": "fixed\n"})
        staged = self.version("staging area without a change to the file", {"pkg/mod.py": "staged\n"})
        (self.git_dir / "shallow").write_text(f"{staged}\n")
        session = self.session(
            "20261001-0000-5a110000",
            snapshot=working_copy,
            base=self.reviewed,
            root=self.repo,
            snapshots=[self.record(working_copy, staged=staged)],
        )

        self.assertEqual(self.code(session), ReviewedCode(Judgeability.EXACT, self.git_dir, working_copy, "a.txt"))

    def test_staging_area_without_its_recorded_parent_gives_no_staged_version(self) -> None:
        working_copy = self.version("working copy", {"a.txt": "fixed\n"})
        tree = git(self.repo, "rev-parse", f"{self.reviewed}^{{tree}}")
        orphan = subprocess.run(
            ["git", "hash-object", "-t", "commit", "-w", "--literally", "--stdin"],
            cwd=self.repo,
            input=f"tree {tree}\nparent {'1' * 40}\nauthor t <t@t> 0 +0000\ncommitter t <t@t> 0 +0000\n\nx\n",
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        session = self.session(
            "20261001-0000-5a11d000",
            snapshot=working_copy,
            base=self.reviewed,
            root=self.repo,
            snapshots=[self.record(working_copy, staged=orphan)],
        )

        self.assertEqual(self.code(session), ReviewedCode(Judgeability.EXACT, self.git_dir, working_copy, "a.txt"))

    def test_other_file_that_the_snapshot_left_out_keeps_the_deleted_file_exact(self) -> None:
        snapshot = self.version("snapshot without the files", {"a.txt": None, "pkg/mod.py": None})
        session = self.session(
            "20261001-0000-0b170ff0",
            snapshot=snapshot,
            base=self.reviewed,
            omitted=["pkg/mod.py"],
            root=self.repo,
            snapshots=[self.record(snapshot)],
        )

        self.assertEqual(
            self.code(session),
            ReviewedCode(Judgeability.EXACT, self.git_dir, snapshot, "a.txt", base_commit=self.reviewed),
        )

    def test_failed_lookup_at_the_start_of_the_change_keeps_the_version_of_the_snapshot(self) -> None:
        working_copy = self.version("working copy", {"a.txt": "working copy\n"})
        deletion = self.version("deletion", {"a.txt": None})
        run_git = git_objects.git

        def git_that_cannot_list_the_start(repository: Path, *args: str) -> str | None:
            return None if "ls-tree" in args and self.reviewed in args else run_git(repository, *args)

        cases = {
            "file in the snapshot": (working_copy, ReviewedCode(Judgeability.EXACT, self.git_dir, working_copy, "a.txt")),
            "file not in the snapshot": (deletion, ReviewedCode(Judgeability.APPROXIMATE, self.repo, None, "a.txt")),
        }
        for index, (name, (snapshot, expected)) in enumerate(cases.items()):
            with self.subTest(name):
                session = self.session(
                    f"20261001-0000-ba5e{index}000",
                    snapshot=snapshot,
                    base=self.reviewed,
                    root=self.repo,
                    snapshots=[self.record(snapshot)],
                )

                with mock.patch.object(git_objects, "git", git_that_cannot_list_the_start):
                    code = self.code(session)

                self.assertEqual(code, expected)

    def test_file_lookup_in_a_commit_that_the_repository_does_not_have_is_a_failure(self) -> None:
        with self.assertRaises(git_objects.GitGaveNoAnswer):
            git_objects.file_version(self.repo, "0" * 39 + "1", "a.txt")

    def test_malformed_start_of_the_change_is_ignored(self) -> None:
        for index, base in enumerate((7, "", "not-a-commit")):
            with self.subTest(base=base):
                session = self.session(
                    f"20261001-0000-8{index}888888",
                    snapshot=self.reviewed,
                    base=base,
                    root=self.repo,
                    snapshots=[self.record(self.reviewed)],
                )

                self.assertEqual(
                    self.code(session), ReviewedCode(Judgeability.EXACT, self.git_dir, self.reviewed, "a.txt")
                )


if __name__ == "__main__":
    unittest.main()
