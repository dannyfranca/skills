#!/usr/bin/env python3
"""Discover and render classifier-only REVIEW instructions."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from review_state import ReviewStateError


DEFAULT_MAX_BYTES = 32 * 1024
DEFAULT_REVIEW_FILE = "REVIEW"


@dataclass(frozen=True)
class _ScopedGuidance:
    """Normalized classifier guidance with all source mechanics removed."""

    scope: str
    content: str
    truncated: bool = False


def load_classifier_guidance(
    root: Path,
    target: dict[str, str],
    *,
    review_file: str = DEFAULT_REVIEW_FILE,
    global_agents_dir: Path | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> str:
    """Resolve all REVIEW mechanics into opaque scoped classifier guidance."""

    return _render_review_instructions(
        _discover_review_instructions(
            root,
            _collect_changed_files(root, target),
            review_file=review_file,
            global_agents_dir=global_agents_dir,
            max_bytes=max_bytes,
        )
    )


def _collect_changed_files(root: Path, target: dict[str, str]) -> tuple[str, ...]:
    """Return deterministic repository-relative paths for the live review target."""

    root = root.resolve()
    kind = target.get("kind")
    if kind == "uncommitted" and set(target) == {"kind"}:
        if _git_succeeds(root, ["rev-parse", "--verify", "HEAD"]):
            tracked = _git_paths(root, ["diff", "--name-only", "-z", "HEAD"])
        else:
            tracked = _git_paths(root, ["ls-files", "--cached", "-z"])
        untracked = _git_paths(root, ["ls-files", "--others", "--exclude-standard", "-z"])
        paths = {*tracked, *untracked}
    elif kind == "base" and _valid_target_value(target):
        paths = set(
            _git_paths(
                root,
                ["diff", "--name-only", "-z", f"{target['value']}...HEAD"],
            )
        )
    elif kind == "commit" and _valid_target_value(target):
        paths = set(
            _git_paths(
                root,
                [
                    "diff-tree",
                    "--root",
                    "--no-commit-id",
                    "--name-only",
                    "-r",
                    "-z",
                    target["value"],
                ],
            )
        )
    else:
        raise ReviewStateError("invalid review target for REVIEW instruction discovery")

    return tuple(
        sorted(
            path
            for path in paths
            if _safe_relative_path(path) is not None and not _is_review_artifact(path)
        )
    )


def _discover_review_instructions(
    root: Path,
    changed_files: Iterable[str],
    *,
    review_file: str = DEFAULT_REVIEW_FILE,
    global_agents_dir: Path | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> tuple[_ScopedGuidance, ...]:
    """Apply OpenAI-shaped override and root-to-leaf discovery semantics."""

    if max_bytes <= 0:
        raise ReviewStateError("REVIEW instruction max bytes must be positive")
    review_base, review_override = _review_filenames(review_file)

    root = root.resolve()
    global_agents_dir = (
        Path.home() / ".agents"
        if global_agents_dir is None
        else global_agents_dir.resolve()
    )
    loaded: list[_ScopedGuidance] = []

    global_instruction = _load_global_instruction(
        global_agents_dir,
        max_bytes,
        review_base=review_base,
        review_override=review_override,
    )
    if global_instruction is not None:
        loaded.append(global_instruction)

    directories = _applicable_directories(root, changed_files)
    remaining = max_bytes
    for directory in directories:
        if remaining <= 0:
            break
        candidate = _select_project_candidate(
            directory,
            review_base=review_base,
            review_override=review_override,
        )
        if candidate is None:
            continue
        content, consumed, truncated = _read_limited_utf8(candidate, remaining)
        if not content.strip():
            continue
        remaining -= consumed
        scope = "."
        if directory != root:
            scope = directory.relative_to(root).as_posix()
        loaded.append(
            _ScopedGuidance(
                scope=scope,
                content=content.strip(),
                truncated=truncated,
            )
        )

    return tuple(loaded)


def _render_review_instructions(instructions: Iterable[_ScopedGuidance]) -> str:
    """Render scoped classifier guidance while hiding its discovery mechanism."""

    instructions = tuple(instructions)
    if not instructions:
        return "(no additional scoped guidance)"

    blocks: list[str] = []
    for instruction in instructions:
        heading = (
            "Guidance for all changed files"
            if instruction.scope == "*"
            else f"Guidance for changed descendants of {instruction.scope}"
        )
        truncated = " (truncated at byte limit)" if instruction.truncated else ""
        blocks.append(
            f"### {heading}{truncated}\n\n"
            f"{instruction.content}"
        )
    return "\n\n".join(blocks)


def _applicable_directories(root: Path, changed_files: Iterable[str]) -> tuple[Path, ...]:
    directories: list[Path] = [root]
    seen = {root}
    for changed in sorted(set(changed_files)):
        relative = _safe_relative_path(changed)
        if relative is None:
            continue
        current = root
        for part in relative.parts[:-1]:
            current /= part
            if current not in seen:
                seen.add(current)
                directories.append(current)
    return tuple(directories)


def _load_global_instruction(
    global_agents_dir: Path,
    max_bytes: int,
    *,
    review_base: str,
    review_override: str,
) -> _ScopedGuidance | None:
    for candidate in (
        global_agents_dir / review_override,
        global_agents_dir / review_base,
    ):
        if not candidate.is_file():
            continue
        content, _consumed, truncated = _read_limited_utf8(candidate, max_bytes)
        if not content.strip():
            continue
        return _ScopedGuidance(
            scope="*",
            content=content.strip(),
            truncated=truncated,
        )
    return None


def _select_project_candidate(
    directory: Path,
    *,
    review_base: str,
    review_override: str,
) -> Path | None:
    for name in (review_override, review_base):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def _review_filenames(review_file: str) -> tuple[str, str]:
    return f"{review_file}.md", f"{review_file}.override.md"


def _read_limited_utf8(path: Path, max_bytes: int) -> tuple[str, int, bool]:
    try:
        raw = path.read_bytes()
        raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ReviewStateError(f"could not read REVIEW instructions from {path}: {exc}") from exc
    truncated = len(raw) > max_bytes
    selected = raw[:max_bytes]
    content = selected.decode("utf-8", errors="ignore") if truncated else selected.decode("utf-8")
    return content, len(selected), truncated


def _git_paths(root: Path, args: list[str]) -> tuple[str, ...]:
    proc = _run_git(root, args)
    return tuple(
        os.fsdecode(value)
        for value in proc.stdout.split(b"\0")
        if value
    )


def _git_succeeds(root: Path, args: list[str]) -> bool:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return proc.returncode == 0


def _run_git(root: Path, args: list[str]) -> subprocess.CompletedProcess[bytes]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise ReviewStateError(f"could not inspect review target with Git: {exc}") from exc
    if proc.returncode != 0:
        details = os.fsdecode(proc.stderr).strip() or "Git command failed"
        raise ReviewStateError(f"could not inspect review target with Git: {details}")
    return proc


def _valid_target_value(target: dict[str, str]) -> bool:
    return (
        set(target) == {"kind", "value"}
        and isinstance(target.get("value"), str)
        and bool(target["value"].strip())
    )


def _safe_relative_path(value: str) -> PurePosixPath | None:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        return None
    return path


def _is_review_artifact(value: str) -> bool:
    path = _safe_relative_path(value)
    return path is not None and bool(path.parts) and path.parts[0] == ".review"
