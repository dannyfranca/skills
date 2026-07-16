#!/usr/bin/env python3
"""Review-target validation and deterministic change inventory."""

from __future__ import annotations

import os
import shutil
import subprocess
import stat
import hashlib
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


GITLINK_METADATA_PREFIX = b"GITLINK STATE METADATA (submodule content not materialized): "

@dataclass(frozen=True)
class ChangeInventory:
    files: tuple[str, ...]
    line_counts: dict[str, int]
    fingerprint: str | None = None
    removed_paths: tuple[str, ...] = ()

    @property
    def total_lines(self) -> int:
        return sum(self.line_counts.get(path, 0) for path in self.meaningful_files)

    @property
    def meaningful_files(self) -> tuple[str, ...]:
        return tuple(path for path in self.files if not is_mechanical_file(path))


def validate_review_target(target: Any) -> dict[str, str]:
    if not isinstance(target, dict):
        raise ValueError("review target must be an object")
    kind = target.get("kind")
    if kind == "uncommitted" and set(target) == {"kind"}:
        return {"kind": "uncommitted"}
    if kind == "commit" and set(target) == {"kind", "value"}:
        value = target.get("value")
        if isinstance(value, str) and value.strip():
            return {"kind": kind, "value": value.strip()}
    if kind == "base" and set(target) == {"kind", "value", "head"}:
        value = target.get("value")
        head = target.get("head")
        if isinstance(value, str) and value.strip() and isinstance(head, str) and head.strip():
            return {"kind": kind, "value": value.strip(), "head": head.strip()}
    raise ValueError("review target must be uncommitted, a commit, or a base with pinned head")


def target_cli_args(target: dict[str, str]) -> list[str]:
    target = validate_review_target(target)
    if target["kind"] == "uncommitted":
        return ["--uncommitted"]
    return [f"--{target['kind']}", target["value"]]


def resolve_review_target(root: Path, target: dict[str, str]) -> dict[str, str]:
    """Pin a symbolic base or commit to the commit OID selected at session creation."""
    if not isinstance(target, dict):
        raise ValueError("review target must be an object")
    kind = target.get("kind")
    if kind == "uncommitted":
        return validate_review_target(target)
    if kind not in {"base", "commit"} or set(target) != {"kind", "value"}:
        return validate_review_target(target)
    value = target.get("value")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("review target value must be non-empty")
    oid = _git(root.resolve(), ["rev-parse", "--verify", f"{value}^{{commit}}"]).strip()
    if not oid:
        raise RuntimeError(f"could not resolve {kind} target: {value}")
    if kind == "commit":
        return {"kind": kind, "value": oid}
    head = _git(root.resolve(), ["rev-parse", "--verify", "HEAD^{commit}"]).strip()
    return {"kind": kind, "value": oid, "head": head}


def canonical_repository_root(root: Path) -> Path:
    root = root.resolve()
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return root
    return Path(proc.stdout.strip()).resolve()


def is_mechanical_file(path: str) -> bool:
    name = Path(path).name
    return name in {
        "Cargo.lock",
        "composer.lock",
        "go.sum",
        "package-lock.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
        "yarn.lock",
    }


