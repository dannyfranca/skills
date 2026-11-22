from __future__ import annotations

import subprocess
from pathlib import Path


class GitGaveNoAnswer(Exception):
    """Git failed. That is not the answer "the commit has no file there"."""


def git(repository: Path, *args: str) -> str | None:
    """None when Git cannot answer: an evaluation reads repositories that can be gone or damaged."""

    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repository,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


def resolved_commit(repository: Path, name: str) -> str | None:
    """The full commit ID. A name such as HEAD moves, and a saved sample must open the same commit later."""

    output = git(repository, "rev-parse", "--verify", "--quiet", f"{name}^{{commit}}")
    return (output or "").strip() or None


def file_version(repository: Path, commit: str, path: str) -> str | None:
    """The mode and the object ID of the file at the path in the commit: file content, or the text of a symbolic link.

    The mode is part of the version: a staged executable bit changes no content.

    None when the commit has no file there: no entry, a directory, or a submodule. The commit must
    resolve in the repository, so a failure of Git is not an answer about the file.
    """

    # --literal-pathspecs: a file name can start with `:` or hold `*`, which Git reads as a pattern.
    # --full-tree: the repository can be a subdirectory, and the path is relative to the top.
    output = git(repository, "--literal-pathspecs", "ls-tree", "--full-tree", "-z", commit, "--", path)
    if output is None:
        raise GitGaveNoAnswer(f"git ls-tree {commit} in {repository}")
    for entry in output.split("\0"):
        details, _, name = entry.partition("\t")
        # Git also answers for `pkg/` and `./a.py`, and `git show <commit>:<path>` needs the same name.
        if name == path:
            mode, kind, entry_id = details.split(" ")
            return f"{mode} {entry_id}" if kind == "blob" else None
    return None

