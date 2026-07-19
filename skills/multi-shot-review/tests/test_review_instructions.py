from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from review_instructions import (  # noqa: E402
    _ScopedGuidance,
    _collect_changed_files,
    _discover_review_instructions,
    _read_limited_utf8,
    _render_review_instructions,
    load_classifier_guidance,
)
from review_state import ReviewStateError  # noqa: E402


class ReviewInstructionDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "repo"
        self.global_dir = base / ".agents"
        self.root.mkdir()
        self.global_dir.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_accumulates_global_and_each_changed_path_chain(self) -> None:
        (self.global_dir / "REVIEW.md").write_text("global", encoding="utf-8")
        (self.global_dir / "REVIEW.override.md").write_text(
            "global override",
            encoding="utf-8",
        )
        (self.root / "REVIEW.md").write_text("root", encoding="utf-8")
        services = self.root / "services"
        payments = services / "payments"
        web = self.root / "web"
        payments.mkdir(parents=True)
        web.mkdir()
        (services / "REVIEW.md").write_text("services", encoding="utf-8")
        (payments / "REVIEW.md").write_text("payments base", encoding="utf-8")
        (payments / "REVIEW.override.md").write_text("payments override", encoding="utf-8")
        (web / "REVIEW.md").write_text("web", encoding="utf-8")

        instructions = _discover_review_instructions(
            self.root,
            ["web/app.ts", "services/payments/api.py"],
            global_agents_dir=self.global_dir,
        )

        self.assertEqual(
            [item.content for item in instructions],
            [
                "global override",
                "root",
                "services",
                "payments override",
                "web",
            ],
        )
        self.assertEqual(
            [item.scope for item in instructions],
            ["*", ".", "services", "services/payments", "web"],
        )
        self.assertNotIn("payments base", [item.content for item in instructions])

    def test_global_empty_override_falls_through_but_project_empty_override_masks(self) -> None:
        (self.global_dir / "REVIEW.override.md").write_text(" \n", encoding="utf-8")
        (self.global_dir / "REVIEW.md").write_text("global base", encoding="utf-8")
        (self.root / "REVIEW.override.md").write_text("", encoding="utf-8")
        (self.root / "REVIEW.md").write_text("project base", encoding="utf-8")

        instructions = _discover_review_instructions(
            self.root,
            ["src/app.py"],
            global_agents_dir=self.global_dir,
        )

        self.assertEqual(len(instructions), 1)
        self.assertEqual(instructions[0].content, "global base")

    def test_truncated_whitespace_global_override_still_masks_base(self) -> None:
        (self.global_dir / "REVIEW.override.md").write_text(
            " " * 10 + "override",
            encoding="utf-8",
        )
        (self.global_dir / "REVIEW.md").write_text("global base", encoding="utf-8")

        instructions = _discover_review_instructions(
            self.root,
            ["app.py"],
            global_agents_dir=self.global_dir,
            max_bytes=5,
        )

        self.assertEqual(instructions, ())

    def test_shared_sources_load_once_in_deterministic_chain_order(self) -> None:
        package = self.root / "package"
        first = package / "a"
        second = package / "b"
        first.mkdir(parents=True)
        second.mkdir()
        (self.root / "REVIEW.md").write_text("root", encoding="utf-8")
        (package / "REVIEW.md").write_text("package", encoding="utf-8")

        instructions = _discover_review_instructions(
            self.root,
            ["package/b/two.py", "package/a/one.py", "package/a/other.py"],
            global_agents_dir=self.global_dir,
        )

        self.assertEqual(
            [item.content for item in instructions],
            ["root", "package"],
        )

    def test_project_content_uses_combined_byte_limit(self) -> None:
        nested = self.root / "nested"
        nested.mkdir()
        (self.root / "REVIEW.md").write_text("1234", encoding="utf-8")
        (nested / "REVIEW.md").write_text("5678", encoding="utf-8")

        instructions = _discover_review_instructions(
            self.root,
            ["nested/app.py"],
            global_agents_dir=self.global_dir,
            max_bytes=6,
        )

        self.assertEqual([item.content for item in instructions], ["1234", "56"])
        self.assertFalse(instructions[0].truncated)
        self.assertTrue(instructions[1].truncated)

    def test_empty_project_guidance_still_consumes_the_combined_byte_limit(self) -> None:
        nested = self.root / "nested"
        nested.mkdir()
        (self.root / "REVIEW.override.md").write_text("    ", encoding="utf-8")
        (nested / "REVIEW.md").write_text("nested rule", encoding="utf-8")

        instructions = _discover_review_instructions(
            self.root,
            ["nested/app.py"],
            global_agents_dir=self.global_dir,
            max_bytes=4,
        )

        self.assertEqual(instructions, ())

    def test_render_labels_sources_scopes_and_truncation(self) -> None:
        rendered = _render_review_instructions(
            [
                _ScopedGuidance(
                    scope="*",
                    content="global rule",
                ),
                _ScopedGuidance(
                    scope="api",
                    content="api rule",
                    truncated=True,
                ),
            ]
        )

        self.assertIn("Guidance for all changed files", rendered)
        self.assertIn("Guidance for changed descendants of api", rendered)
        self.assertIn("(truncated at byte limit)", rendered)
        self.assertIn("global rule", rendered)
        self.assertIn("api rule", rendered)
        self.assertNotIn(str(self.global_dir), rendered)
        self.assertNotIn("REVIEW", rendered)

    def test_invalid_utf8_fails_with_source_path(self) -> None:
        source = self.root / "REVIEW.md"
        source.write_bytes(b"\xff")

        with self.assertRaisesRegex(ReviewStateError, str(source)):
            _discover_review_instructions(
                self.root,
                ["app.py"],
                global_agents_dir=self.global_dir,
            )

    def test_limited_read_does_not_decode_content_beyond_the_budget(self) -> None:
        source = self.root / "REVIEW.md"
        source.write_bytes(b"valid" + b"\xff" * 1024)

        content, consumed, truncated = _read_limited_utf8(source, 5)

        self.assertEqual(content, "valid")
        self.assertEqual(consumed, 5)
        self.assertTrue(truncated)

    def test_limited_read_drops_only_an_incomplete_utf8_boundary(self) -> None:
        source = self.root / "REVIEW.md"
        source.write_bytes("abc€".encode())

        content, consumed, truncated = _read_limited_utf8(source, 4)

        self.assertEqual(content, "abc")
        self.assertEqual(consumed, 4)
        self.assertTrue(truncated)

    def test_custom_review_file_stem_controls_base_and_override_names(self) -> None:
        (self.global_dir / "SECURITY.md").write_text("global security", encoding="utf-8")
        (self.root / "SECURITY.md").write_text("root base", encoding="utf-8")
        (self.root / "SECURITY.override.md").write_text(
            "root override",
            encoding="utf-8",
        )
        (self.root / "REVIEW.md").write_text("ignored default", encoding="utf-8")

        instructions = _discover_review_instructions(
            self.root,
            ["app.py"],
            review_file="SECURITY",
            global_agents_dir=self.global_dir,
        )

        self.assertEqual(
            [item.content for item in instructions],
            ["global security", "root override"],
        )


class ChangedFileCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        self._git("init", "-b", "main")
        self._git("config", "user.email", "tests@example.com")
        self._git("config", "user.name", "Tests")
        (self.root / "existing.py").write_text("before\n", encoding="utf-8")
        self._git("add", "existing.py")
        self._git("commit", "-m", "baseline")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_collects_staged_unstaged_and_untracked_files(self) -> None:
        (self.root / "existing.py").write_text("after\n", encoding="utf-8")
        (self.root / "staged.py").write_text("staged\n", encoding="utf-8")
        (self.root / "untracked.py").write_text("untracked\n", encoding="utf-8")
        (self.root / ".review").mkdir()
        (self.root / ".review" / "state.json").write_text("{}", encoding="utf-8")
        self._git("add", "staged.py")

        paths = _collect_changed_files(self.root, {"kind": "uncommitted"})

        self.assertEqual(paths, ("existing.py", "staged.py", "untracked.py"))

    def test_collects_base_and_commit_targets(self) -> None:
        (self.root / "feature.py").write_text("feature\n", encoding="utf-8")
        self._git("add", "feature.py")
        self._git("commit", "-m", "feature")
        commit = self._git("rev-parse", "HEAD").stdout.strip()

        self.assertEqual(
            _collect_changed_files(self.root, {"kind": "base", "value": "main^"}),
            ("feature.py",),
        )
        self.assertEqual(
            _collect_changed_files(self.root, {"kind": "commit", "value": commit}),
            ("feature.py",),
        )

    def test_collects_changed_paths_from_each_merge_parent(self) -> None:
        self._git("checkout", "-b", "feature")
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "feature.py").write_text("feature\n", encoding="utf-8")
        self._git("add", "nested/feature.py")
        self._git("commit", "-m", "nested feature")
        self._git("checkout", "main")
        (self.root / "main.py").write_text("main\n", encoding="utf-8")
        self._git("add", "main.py")
        self._git("commit", "-m", "main change")
        self._git("merge", "--no-ff", "feature", "-m", "merge feature")
        merge_commit = self._git("rev-parse", "HEAD").stdout.strip()

        self.assertEqual(
            _collect_changed_files(
                self.root,
                {"kind": "commit", "value": merge_commit},
            ),
            ("main.py", "nested/feature.py"),
        )

    def test_collects_both_rename_paths_for_every_target_kind(self) -> None:
        source = self.root / "source"
        destination = self.root / "destination"
        source.mkdir()
        destination.mkdir()
        (source / "app.py").write_text("app\n", encoding="utf-8")
        self._git("add", "source/app.py")
        self._git("commit", "-m", "source file")
        self._git("mv", "source/app.py", "destination/app.py")

        self.assertEqual(
            _collect_changed_files(self.root, {"kind": "uncommitted"}),
            ("destination/app.py", "source/app.py"),
        )

        self._git("commit", "-m", "move source file")
        commit = self._git("rev-parse", "HEAD").stdout.strip()
        expected = ("destination/app.py", "source/app.py")
        self.assertEqual(
            _collect_changed_files(
                self.root,
                {"kind": "base", "value": f"{commit}^"},
            ),
            expected,
        )
        self.assertEqual(
            _collect_changed_files(
                self.root,
                {"kind": "commit", "value": commit},
            ),
            expected,
        )

    def test_rejects_invalid_target(self) -> None:
        with self.assertRaises(ReviewStateError):
            _collect_changed_files(self.root, {"kind": "base", "value": ""})

    def test_public_interface_returns_only_scoped_guidance(self) -> None:
        global_dir = self.root.parent / ".agents"
        global_dir.mkdir()
        (global_dir / "REVIEW.md").write_text("global rule", encoding="utf-8")
        (self.root / "REVIEW.md").write_text("root rule", encoding="utf-8")
        (self.root / "existing.py").write_text("changed\n", encoding="utf-8")

        guidance = load_classifier_guidance(
            self.root,
            {"kind": "uncommitted"},
            global_agents_dir=global_dir,
        )

        self.assertIn("Guidance for all changed files", guidance)
        self.assertIn("global rule", guidance)
        self.assertIn("root rule", guidance)
        self.assertNotIn(str(global_dir), guidance)
        self.assertNotIn("REVIEW", guidance)

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )


if __name__ == "__main__":
    unittest.main()