def collect_change_inventory(root: Path, target: dict[str, str]) -> ChangeInventory:
    root = root.resolve()
    target = validate_review_target(target)
    kind = target["kind"]
    if kind == "uncommitted":
        try:
            comparison = _git(root, ["rev-parse", "--verify", "HEAD"]).strip()
            diff_args = ["diff", "--numstat", "-z", comparison]
            baseline = comparison
        except RuntimeError:
            diff_args = None
            baseline = "unborn"
        include_untracked = True
    elif kind == "base":
        merge_base = _git(root, ["merge-base", target["value"], target["head"]]).strip()
        diff_args = ["diff", "--numstat", "-z", merge_base, target["head"]]
        baseline = merge_base
        include_untracked = False
    else:
        try:
            parent = _git(root, ["rev-parse", "--verify", f"{target['value']}^1"]).strip()
            diff_args = ["diff", "--numstat", "-z", parent, target["value"]]
        except RuntimeError:
            diff_args = [
                "diff-tree",
                "--root",
                "--no-commit-id",
                "--numstat",
                "-r",
                "-z",
                target["value"],
            ]
        baseline = target["value"]
        include_untracked = False

    counts: dict[str, int] = {}
    removed_paths: set[str] = set()
    if diff_args is None:
        for path in _git(root, ["ls-files", "-z"]).split("\0"):
            if not path:
                continue
            try:
                (root / path).lstat()
            except OSError:
                continue
            counts[path] = _count_file_lines(root / path)
    else:
        for path, count, rename_source in _parse_numstat_z(_git(root, diff_args)):
            counts[path] = count
            if rename_source is not None:
                removed_paths.add(rename_source)

    if include_untracked:
        raw = _git(root, ["ls-files", "--others", "--exclude-standard", "-z"])
        for value in raw.split("\0"):
            if not value:
                continue
            path = value
            counts[path] = _count_file_lines(root / path)

    counts = {path: count for path, count in counts.items() if not _is_review_artifact(path)}
    files = tuple(sorted(counts))
    removed = tuple(sorted(path for path in removed_paths if not _is_review_artifact(path)))
    return ChangeInventory(
        files=files,
        line_counts={path: counts[path] for path in files},
        fingerprint=_inventory_fingerprint(root, target, files, removed_paths=removed, baseline=baseline),
        removed_paths=removed,
    )


def _inventory_fingerprint(
    root: Path,
    target: dict[str, str],
    files: tuple[str, ...],
    *,
    removed_paths: tuple[str, ...],
    baseline: str,
) -> str:
    digest = hashlib.sha256()
    digest.update(
        (
            target["kind"]
            + "\0"
            + target.get("value", "")
            + "\0head\0"
            + target.get("head", "")
            + "\0baseline\0"
            + baseline
        ).encode()
    )
    for relative in removed_paths:
        digest.update(b"\0removed-path\0" + relative.encode("utf-8", errors="surrogateescape"))
    if target["kind"] in {"base", "commit"}:
        return digest.hexdigest()
    for relative in files:
        digest.update(b"\0path\0" + relative.encode("utf-8", errors="surrogateescape") + b"\0")
        gitlink_state = _gitlink_state(root, relative)
        if gitlink_state is not None:
            digest.update(b"gitlink\0" + gitlink_state.encode("ascii", errors="backslashreplace"))
            continue
        path = root / relative
        try:
            info = path.lstat()
        except OSError:
            digest.update(b"missing")
            continue
        if stat.S_ISLNK(info.st_mode):
            digest.update(b"symlink\0" + path.readlink().as_posix().encode("utf-8", errors="surrogateescape"))
        elif stat.S_ISREG(info.st_mode):
            digest.update(f"file-mode:{stat.S_IMODE(info.st_mode)}\0".encode())
            try:
                with path.open("rb") as fh:
                    for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError:
                digest.update(b"unreadable")
        else:
            digest.update(f"mode:{info.st_mode}".encode())
    return digest.hexdigest()


