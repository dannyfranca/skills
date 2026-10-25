from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fixtures import finding, run, slice_, write_session

from findings import FindingClass, classify, load_findings, session_findings
from session_store import TargetKind, load_session, load_sessions


class ClassifyTests(unittest.TestCase):
    def test_outcomes_map_to_classes(self) -> None:
        expected = {
            "kept": FindingClass.KEPT,
            "removed": FindingClass.REMOVED,
            "rejected": FindingClass.REJECTED,
            "duplicate": FindingClass.DUPLICATE,
            "open": FindingClass.UNTRIAGED,
        }
        for outcome, finding_class in expected.items():
            with self.subTest(outcome=outcome):
                self.assertEqual(classify(finding("f", outcome=outcome)), finding_class)


class SessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_loads_only_session_directories(self) -> None:
        write_session(self.root, "20261001-0000-aaaaaaaa", variant=None)
        write_session(self.root, "20261001-0100-bbbbbbbb", variant="b", created=60)
        (self.root / "_evals").mkdir()
        (self.root / "owner" / "repo" / "resource-diagnosis").mkdir()
        (self.root / "owner" / "repo" / "20261001-0200-broken").mkdir()
        (self.root / "owner" / "repo" / "20261001-0200-broken" / "_state.json").write_text("{")
        sessions = load_sessions(self.root)
        self.assertEqual([s.id for s in sessions], ["owner/repo/20261001-0000-aaaaaaaa", "owner/repo/20261001-0100-bbbbbbbb"])
        self.assertEqual(sessions[0].variant, "default")
        self.assertEqual(sessions[0].repository, "owner/repo")
        self.assertEqual(sessions[0].target.kind, TargetKind.UNCOMMITTED)
        self.assertIsNone(sessions[0].skill_revision)
        self.assertEqual(load_session(self.root, sessions[1].id).variant, "b")

    def test_reads_new_session_fields(self) -> None:
        write_session(
            self.root,
            "20261001-0000-cccccccc",
            target={"kind": "base", "value": "origin/main"},
            session_extra={
                "skill_revision": "0123456789ab",
                "target_size": {"files": 2, "added": 30, "deleted": 10},
            },
        )
        session = load_sessions(self.root)[0]
        self.assertEqual(session.skill_revision, "0123456789ab")
        self.assertEqual(session.target.value, "origin/main")
        assert session.target_size is not None
        self.assertEqual(session.target_size.changed_lines, 40)


class SessionFindingsTests(unittest.TestCase):
    def test_reads_inline_and_archived_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_session(
                Path(tmp),
                "20261001-0000-dddddddd",
                slices={
                    "core": slice_(
                        run("r1", 1, findings=[finding("f1", "P1"), finding("f2", outcome="rejected")], archived=True),
                        run("r2", 2, findings=[finding("f3", outcome="open")]),
                    )
                },
            )
            session = load_sessions(Path(tmp))[0]
            found = {item.finding_id: item for item in session_findings(session)}
        self.assertEqual(set(found), {"f1", "f2", "f3"})
        self.assertEqual(found["f1"].finding_class, FindingClass.KEPT)
        self.assertEqual(found["f1"].pass_number, 1)
        self.assertEqual(found["f1"].slice_name, "core")
        self.assertEqual(found["f2"].finding_class, FindingClass.REJECTED)
        self.assertEqual(found["f3"].finding_class, FindingClass.UNTRIAGED)
        self.assertEqual(found["f3"].report_path, "/reviews/r2.md")

    def test_unreadable_or_short_archive_marks_run_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = write_session(
                Path(tmp),
                "20261001-0000-eeeeeeee",
                slices={
                    "core": slice_(
                        run("r1", 1, findings=[finding("f1", "P1")], archived=True),
                        run("r2", 2, findings=[finding("f2", "P1"), finding("f3", "P1")], archived=True),
                        run("r3", 3, findings=[finding("f4", "P1")]),
                    )
                },
            )
            state = json.loads((directory / "_state.json").read_text())
            state["slices"]["core"]["runs"][1]["finding_count"] = 3
            (directory / "_state.json").write_text(json.dumps(state))
            (directory / "history" / "r1.json").unlink()
            loaded = load_findings(load_sessions(Path(tmp))[0])
        self.assertEqual(loaded.missing_runs, ("r1", "r2"))
        self.assertEqual([item.finding_id for item in loaded.findings], ["f4"])
        self.assertFalse(loaded.complete)

    def test_superseded_findings_of_removed_slice_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_session(
                Path(tmp),
                "20261001-0000-ffffffff",
                slices={
                    "gone": slice_(
                        run("r1", 1, findings=[finding("f1", "P1"), finding("f2", outcome="rejected")]),
                        removed=True,
                    ),
                    "core": slice_(run("r2", 1, findings=[finding("f3", "P1")])),
                },
            )
            found = {item.finding_id: item.finding_class for item in session_findings(load_sessions(Path(tmp))[0])}
        self.assertEqual(
            found, {"f1": FindingClass.REMOVED, "f2": FindingClass.REJECTED, "f3": FindingClass.KEPT}
        )


if __name__ == "__main__":
    unittest.main()
