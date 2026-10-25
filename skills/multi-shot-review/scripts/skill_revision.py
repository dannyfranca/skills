from __future__ import annotations

import hashlib
from pathlib import Path

# Installed copies are not Git checkouts, so a content digest is the only revision that
# identifies the behavior a session ran with. Only source files change that behavior, so logs,
# scratch, and bytecode next to them do not count. Reports live under the review root, not here.
TRACKED_FILES = ("SKILL.md",)
TRACKED_DIRS = ("scripts", "references")
IGNORED_PARTS = frozenset({"__pycache__"})
SOURCE_SUFFIXES = frozenset({".py", ".md", ".json", ".toml"})
REVISION_LENGTH = 12


def skill_revision(skill_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in _tracked_paths(skill_dir):
        digest.update(path.relative_to(skill_dir).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:REVISION_LENGTH]


def _tracked_paths(skill_dir: Path) -> list[Path]:
    paths = [skill_dir / name for name in TRACKED_FILES if (skill_dir / name).is_file()]
    for name in TRACKED_DIRS:
        paths.extend(
            path
            for path in (skill_dir / name).rglob("*")
            if path.is_file()
            and IGNORED_PARTS.isdisjoint(path.relative_to(skill_dir).parts)
            and path.suffix in SOURCE_SUFFIXES
        )
    return sorted(paths, key=lambda path: path.relative_to(skill_dir).as_posix())