@contextmanager
def isolated_review_root(
    repository_root: Path,
    target: dict[str, str],
    changed_files: tuple[str, ...],
    *,
    removed_paths: tuple[str, ...] = (),
    expected_inventory: ChangeInventory | None = None,
) -> Iterator[Path]:
    """Yield a symlink-neutralized tree for the immutable target, then remove it."""
    repository_root = repository_root.resolve()
    target = validate_review_target(target)
    with tempfile.TemporaryDirectory(prefix="multi-shot-review-target-") as temporary:
        snapshot = Path(temporary) / "tree"
        isolated_home = Path(temporary) / "git-home"
        isolated_home.mkdir()
        checkout = (
            target["value"]
            if target["kind"] == "commit"
            else target["head"]
            if target["kind"] == "base"
            else "HEAD"
        )
        filter_overrides = _checkout_filter_overrides(repository_root)
        git_environment = {
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "HOME": str(isolated_home),
            "XDG_CONFIG_HOME": str(isolated_home),
        }
        proc = subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.attributesFile=/dev/null",
                *filter_overrides,
                "worktree",
                "add",
                "--detach",
                "-q",
                str(snapshot),
                checkout,
            ],
            cwd=repository_root,
            env=git_environment,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        registered = proc.returncode == 0
        if not registered:
            head = subprocess.run(
                ["git", "rev-parse", "--verify", "HEAD"],
                cwd=repository_root,
                env=git_environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if target["kind"] != "uncommitted" or head.returncode == 0:
                raise RuntimeError(
                    f"could not create isolated review snapshot: {proc.stderr.strip() or proc.stdout.strip()}"
                )
            snapshot.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=snapshot, check=True)
        try:
            if target["kind"] == "uncommitted":
                _overlay_worktree_changes(
                    repository_root,
                    snapshot,
                    (*changed_files, *removed_paths),
                    preserve_symlinks=True,
                )
            _materialize_gitlink_metadata(snapshot)
            if target["kind"] == "uncommitted":
                if expected_inventory is not None:
                    copied_inventory = collect_change_inventory(snapshot, target)
                    if copied_inventory != expected_inventory:
                        raise RuntimeError(
                            "isolated review snapshot does not match the collected change inventory; retry"
                        )
            _neutralize_symlinks(snapshot)
            yield snapshot
        finally:
            if registered:
                _cleanup_registered_worktree(repository_root, snapshot)


