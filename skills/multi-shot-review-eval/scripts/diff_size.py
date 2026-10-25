from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from review_target import TargetKind

if TYPE_CHECKING:
    from session_store import Session


@dataclass(frozen=True)
class DiffSize:
    files: int
    added: int
    deleted: int

    @property
    def changed_lines(self) -> int:
        return self.added + self.deleted


def resolve_diff_size(session: Session) -> DiffSize | None:
    """Prefer the size recorded at review time; otherwise recompute only a commit, whose diff never drifts.

    A base target compared against today's HEAD would measure later work, not the reviewed change.
    """

    if session.target_size is not None:
        return session.target_size
    commit = _commit_target(session)
    if commit is None or session.root is None:
        return None
    # --root diffs a parentless commit against the empty tree; a merge counts only what it adds to its first parent.
    output = _git(
        session.root, "diff-tree", "--root", "--diff-merges=first-parent", "--no-commit-id", "-r", "--numstat", commit
    )
    return None if output is None else _parse_numstat(output)


def target_resolves(session: Session) -> bool:
    commit = _commit_target(session)
    if commit is None or session.root is None:
        return False
    return _git(session.root, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}") is not None


def _commit_target(session: Session) -> str | None:
    value = session.target.value
    if not value or session.root is None or not session.root.is_dir():
        return None
    match session.target.kind:
        case TargetKind.COMMIT:
            return value
        case TargetKind.BASE | TargetKind.UNCOMMITTED | TargetKind.UNKNOWN:
            return None


def _git(root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _parse_numstat(output: str) -> DiffSize:
    files = added = deleted = 0
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        files += 1
        # Binary files report "-" for both counts; they change no reviewable lines.
        added += int(parts[0]) if parts[0].isdigit() else 0
        deleted += int(parts[1]) if parts[1].isdigit() else 0
    return DiffSize(files=files, added=added, deleted=deleted)
