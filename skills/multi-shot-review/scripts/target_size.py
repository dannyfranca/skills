from __future__ import annotations

import subprocess
from pathlib import Path

# Uncommitted targets leave no trace in Git once the work is committed, so evaluations can only
# normalize review results by change size if the session records it at creation.


def measure_target_size(root: Path, target: dict[str, str]) -> dict[str, int] | None:
    numstat = _target_numstat(root, target)
    if numstat is None:
        return None
    files, added, deleted = _sum_numstat(numstat)
    if target["kind"] == "uncommitted":
        untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
        if untracked is None:
            return None
        for name in filter(None, untracked.split("\0")):
            files += 1
            added += _text_line_count(root / name)
    return {"files": files, "added": added, "deleted": deleted}


def _target_numstat(root: Path, target: dict[str, str]) -> str | None:
    kind = target["kind"]
    if kind == "uncommitted":
        return _git(root, "diff", "HEAD", "--numstat")
    if kind == "base":
        return _git(root, "diff", f"{target['value']}...HEAD", "--numstat")
    # Reviewers see a merge commit as its change against the first parent.
    return _git(root, "diff-tree", "--root", "-r", "-m", "--first-parent", "--no-commit-id", "--numstat",
                target["value"])


def _sum_numstat(numstat: str) -> tuple[int, int, int]:
    files = added = deleted = 0
    for line in numstat.splitlines():
        if not line.strip():
            continue
        line_added, line_deleted, _path = line.split("\t", 2)
        files += 1
        # Git prints "-" for binary files, which have no line counts.
        added += int(line_added) if line_added != "-" else 0
        deleted += int(line_deleted) if line_deleted != "-" else 0
    return files, added, deleted


def _text_line_count(path: Path) -> int:
    # Git stores a symlink as its link text, one line without a newline.
    if path.is_symlink():
        return 1
    try:
        content = path.read_bytes()
    except OSError:
        return 0
    if b"\0" in content:
        return 0
    return content.count(b"\n") + (1 if content and not content.endswith(b"\n") else 0)


def _git(root: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False)
    except OSError:
        return None
    return proc.stdout if proc.returncode == 0 else None