def _checkout_filter_overrides(repository_root: Path) -> list[str]:
    proc = subprocess.run(
        [
            "git",
            "config",
            "--local",
            "--null",
            "--name-only",
            "--get-regexp",
            r"^filter\..*\.(smudge|process|required)$",
        ],
        cwd=repository_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    names = proc.stdout.split(b"\0") if proc.returncode in {0, 1} else []
    drivers = {
        decoded.rsplit(".", 1)[0]
        for raw in names
        if raw
        for decoded in [raw.decode("utf-8", errors="surrogateescape")]
        if decoded.startswith("filter.") and "." in decoded[7:]
    }
    result: list[str] = []
    for driver in sorted(drivers):
        result.extend(["-c", f"{driver}.smudge=", "-c", f"{driver}.process=", "-c", f"{driver}.required=false"])
    return result


def _overlay_worktree_changes(
    repository_root: Path,
    snapshot: Path,
    changed_files: tuple[str, ...],
    *,
    preserve_symlinks: bool,
) -> None:
    for relative in changed_files:
        source = repository_root / relative
        destination = snapshot / relative
        _remove_snapshot_entry(destination)
        gitlink_state = _gitlink_state(repository_root, relative)
        if gitlink_state is not None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(GITLINK_METADATA_PREFIX + gitlink_state.encode("ascii") + b"\n")
            continue
        try:
            metadata = source.lstat()
        except OSError:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if stat.S_ISLNK(metadata.st_mode):
            target = os.readlink(source)
            if preserve_symlinks:
                destination.symlink_to(target)
            else:
                _write_symlink_metadata(destination, target)
        elif stat.S_ISREG(metadata.st_mode):
            shutil.copy2(source, destination, follow_symlinks=False)
        elif stat.S_ISDIR(metadata.st_mode):
            destination.mkdir(exist_ok=True)
        else:
            destination.write_text(f"SPECIAL FILE MODE: {metadata.st_mode:o}\n", encoding="utf-8")


def _materialize_gitlink_metadata(snapshot: Path) -> None:
    staged = _git(snapshot, ["ls-files", "--stage", "-z"])
    for record in staged.split("\0"):
        if not record or "\t" not in record:
            continue
        metadata, relative = record.split("\t", 1)
        fields = metadata.split()
        if len(fields) < 3 or fields[0] != "160000":
            continue
        path = snapshot / relative
        state = _gitlink_state(snapshot, relative)
        if state is None:
            state = f"index:{fields[1]}:worktree:{fields[1]}"
        elif state.endswith(":worktree:unavailable"):
            state = f"index:{fields[1]}:worktree:{fields[1]}"
        _remove_snapshot_entry(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(GITLINK_METADATA_PREFIX + state.encode("ascii") + b"\n")


def _neutralize_symlinks(root: Path) -> None:
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in list(directories):
            path = current_path / name
            if path.is_symlink():
                directories.remove(name)
                target = os.readlink(path)
                path.unlink()
                _write_symlink_metadata(path, target)
        for name in files:
            path = current_path / name
            if path.is_symlink():
                target = os.readlink(path)
                path.unlink()
                _write_symlink_metadata(path, target)


def _write_symlink_metadata(path: Path, target: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"SYMLINK TARGET METADATA (do not follow): " + target.encode("utf-8", errors="surrogateescape") + b"\n")


def _remove_snapshot_entry(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError:
        return
    if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
        shutil.rmtree(path)
    else:
        path.unlink()


def _cleanup_registered_worktree(repository_root: Path, snapshot: Path) -> None:
    cleanup = subprocess.run(
        ["git", "worktree", "remove", "--force", str(snapshot)],
        cwd=repository_root,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if cleanup.returncode == 0:
        return
    try:
        _remove_snapshot_entry(snapshot)
    except OSError:
        pass
    prune = subprocess.run(
        ["git", "worktree", "prune", "--expire", "now"],
        cwd=repository_root,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    listed = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repository_root,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if prune.returncode != 0 or listed.returncode != 0 or f"worktree {snapshot}\n" in listed.stdout:
        details = cleanup.stderr.strip() or cleanup.stdout.strip() or "worktree remove failed"
        raise RuntimeError(f"could not clean isolated review snapshot: {details}")


def _gitlink_state(root: Path, relative: str) -> str | None:
    staged = _git(root, ["ls-files", "--stage", "-z", "--", relative])
    for record in staged.split("\0"):
        if not record or "\t" not in record:
            continue
        metadata, path = record.split("\t", 1)
        fields = metadata.split()
        if path != relative or len(fields) < 3 or fields[0] != "160000":
            continue
        index_oid = fields[1]
        worktree_path = root / relative
        try:
            if worktree_path.is_file():
                raw = worktree_path.read_bytes()
                if raw.startswith(GITLINK_METADATA_PREFIX):
                    return raw[len(GITLINK_METADATA_PREFIX):].rstrip(b"\n").decode("ascii")
        except OSError:
            pass
        try:
            worktree_oid = _git(root, ["-C", relative, "rev-parse", "--verify", "HEAD"]).strip()
        except RuntimeError:
            worktree_oid = "unavailable"
        if worktree_oid != "unavailable":
            dirty = _git(root, ["-C", relative, "status", "--porcelain=v1", "-z", "--untracked-files=all"])
            if dirty:
                raise RuntimeError(
                    f"dirty submodule contents are not supported for review target {relative}; "
                    "commit the submodule changes or review that repository separately"
                )
        return f"index:{index_oid}:worktree:{worktree_oid}"
    return None


def _git(root: Path, args: list[str]) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"git exited with {proc.returncode}"
        raise RuntimeError(detail)
    return proc.stdout


def _parse_numstat_z(raw: str) -> list[tuple[str, int, str | None]]:
    records = raw.split("\0")
    result: list[tuple[str, int, str | None]] = []
    index = 0
    while index < len(records):
        header = records[index]
        index += 1
        if not header:
            continue
        parts = header.split("\t", 2)
        if len(parts) != 3:
            continue
        added, removed, path = parts
        rename_source: str | None = None
        if not path:
            if index + 1 >= len(records):
                continue
            rename_source = records[index]
            path = records[index + 1]
            index += 2
        result.append((path, _numstat_value(added) + _numstat_value(removed), rename_source))
    return result


def _numstat_value(value: str) -> int:
    return int(value) if value.isdigit() else 0


def _count_file_lines(path: Path) -> int:
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            return 0
        with path.open("rb") as fh:
            return sum(1 for _line in fh)
    except OSError:
        return 0


def _is_review_artifact(path: str) -> bool:
    return Path(path).parts[:1] == (".review",)
