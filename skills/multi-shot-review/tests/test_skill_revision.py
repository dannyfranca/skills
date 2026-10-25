from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from skill_revision import skill_revision  # noqa: E402


class SkillRevisionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.skill = Path(self.tmp.name)
        (self.skill / "scripts" / "harnesses").mkdir(parents=True)
        (self.skill / "references").mkdir()
        (self.skill / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
        (self.skill / "scripts" / "run.py").write_text("print(1)\n", encoding="utf-8")
        (self.skill / "scripts" / "harnesses" / "codex.py").write_text("x = 1\n", encoding="utf-8")
        (self.skill / "references" / "rules.md").write_text("rules\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_same_content_gives_same_revision(self) -> None:
        self.assertEqual(skill_revision(self.skill), skill_revision(self.skill))
        self.assertRegex(skill_revision(self.skill), r"^[0-9a-f]{12}$")

    def test_tracked_change_gives_new_revision(self) -> None:
        for relative in ("SKILL.md", "scripts/harnesses/codex.py", "references/rules.md"):
            with self.subTest(relative=relative):
                before = skill_revision(self.skill)
                path = self.skill / relative
                path.write_text(path.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
                self.assertNotEqual(before, skill_revision(self.skill))

    def test_renamed_file_gives_new_revision(self) -> None:
        before = skill_revision(self.skill)
        (self.skill / "scripts" / "run.py").rename(self.skill / "scripts" / "start.py")
        self.assertNotEqual(before, skill_revision(self.skill))

    def test_generated_and_untracked_files_do_not_change_revision(self) -> None:
        before = skill_revision(self.skill)
        (self.skill / "scripts" / "__pycache__").mkdir()
        (self.skill / "scripts" / "__pycache__" / "run.cpython-314.pyc").write_bytes(b"\0")
        (self.skill / "scripts" / "stale.pyc").write_bytes(b"\0")
        (self.skill / "scripts" / "run.log").write_text("log\n", encoding="utf-8")
        (self.skill / "references" / "scratch.tmp").write_text("tmp\n", encoding="utf-8")
        (self.skill / "reports").mkdir()
        (self.skill / "reports" / "analysis.md").write_text("report\n", encoding="utf-8")
        (self.skill / "README.md").write_text("readme\n", encoding="utf-8")
        self.assertEqual(before, skill_revision(self.skill))

    def test_installed_skill_has_a_revision(self) -> None:
        self.assertRegex(skill_revision(ROOT), r"^[0-9a-f]{12}$")


if __name__ == "__main__":
    unittest.main()
