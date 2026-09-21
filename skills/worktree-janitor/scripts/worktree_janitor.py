#!/usr/bin/env python3
"""Bounded, recoverable cleanup for ephemeral Git worktrees."""

from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
import platform
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Iterable


CLEAN_HOURS = 48
DIRTY_DAYS = 7
ARCHIVE_DAYS = 30
STATE_VERSION = 1
ARCHIVE_PREFIX = "refs/archive/worktree-janitor"
CARGO_CACHE_SIGNATURE = "Signature: 8a477f597d28d172789f06886806bc55"


class JanitorError(RuntimeError):
    pass


class MutationError(JanitorError):
    pass


class PartialMutationError(MutationError):
    pass


class SafetyHold(MutationError):
    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason


class InvalidWorktree(JanitorError):
    pass


class CliError(JanitorError):
    pass


@dataclasses.dataclass(frozen=True)
class Policy:
    clean_age: dt.timedelta = dt.timedelta(hours=CLEAN_HOURS)
    dirty_age: dt.timedelta = dt.timedelta(days=DIRTY_DAYS)
    archive_age: dt.timedelta = dt.timedelta(days=ARCHIVE_DAYS)


@dataclasses.dataclass
class Worktree:
    path: Path
    git_file: Path
    admin_dir: Path
    common_dir: Path
    head: str
    age: dt.timedelta
    locked: bool
    dirty: bool
    status: str
    dirty_submodule: bool
    embedded_repository: bool
    git_file_identity: tuple[int, int, int]


@dataclasses.dataclass(frozen=True)
class OrphanWorktree:
    path: Path
    admin_dir: Path
    age: dt.timedelta
    directory_identity: tuple[int, int]
    pointer_identity: tuple[int, int, int, int]


@dataclasses.dataclass
class Scan:
    orphan_expired: list[OrphanWorktree] = dataclasses.field(default_factory=list)
    clean_expired: list[Worktree] = dataclasses.field(default_factory=list)
    dirty_expired: list[Worktree] = dataclasses.field(default_factory=list)
    target_cleanup: list[tuple[Worktree, list[Path]]] = dataclasses.field(default_factory=list)
    skipped: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    events: list[dict] = dataclasses.field(default_factory=list)
    scanned: int = 0


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("timestamp lacks timezone")
    return parsed


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return path != root
    except ValueError:
        return False


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return (slug or "worktree")[:48]


def git(
    args: Iterable[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    command_env = os.environ.copy()
    command_env["GIT_OPTIONAL_LOCKS"] = "0"
    if env:
        command_env.update(env)
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=command_env,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise JanitorError(f"git {' '.join(args)} failed: {detail}")
    return result


class ProcessLock:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.path = cache_dir / "lock"
        self.handle = None

    def acquire(self) -> bool:
        self.cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.handle.close()
            self.handle = None
            return False
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(json.dumps({"pid": os.getpid(), "started_at": iso(utc_now())}) + "\n")
        self.handle.flush()
        os.fsync(self.handle.fileno())
        os.chmod(self.path, 0o600)
        return True

    def close(self) -> None:
        if self.handle:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None

    def __enter__(self) -> "ProcessLock":
        if not self.acquire():
            raise BlockingIOError("janitor already running")
        return self

    def __exit__(self, *_args) -> None:
        self.close()


class StateStore:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.path = cache_dir / "state.json"
        self.data = {"version": STATE_VERSION, "archives": []}

    def load(self) -> dict:
        if not self.path.exists():
            return self.data
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise JanitorError(f"cannot read state {self.path}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise JanitorError(f"unsupported state schema in {self.path}")
        if loaded.get("version") != STATE_VERSION or not isinstance(loaded.get("archives"), list):
            raise JanitorError(f"unsupported state schema in {self.path}")
        seen_ids: set[str] = set()
        for record in loaded["archives"]:
            self.validate_record(record, seen_ids)
        self.data = loaded
        return loaded

    @staticmethod
    def validate_record(record: object, seen_ids: set[str]) -> None:
        if not isinstance(record, dict):
            raise JanitorError("archive state contains a non-object record")
        required_strings = ("id", "repository", "ref", "oid", "head", "kind", "original_path", "created_at")
        if any(not isinstance(record.get(field), str) or not record[field] for field in required_strings):
            raise JanitorError("archive state contains missing or invalid string fields")
        if record["id"] in seen_ids:
            raise JanitorError(f"archive state contains duplicate id: {record['id']}")
        seen_ids.add(record["id"])
        if not Path(record["repository"]).is_absolute():
            raise JanitorError("archive repository path must be absolute")
        if not record["ref"].startswith(f"{ARCHIVE_PREFIX}/"):
            raise JanitorError(f"archive ref is outside the janitor namespace: {record['ref']}")
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", record["oid"]):
            raise JanitorError(f"archive has invalid object id: {record['id']}")
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", record["head"]):
            raise JanitorError(f"archive has invalid head id: {record['id']}")
        if record["kind"] not in {"detached-head", "dirty-snapshot"}:
            raise JanitorError(f"archive has invalid kind: {record['kind']}")
        for field in ("created_at", "removed_at", "expires_at"):
            value = record.get(field)
            if value is not None:
                if not isinstance(value, str):
                    raise JanitorError(f"archive has invalid {field}: {record['id']}")
                try:
                    parse_time(value)
                except ValueError as exc:
                    raise JanitorError(f"archive has invalid {field}: {record['id']}") from exc

    def save(self) -> None:
        self.cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".state-", dir=self.cache_dir)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            directory = os.open(self.cache_dir, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def find(self, archive_id: str) -> dict | None:
        return next((item for item in self.data["archives"] if item["id"] == archive_id), None)

    def existing_for(self, common_dir: Path, original_path: Path, head: str, kind: str) -> dict | None:
        for item in self.data["archives"]:
            if (
                item["repository"] == str(common_dir)
                and item["original_path"] == str(original_path)
                and item["head"] == head
                and item["kind"] == kind
                and item.get("removed_at") is None
                and ref_matches(common_dir, item["ref"], item["oid"])
            ):
                return item
        return None


def default_active_paths() -> set[Path]:
    system = platform.system()
    if system == "Darwin":
        return darwin_active_paths()
    if system == "Linux":
        return linux_active_paths()
    raise JanitorError(f"unsupported platform: {system}")


def darwin_active_paths() -> set[Path]:
    executable = shutil.which("lsof")
    if not executable:
        raise JanitorError("active-process detection unavailable: lsof missing")
    username = pwd.getpwuid(os.geteuid()).pw_name
    result = subprocess.run(
        [executable, "-a", "-d", "cwd", "-u", username, "-Fn"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise JanitorError(f"active-process detection failed: {result.stderr.strip()}")
    return {Path(line[1:]).resolve(strict=False) for line in result.stdout.splitlines() if line.startswith("n/")}


def linux_active_paths(proc: Path = Path("/proc"), uid: int | None = None) -> set[Path]:
    if not proc.is_dir():
        raise JanitorError("active-process detection unavailable: /proc missing")
    paths: set[Path] = set()
    effective_uid = os.geteuid() if uid is None else uid
    for process_dir in proc.iterdir():
        if not process_dir.name.isdigit():
            continue
        try:
            status = (process_dir / "status").read_text(encoding="utf-8", errors="replace")
            uid_line = next(line for line in status.splitlines() if line.startswith("Uid:"))
            if int(uid_line.split()[2]) != effective_uid:
                continue
        except FileNotFoundError:
            continue
        except PermissionError as exc:
            raise JanitorError(f"active-process detection denied for {process_dir}") from exc
        except (StopIteration, ValueError, OSError) as exc:
            raise JanitorError(f"active-process detection failed for {process_dir}: {exc}") from exc
        try:
            paths.add((process_dir / "cwd").resolve(strict=True))
        except FileNotFoundError:
            continue
        except PermissionError:
            # Linux hides cwd for some non-dumpable same-user processes, such
            # as the user systemd manager and desktop security services. Their
            # unreadable cwd must not make process discovery unusable systemwide.
            continue
        except OSError as exc:
            raise JanitorError(f"active-process detection failed for {process_dir}: {exc}") from exc
    return paths


def parse_git_file(git_file: Path) -> Path:
    if git_file.is_symlink() or not git_file.is_file():
        raise InvalidWorktree(".git is not a regular pointer file")
    try:
        line = git_file.read_text(encoding="utf-8").strip()
    except UnicodeError as exc:
        raise InvalidWorktree("invalid .git pointer encoding") from exc
    if not line.startswith("gitdir: "):
        raise InvalidWorktree("invalid .git pointer")
    value = Path(line.removeprefix("gitdir: "))
    return (value if value.is_absolute() else git_file.parent / value).resolve(strict=False)


def registered_worktrees(common_dir: Path) -> dict[Path, dict]:
    result = git([f"--git-dir={common_dir}", "worktree", "list", "--porcelain"])
    records: dict[Path, dict] = {}
    current: dict | None = None
    for line in [*result.stdout.splitlines(), ""]:
        if line.startswith("worktree "):
            current = {"path": line.removeprefix("worktree "), "locked": False}
        elif current is not None and line.startswith("HEAD "):
            current["head"] = line.removeprefix("HEAD ")
        elif current is not None and line.startswith("locked"):
            current["locked"] = True
        elif not line and current is not None:
            path = Path(current["path"]).resolve(strict=False)
            records[path] = current
            current = None
    return records


def discover(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        base = Path(directory)
        names[:] = [name for name in names if not (base / name).is_symlink()]
        git_path = base / ".git"
        if ".git" in files and git_path.is_file() and not git_path.is_symlink():
            resolved = base.resolve(strict=False)
            if is_within(resolved, root):
                candidates.append(resolved)
            names[:] = []
        elif ".git" in names and git_path.is_dir():
            names[:] = []
    return sorted(set(candidates), key=str)


def index_audit_paths(path: Path) -> list[str]:
    result = git(["ls-files", "-v", "-z"], cwd=path)
    paths: list[str] = []
    for entry in result.stdout.split("\0"):
        if len(entry) < 3 or entry[1] != " ":
            continue
        tag, relative = entry[0], entry[2:]
        if tag.islower() or (tag == "S" and os.path.lexists(path / relative)):
            paths.append(relative)
    return paths


def worktree_status(path: Path) -> str:
    status = git(
        ["status", "--porcelain=v1", "--untracked-files=normal", "--ignore-submodules=none"],
        cwd=path,
    ).stdout
    hidden = index_audit_paths(path)
    return status + "".join(f"! index-flagged path: {relative}\n" for relative in hidden)


def tracked_submodule_roots(path: Path) -> set[Path]:
    result = git(["ls-files", "--stage", "-z"], cwd=path)
    roots: set[Path] = set()
    for entry in result.stdout.split("\0"):
        if not entry or "\t" not in entry:
            continue
        metadata, relative = entry.split("\t", 1)
        if metadata.split(" ", 1)[0] == "160000":
            roots.add((path / relative).resolve(strict=False))
    return roots


def has_dirty_submodule(path: Path, common_dir: Path | None = None) -> bool:
    roots = tracked_submodule_roots(path)
    if not roots:
        return False
    result = git(["submodule", "status", "--recursive"], cwd=path, check=False)
    if result.returncode:
        raise JanitorError(f"cannot inspect submodules in {path}: {result.stderr.strip()}")
    if any(line and line[0] in "+U" for line in result.stdout.splitlines()):
        return True
    direct = {str(root.relative_to(path.resolve(strict=False))) for root in roots}
    for line in result.stdout.splitlines():
        match = re.match(r"^.[0-9a-f]{40,64} (.*?)(?: \(.*\))?$", line)
        if match and match.group(1) not in direct:
            return True
    for submodule in roots:
        if not submodule.exists():
            continue
        status = worktree_status(submodule)
        if status:
            return True
        repository = common_dir
        if repository is None:
            repository = Path(
                git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=path).stdout.strip()
            ).resolve(strict=False)
        relative = submodule.relative_to(path.resolve(strict=False))
        durable_repo = repository / "modules" / relative
        oid = git(["rev-parse", "HEAD"], cwd=submodule).stdout.strip()
        durable_object = git(
            [f"--git-dir={durable_repo}", "cat-file", "-e", f"{oid}^{{commit}}"],
            check=False,
        )
        if durable_object.returncode != 0:
            return True
        retained = git(
            [
                f"--git-dir={durable_repo}",
                "for-each-ref",
                f"--contains={oid}",
                "--format=%(refname)",
                "refs/heads",
                "refs/remotes",
                "refs/tags",
            ],
            check=False,
        )
        if retained.returncode != 0:
            raise JanitorError(f"cannot verify durable submodule refs for {submodule}: {retained.stderr.strip()}")
        if not retained.stdout.strip():
            return True
    return False


def has_embedded_repository(path: Path) -> bool:
    path = path.resolve(strict=False)
    submodule_roots = tracked_submodule_roots(path)

    def traversal_failed(error: OSError) -> None:
        raise JanitorError(f"cannot inspect nested repositories in {path}: {error}")

    for directory, names, files in os.walk(
        path,
        topdown=True,
        followlinks=False,
        onerror=traversal_failed,
    ):
        base = Path(directory).resolve(strict=False)
        if any(base == root or is_within(base, root) for root in submodule_roots):
            names[:] = []
            continue
        names[:] = [name for name in names if not (base / name).is_symlink()]
        if base != path and ".git" in {*names, *files}:
            return True
        if (
            base != path
            and "HEAD" in files
            and "objects" in names
            and "refs" in names
        ):
            return True
    return False


def cargo_target(path: Path) -> bool:
    if path.is_symlink() or path.name != "target" or not path.is_dir():
        return False

    def regular_file(candidate: Path) -> bool:
        try:
            return stat.S_ISREG(candidate.stat(follow_symlinks=False).st_mode)
        except OSError:
            return False

    if regular_file(path / ".rustc_info.json"):
        return True
    tag = path / "CACHEDIR.TAG"
    if regular_file(tag):
        try:
            if CARGO_CACHE_SIGNATURE in tag.read_text(encoding="utf-8", errors="replace")[:512]:
                return True
        except OSError:
            return False
    return regular_file(path.parent / "Cargo.toml")


def cargo_targets(worktree: Path, root: Path) -> list[Path]:
    targets: list[Path] = []
    for directory, names, _files in os.walk(worktree, topdown=True, followlinks=False):
        base = Path(directory)
        kept: list[str] = []
        for name in names:
            candidate = base / name
            if candidate.is_symlink():
                continue
            if name == ".git":
                continue
            if name == "target" and cargo_target(candidate):
                resolved = candidate.resolve(strict=False)
                if is_within(resolved, root):
                    targets.append(resolved)
                continue
            kept.append(name)
        names[:] = kept
    return sorted(set(targets), key=str)


def ref_exists(common_dir: Path, ref: str) -> bool:
    return git([f"--git-dir={common_dir}", "show-ref", "--verify", "--quiet", ref], check=False).returncode == 0


def ref_oid(common_dir: Path, ref: str) -> str | None:
    if not common_dir.is_dir():
        return None
    symbolic = git([f"--git-dir={common_dir}", "symbolic-ref", "-q", ref], check=False)
    if symbolic.returncode == 0:
        raise JanitorError(f"archive ref is symbolic: {ref}")
    if symbolic.returncode != 1:
        raise JanitorError(
            f"cannot inspect archive ref {ref}: {symbolic.stderr.strip() or symbolic.stdout.strip()}"
        )
    result = git(
        [f"--git-dir={common_dir}", "rev-parse", "--verify", "--quiet", ref],
        check=False,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    if result.returncode == 1:
        return None
    raise JanitorError(
        f"cannot inspect archive ref {ref}: {result.stderr.strip() or result.stdout.strip()}"
    )


def ref_matches(common_dir: Path, ref: str, oid: str) -> bool:
    return ref_oid(common_dir, ref) == oid


class Janitor:
    def __init__(
        self,
        root: Path,
        cache_dir: Path,
        policy: Policy,
        *,
        now: dt.datetime | None = None,
        active_paths: Callable[[], set[Path]] = default_active_paths,
    ):
        self.configured_root = root.expanduser().absolute()
        self.root = self.configured_root.resolve(strict=False)
        self.cache_dir = cache_dir.expanduser().resolve(strict=False)
        self.policy = policy
        self.now = now or utc_now()
        self.active_paths_provider = active_paths
        self.registration_cache: dict[Path, dict[Path, dict]] = {}
        self.state = StateStore(self.cache_dir)
        self.destructive_attempted = False

    def inspect(self, path: Path) -> Worktree:
        path = path.resolve(strict=False)
        git_file = path / ".git"
        try:
            git_stat = git_file.stat(follow_symlinks=False)
        except OSError as exc:
            raise InvalidWorktree(f"cannot stat .git pointer: {exc}") from exc
        admin_dir = parse_git_file(git_file)
        if not admin_dir.is_dir():
            raise InvalidWorktree("linked-worktree admin directory missing")
        reported_admin = Path(
            git(["rev-parse", "--path-format=absolute", "--absolute-git-dir"], cwd=path).stdout.strip()
        ).resolve(strict=False)
        if reported_admin != admin_dir:
            raise InvalidWorktree(".git pointer does not match Git's worktree admin directory")
        common = Path(
            git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=path).stdout.strip()
        ).resolve(strict=False)
        registrations = self.registration_cache.setdefault(common, registered_worktrees(common))
        record = registrations.get(path.resolve(strict=False))
        if not record:
            raise InvalidWorktree("path does not exactly match Git worktree registration")
        head = git(["rev-parse", "HEAD"], cwd=path).stdout.strip()
        if record.get("head") != head:
            raise InvalidWorktree("Git worktree registration HEAD does not match the worktree")
        age = max(dt.timedelta(), self.now - dt.datetime.fromtimestamp(git_stat.st_mtime, dt.timezone.utc))
        status = worktree_status(path)
        dirty = bool(status)
        return Worktree(
            path=path,
            git_file=git_file,
            admin_dir=admin_dir,
            common_dir=common,
            head=head,
            age=age,
            locked=bool(record.get("locked")) or (admin_dir / "locked").exists(),
            dirty=dirty,
            status=status,
            dirty_submodule=has_dirty_submodule(path, common),
            embedded_repository=has_embedded_repository(path),
            git_file_identity=(git_stat.st_dev, git_stat.st_ino, git_stat.st_mtime_ns),
        )

    def inspect_orphan(self, path: Path) -> OrphanWorktree | None:
        # A missing admin directory is distinct from malformed or inaccessible metadata.
        if path.is_symlink() or path.resolve(strict=False) != path or not is_within(path, self.root):
            return None
        pointer = path / ".git"
        try:
            admin = parse_git_file(pointer)
        except InvalidWorktree:
            return None
        # Require the standard linked-worktree layout. The source repository
        # itself may have been deleted, so its continued existence is not required.
        if admin.parent.name != "worktrees" or admin.parent.parent.name != ".git":
            return None
        try:
            admin.lstat()
        except FileNotFoundError:
            pass
        else:
            return None
        directory_stat = path.stat(follow_symlinks=False)
        pointer_stat = pointer.stat(follow_symlinks=False)
        return OrphanWorktree(
            path=path,
            admin_dir=admin,
            age=max(dt.timedelta(), self.now - dt.datetime.fromtimestamp(pointer_stat.st_mtime, dt.timezone.utc)),
            directory_identity=(directory_stat.st_dev, directory_stat.st_ino),
            pointer_identity=(pointer_stat.st_dev, pointer_stat.st_ino, pointer_stat.st_mtime_ns, pointer_stat.st_ctime_ns),
        )

    def orphan_hold_reason(self, orphan: OrphanWorktree) -> str | None:
        if orphan.age < max(self.policy.clean_age, self.policy.dirty_age):
            return "orphan_grace"

        def traversal_failed(error: OSError) -> None:
            raise JanitorError(f"cannot inspect orphan {orphan.path}: {error}")

        for directory, names, files in os.walk(orphan.path, followlinks=False, onerror=traversal_failed):
            base = Path(directory)
            if os.path.ismount(base):
                return "mount_point"
            if base != orphan.path and ".git" in {*names, *files}:
                marker = base / ".git"
                # Review sessions leave empty .git directory markers, not repositories.
                if marker.is_symlink() or not marker.is_dir() or any(marker.iterdir()):
                    return "embedded_repository"
            if "HEAD" in files and "objects" in names and "refs" in names:
                return "embedded_repository"
            names[:] = [name for name in names if not (base / name).is_symlink()]
        return None

    def remove_orphan(self, orphan: OrphanWorktree) -> None:
        current = self.inspect_orphan(orphan.path)
        if current != orphan:
            raise SafetyHold("changed_after_scan", "orphan identity or missing metadata changed after audit")
        self.ensure_inactive(orphan)
        reason = self.orphan_hold_reason(orphan)
        if reason:
            raise SafetyHold(reason, "orphan became ineligible after audit")
        self.destructive_attempted = True
        shutil.rmtree(orphan.path)

    def scan(self) -> Scan:
        result = Scan()
        if self.configured_root.is_symlink():
            raise JanitorError(f"worktree root must not be a symlink: {self.configured_root}")
        if not self.root.exists():
            return result
        if not self.root.is_dir():
            raise JanitorError(f"worktree root is not a directory: {self.root}")
        active = {path.resolve(strict=False) for path in self.active_paths_provider()}
        for path in discover(self.root):
            result.scanned += 1
            try:
                worktree = self.inspect(path)
            except InvalidWorktree as exc:
                orphan = self.inspect_orphan(path)
                if orphan is not None:
                    reason = self.orphan_hold_reason(orphan)
                    if any(cwd == path or is_within(cwd, path) for cwd in active):
                        reason = "active"
                    if reason:
                        result.skipped[reason] += 1
                        result.events.append({"path": str(path), "status": "preserved", "reason": reason})
                    else:
                        result.orphan_expired.append(orphan)
                    continue
                result.skipped["invalid"] += 1
                result.events.append({"path": str(path), "status": "skipped", "reason": "invalid", "detail": str(exc)})
                continue
            if worktree.age < self.policy.clean_age:
                result.skipped["recent"] += 1
                continue
            if any(cwd == path or is_within(cwd, path) for cwd in active):
                result.skipped["active"] += 1
                result.events.append({"path": str(path), "status": "skipped", "reason": "active"})
                continue
            if worktree.locked:
                result.skipped["locked"] += 1
                result.events.append({"path": str(path), "status": "skipped", "reason": "locked"})
                continue
            if worktree.embedded_repository:
                result.skipped["embedded_repository"] += 1
                result.events.append({"path": str(path), "status": "preserved", "reason": "embedded_repository"})
                continue
            if worktree.dirty_submodule:
                result.skipped["dirty_submodule"] += 1
                result.events.append({"path": str(path), "status": "preserved", "reason": "dirty_submodule"})
                continue
            if not worktree.dirty:
                result.clean_expired.append(worktree)
                continue
            targets = cargo_targets(path, self.root)
            if worktree.age >= self.policy.dirty_age:
                result.dirty_expired.append(worktree)
                continue
            if targets:
                result.target_cleanup.append((worktree, targets))
            reason = "dirty_grace"
            result.skipped[reason] += 1
            result.events.append({"path": str(path), "status": "preserved", "reason": reason})
        return result

    def revalidate_identity(self, worktree: Worktree) -> None:
        path = worktree.path.resolve(strict=False)
        if worktree.path.is_symlink() or not is_within(path, self.root):
            raise SafetyHold("changed_after_scan", "worktree failed root containment revalidation")
        try:
            current_stat = worktree.git_file.stat(follow_symlinks=False)
        except OSError as exc:
            raise SafetyHold("changed_after_scan", f"worktree .git pointer changed after audit: {exc}") from exc
        current_identity = (current_stat.st_dev, current_stat.st_ino, current_stat.st_mtime_ns)
        if current_identity != worktree.git_file_identity:
            raise SafetyHold("changed_after_scan", "worktree generation changed after audit")
        if parse_git_file(worktree.git_file) != worktree.admin_dir:
            raise SafetyHold("changed_after_scan", "worktree admin pointer changed after audit")
        reported_admin = Path(
            git(["rev-parse", "--path-format=absolute", "--absolute-git-dir"], cwd=path).stdout.strip()
        ).resolve(strict=False)
        common = Path(
            git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=path).stdout.strip()
        ).resolve(strict=False)
        if reported_admin != worktree.admin_dir or common != worktree.common_dir:
            raise SafetyHold("changed_after_scan", "worktree Git identity changed after audit")
        record = registered_worktrees(worktree.common_dir).get(path)
        if not record or record.get("head") != worktree.head:
            raise SafetyHold("changed_after_scan", "worktree registration changed after audit")
        if record.get("locked") or (worktree.admin_dir / "locked").exists():
            raise SafetyHold("locked", "worktree became locked")

    def ensure_inactive(self, worktree: Worktree | OrphanWorktree) -> None:
        current_active = {path.resolve(strict=False) for path in self.active_paths_provider()}
        if any(cwd == worktree.path or is_within(cwd, worktree.path) for cwd in current_active):
            raise SafetyHold("active", "worktree became active")

    def archive_id(self, worktree: Worktree, kind: str, oid: str) -> str:
        seed = f"{worktree.common_dir}\0{worktree.path}\0{worktree.head}\0{kind}\0{oid}\0{iso(self.now)}"
        return hashlib.sha256(seed.encode()).hexdigest()[:16]

    def create_archive(self, worktree: Worktree, kind: str) -> dict:
        oid = worktree.head
        if kind == "dirty-snapshot":
            oid = self.snapshot_commit(worktree)
        else:
            existing = self.state.existing_for(worktree.common_dir, worktree.path, worktree.head, kind)
            if existing:
                return existing
        archive_id = self.archive_id(worktree, kind, oid)
        stamp = self.now.strftime("%Y%m%dT%H%M%SZ")
        ref = f"{ARCHIVE_PREFIX}/{stamp}/{safe_slug(worktree.path.name)}-{archive_id}"
        git([f"--git-dir={worktree.common_dir}", "update-ref", ref, oid])
        record = {
            "id": archive_id,
            "repository": str(worktree.common_dir),
            "ref": ref,
            "oid": oid,
            "head": worktree.head,
            "kind": kind,
            "original_path": str(worktree.path),
            "created_at": iso(self.now),
            "removed_at": None,
            "expires_at": None,
        }
        self.state.data["archives"].append(record)
        try:
            self.state.save()
        except OSError:
            self.state.data["archives"].remove(record)
            rollback = git([f"--git-dir={worktree.common_dir}", "update-ref", "-d", ref, oid], check=False)
            if rollback.returncode:
                raise PartialMutationError(
                    f"archive state save and ref rollback failed; orphaned ref {ref}: {rollback.stderr.strip()}"
                )
            raise
        return record

    def snapshot_tree(self, worktree: Worktree) -> str:
        if has_embedded_repository(worktree.path):
            raise MutationError("worktree contains an untracked embedded Git repository")
        source_index = Path(
            git(["rev-parse", "--path-format=absolute", "--git-path", "index"], cwd=worktree.path).stdout.strip()
        )
        descriptor, index_path = tempfile.mkstemp(prefix=".janitor-index-", dir=source_index.parent)
        os.close(descriptor)
        snapshot_env = {"GIT_INDEX_FILE": index_path}
        base = [f"--git-dir={worktree.common_dir}", f"--work-tree={worktree.path}"]
        try:
            shutil.copyfile(source_index, index_path)
            git([*base, "update-index", "--no-split-index"], cwd=worktree.path, env=snapshot_env)
            audit_paths = index_audit_paths(worktree.path)
            if audit_paths:
                git(
                    [*base, "update-index", "--no-assume-unchanged", "--no-skip-worktree", "-z", "--stdin"],
                    cwd=worktree.path,
                    env=snapshot_env,
                    input_text="\0".join(audit_paths) + "\0",
                )
            git([*base, "add", "-A", "--", "."], cwd=worktree.path, env=snapshot_env)
            return git([*base, "write-tree"], env=snapshot_env).stdout.strip()
        finally:
            if os.path.exists(index_path):
                os.unlink(index_path)

    def snapshot_commit(self, worktree: Worktree) -> str:
        tree = self.snapshot_tree(worktree)
        timestamp = str(int(self.now.timestamp()))
        commit_env = {
            "GIT_AUTHOR_NAME": "Worktree Janitor",
            "GIT_AUTHOR_EMAIL": "worktree-janitor@localhost",
            "GIT_COMMITTER_NAME": "Worktree Janitor",
            "GIT_COMMITTER_EMAIL": "worktree-janitor@localhost",
            "GIT_AUTHOR_DATE": f"@{timestamp} +0000",
            "GIT_COMMITTER_DATE": f"@{timestamp} +0000",
        }
        return git(
            [f"--git-dir={worktree.common_dir}", "commit-tree", tree, "-p", worktree.head],
            env=commit_env,
            input_text="chore: archive dirty worktree before cleanup\n",
        ).stdout.strip()

    def mark_removed(self, record: dict) -> None:
        for candidate in self.state.data["archives"]:
            if (
                candidate["repository"] == record["repository"]
                and candidate["original_path"] == record["original_path"]
                and candidate.get("removed_at") is None
            ):
                candidate["removed_at"] = iso(self.now)
                candidate["expires_at"] = iso(self.now + self.policy.archive_age)
        self.state.save()

    def remove_worktree(self, worktree: Worktree, *, force: bool, archived_tree: str | None = None) -> None:
        self.revalidate_identity(worktree)
        self.ensure_inactive(worktree)
        if git(["rev-parse", "HEAD"], cwd=worktree.path).stdout.strip() != worktree.head:
            raise MutationError("worktree HEAD changed after audit")
        if worktree_status(worktree.path) != worktree.status:
            raise MutationError("worktree contents changed after audit")
        if force and has_dirty_submodule(worktree.path):
            raise MutationError("worktree contains a dirty submodule")
        if force and has_embedded_repository(worktree.path):
            raise MutationError("worktree contains an untracked embedded Git repository")
        if archived_tree is not None and self.snapshot_tree(worktree) != archived_tree:
            raise MutationError("worktree content changed after its recovery snapshot")
        args = [f"--git-dir={worktree.common_dir}", "worktree", "remove"]
        if force:
            args.append("--force")
        self.destructive_attempted = True
        result = git([*args, str(worktree.path)], check=False)
        if result.returncode == 0:
            return
        if not force and "submodules cannot be moved or removed" in result.stderr:
            if worktree_status(worktree.path) or has_dirty_submodule(worktree.path):
                raise MutationError("worktree changed before submodule removal")
            self.remove_worktree(worktree, force=True)
            return
        raise MutationError(result.stderr.strip() or "git worktree remove failed")

    def reconcile_unremoved_archives(self, apply: bool, report: dict) -> bool:
        pending = [
            record
            for record in self.state.data["archives"]
            if record.get("removed_at") is None and not Path(record["original_path"]).exists()
        ]
        for record in pending:
            report["planned"]["archives_finalized"] += 1
            report["events"].append(
                {"archive_id": record["id"], "action": "finalize_removed_archive", "ref": record["ref"]}
            )
        if not apply or not pending:
            return True
        original = [(record, record.get("removed_at"), record.get("expires_at")) for record in pending]
        for record in pending:
            record["removed_at"] = iso(self.now)
            record["expires_at"] = iso(self.now + self.policy.archive_age)
        try:
            self.state.save()
        except OSError as exc:
            for record, removed_at, expires_at in original:
                record["removed_at"] = removed_at
                record["expires_at"] = expires_at
            report["failures"].append({"operation": "finalize_removed_archives", "error": str(exc)})
            return False
        report["applied"]["archives_finalized"] += len(pending)
        return True

    def expire_archives(self, apply: bool, report: dict, changed_repos: set[Path]) -> bool:
        original = list(self.state.data["archives"])
        kept: list[dict] = []
        changed = False
        for record in self.state.data["archives"]:
            expires = record.get("expires_at")
            if not record.get("removed_at") or not expires or parse_time(expires) > self.now:
                kept.append(record)
                continue
            report["planned"]["archives_expired"] += 1
            report["events"].append({"archive_id": record["id"], "action": "expire_archive", "ref": record["ref"]})
            repository = Path(record["repository"])
            if not repository.is_dir():
                report["skipped"]["archive_repository_missing"] += 1
                kept.append(record)
                continue
            try:
                current_oid = ref_oid(repository, record["ref"])
            except JanitorError as exc:
                report["failures"].append(
                    {"operation": "expire_archive", "archive_id": record["id"], "error": str(exc)}
                )
                kept.append(record)
                continue
            if current_oid is not None and current_oid != record["oid"]:
                report["failures"].append(
                    {
                        "operation": "expire_archive",
                        "archive_id": record["id"],
                        "error": "archive ref no longer points to its recorded object",
                    }
                )
                kept.append(record)
                continue
            if not apply:
                kept.append(record)
                continue
            if current_oid is None:
                report["applied"]["archives_expired"] += 1
                changed = True
                continue
            deletion = git(
                [f"--git-dir={repository}", "update-ref", "-d", record["ref"], record["oid"]],
                check=False,
            )
            if deletion.returncode:
                report["failures"].append({"operation": "expire_archive", "archive_id": record["id"], "error": deletion.stderr.strip()})
                kept.append(record)
                continue
            report["applied"]["archives_expired"] += 1
            changed_repos.add(repository)
            changed = True
        if apply and changed:
            self.state.data["archives"] = kept
            try:
                self.state.save()
            except OSError as exc:
                self.state.data["archives"] = original
                report["failures"].append({"operation": "save_archive_expiry_state", "error": str(exc)})
                return False
        return True

    @staticmethod
    def preserve_after_scan(report: dict, event: dict, hold: SafetyHold) -> None:
        report["skipped"][hold.reason] += 1
        event.update({"status": "preserved", "reason": hold.reason, "detail": str(hold)})

    def finish_report(self, report: dict, free_before: int) -> tuple[dict, int]:
        try:
            free_after = shutil.disk_usage(self.root.parent if not self.root.exists() else self.root).free
        except OSError as exc:
            free_after = free_before
            report["failures"].append({"operation": "measure_disk_after", "error": str(exc)})
        report["disk"] = {"free_before": free_before, "free_after": free_after, "reclaimed": max(0, free_after - free_before)}
        report["planned"] = dict(report["planned"])
        report["applied"] = dict(report["applied"])
        report["skipped"] = dict(report["skipped"])
        if report["failures"]:
            mutated = sum(report["applied"].values()) > 0 or self.destructive_attempted
            report["status"] = "partial" if mutated else "error"
            return report, 2 if mutated else 1
        return report, 0

    def sweep(self, apply: bool) -> tuple[dict, int]:
        free_before = shutil.disk_usage(self.root.parent if not self.root.exists() else self.root).free
        self.state.load()
        scan = self.scan()
        report = {
            "status": "ok",
            "mode": "apply" if apply else "dry-run",
            "root": str(self.root),
            "policy": {
                "clean_hours": self.policy.clean_age.total_seconds() / 3600,
                "dirty_days": self.policy.dirty_age.total_seconds() / 86400,
                "archive_days": self.policy.archive_age.total_seconds() / 86400,
            },
            "scanned": scan.scanned,
            "planned": collections.Counter(),
            "applied": collections.Counter(),
            "skipped": scan.skipped,
            "events": scan.events,
            "failures": [],
        }
        changed_repos: set[Path] = set()
        if not self.reconcile_unremoved_archives(apply, report):
            return self.finish_report(report, free_before)
        if not self.expire_archives(apply, report, changed_repos):
            return self.finish_report(report, free_before)
        for orphan in scan.orphan_expired:
            report["planned"]["orphan_worktrees_removed"] += 1
            event = {"path": str(orphan.path), "action": "remove_orphan_worktree", "recoverable": False}
            report["events"].append(event)
            if not apply:
                continue
            try:
                self.remove_orphan(orphan)
                report["applied"]["orphan_worktrees_removed"] += 1
            except SafetyHold as hold:
                self.preserve_after_scan(report, event, hold)
            except (JanitorError, OSError) as exc:
                report["failures"].append({"path": str(orphan.path), "operation": "remove_orphan_worktree", "error": str(exc)})
                return self.finish_report(report, free_before)
        for worktree in scan.clean_expired:
            report["planned"]["clean_worktrees_removed"] += 1
            event = {"path": str(worktree.path), "action": "remove_clean_worktree"}
            report["events"].append(event)
            # A tiny ref is cheap insurance against branch/ref races between
            # audit and removal, so every clean worktree gets recovery state.
            needs_archive = True
            try:
                existing_archive = self.state.existing_for(
                    worktree.common_dir, worktree.path, worktree.head, "detached-head"
                )
            except JanitorError as exc:
                report["failures"].append(
                    {"path": str(worktree.path), "operation": "inspect_existing_archive", "error": str(exc)}
                )
                continue
            if not existing_archive:
                report["planned"]["archives_created"] += 1
            if not apply:
                continue
            archive = None
            archive_created = False
            try:
                self.revalidate_identity(worktree)
                self.ensure_inactive(worktree)
            except SafetyHold as hold:
                self.preserve_after_scan(report, event, hold)
                continue
            except (JanitorError, OSError) as exc:
                report["failures"].append(
                    {"path": str(worktree.path), "operation": "audit_before_removal", "error": str(exc)}
                )
                return self.finish_report(report, free_before)
            if needs_archive:
                try:
                    archive = self.create_archive(worktree, "detached-head")
                except (JanitorError, OSError) as exc:
                    if isinstance(exc, PartialMutationError):
                        report["applied"]["orphaned_archive_refs"] += 1
                    report["failures"].append(
                        {"path": str(worktree.path), "operation": "create_archive", "error": str(exc)}
                    )
                    continue
                if not existing_archive:
                    archive_created = True
                    report["applied"]["archives_created"] += 1
                event["archive_id"] = archive["id"]
            try:
                # Force is required to remove disposable ignored files. The
                # tracked/untracked status was revalidated immediately above.
                self.remove_worktree(worktree, force=True)
                report["applied"]["clean_worktrees_removed"] += 1
                changed_repos.add(worktree.common_dir)
                if archive:
                    try:
                        self.mark_removed(archive)
                    except OSError as exc:
                        report["failures"].append(
                            {
                                "path": str(worktree.path),
                                "operation": "save_archive_removal_state",
                                "error": str(exc),
                            }
                        )
            except SafetyHold as hold:
                if not archive_created:
                    self.preserve_after_scan(report, event, hold)
                else:
                    report["failures"].append(
                        {"path": str(worktree.path), "operation": "remove_clean_worktree", "error": str(hold)}
                    )
            except (JanitorError, OSError) as exc:
                report["failures"].append({"path": str(worktree.path), "operation": "remove_clean_worktree", "error": str(exc)})
        for worktree in scan.dirty_expired:
            report["planned"]["dirty_worktrees_removed"] += 1
            report["planned"]["archives_created"] += 1
            event = {"path": str(worktree.path), "action": "snapshot_and_remove_dirty_worktree"}
            report["events"].append(event)
            if not apply:
                continue
            try:
                self.revalidate_identity(worktree)
                self.ensure_inactive(worktree)
            except SafetyHold as hold:
                self.preserve_after_scan(report, event, hold)
                continue
            except (JanitorError, OSError) as exc:
                report["failures"].append(
                    {"path": str(worktree.path), "operation": "audit_before_removal", "error": str(exc)}
                )
                return self.finish_report(report, free_before)
            try:
                archive = self.create_archive(worktree, "dirty-snapshot")
            except (JanitorError, OSError) as exc:
                if isinstance(exc, PartialMutationError):
                    report["applied"]["orphaned_archive_refs"] += 1
                report["failures"].append(
                    {"path": str(worktree.path), "operation": "create_archive", "error": str(exc)}
                )
                continue
            event["archive_id"] = archive["id"]
            report["applied"]["archives_created"] += 1
            try:
                archived_tree = git(
                    [f"--git-dir={worktree.common_dir}", "rev-parse", f"{archive['oid']}^{{tree}}"]
                ).stdout.strip()
                self.remove_worktree(worktree, force=True, archived_tree=archived_tree)
                report["applied"]["dirty_worktrees_removed"] += 1
                changed_repos.add(worktree.common_dir)
                try:
                    self.mark_removed(archive)
                except OSError as exc:
                    report["failures"].append(
                        {
                            "path": str(worktree.path),
                            "operation": "save_archive_removal_state",
                            "error": str(exc),
                        }
                    )
            except (JanitorError, OSError) as exc:
                report["failures"].append({"path": str(worktree.path), "operation": "remove_dirty_worktree", "error": str(exc)})
        for worktree, targets in scan.target_cleanup:
            for target in targets:
                report["planned"]["target_directories_removed"] += 1
                event = {"path": str(target), "action": "remove_cargo_target", "worktree": str(worktree.path)}
                report["events"].append(event)
                if not apply:
                    continue
                try:
                    self.revalidate_identity(worktree)
                    self.ensure_inactive(worktree)
                except SafetyHold as hold:
                    self.preserve_after_scan(report, event, hold)
                    continue
                except (OSError, JanitorError) as exc:
                    report["failures"].append(
                        {"path": str(target), "operation": "audit_before_target_removal", "error": str(exc)}
                    )
                    return self.finish_report(report, free_before)
                try:
                    resolved_target = target.resolve(strict=False)
                    if (
                        resolved_target != target
                        or not is_within(resolved_target, worktree.path)
                        or not cargo_target(target)
                    ):
                        raise SafetyHold("changed_after_scan", "target failed boundary or Cargo ownership revalidation")
                    self.destructive_attempted = True
                    shutil.rmtree(target)
                    report["applied"]["target_directories_removed"] += 1
                except SafetyHold as hold:
                    self.preserve_after_scan(report, event, hold)
                except (OSError, JanitorError) as exc:
                    report["failures"].append({"path": str(target), "operation": "remove_cargo_target", "error": str(exc)})
        if apply:
            for repository in sorted(changed_repos, key=str):
                maintenance = git([f"--git-dir={repository}", "maintenance", "run", "--auto"], check=False)
                if maintenance.returncode:
                    report["failures"].append({"path": str(repository), "operation": "git_maintenance", "error": maintenance.stderr.strip()})
                else:
                    report["applied"]["maintenance_runs"] += 1
        return self.finish_report(report, free_before)

    def archives(self) -> dict:
        self.state.load()
        archives = []
        for record in self.state.data["archives"]:
            item = dict(record)
            item["available"] = ref_matches(Path(record["repository"]), record["ref"], record["oid"])
            archives.append(item)
        return {"status": "ok", "root": str(self.root), "archives": archives}

    def restore(self, archive_id: str, destination: Path) -> tuple[dict, int]:
        self.state.load()
        record = self.state.find(archive_id)
        if not record:
            raise JanitorError(f"unknown archive id: {archive_id}")
        if self.configured_root.is_symlink():
            raise JanitorError(f"worktree root must not be a symlink: {self.configured_root}")
        destination = destination.expanduser().resolve(strict=False)
        if not is_within(destination, self.root):
            raise JanitorError(f"restore path must be inside {self.root}")
        if destination.exists() and any(destination.iterdir()):
            raise JanitorError(f"restore path is not empty: {destination}")
        repository = Path(record["repository"])
        if not ref_matches(repository, record["ref"], record["oid"]):
            raise JanitorError(f"archive ref is unavailable or changed: {record['ref']}")
        self.root.mkdir(parents=True, exist_ok=True)
        result = git(
            [f"--git-dir={repository}", "worktree", "add", "--detach", str(destination), record["ref"]],
            check=False,
        )
        if result.returncode:
            return {"status": "partial", "archive_id": archive_id, "path": str(destination), "error": result.stderr.strip()}, 2
        return {"status": "ok", "archive_id": archive_id, "path": str(destination), "ref": record["ref"], "detached": True}, 0


def policy_from(args: argparse.Namespace) -> Policy:
    return Policy(
        clean_age=dt.timedelta(hours=args.clean_hours),
        dirty_age=dt.timedelta(days=args.dirty_days),
        archive_age=dt.timedelta(days=args.archive_days),
    )


def finite_nonnegative(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("must be finite and non-negative")
    return parsed


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliError(message)


def parser() -> argparse.ArgumentParser:
    result = JsonArgumentParser(description=__doc__)
    result.add_argument("--clean-hours", type=finite_nonnegative, default=CLEAN_HOURS)
    result.add_argument("--dirty-days", type=finite_nonnegative, default=DIRTY_DAYS)
    result.add_argument("--archive-days", type=finite_nonnegative, default=ARCHIVE_DAYS)
    result.add_argument("--verbose", action="store_true", help="write concise progress to stderr")
    commands = result.add_subparsers(dest="command", required=True)
    sweep = commands.add_parser("sweep", help="audit or apply the cleanup policy")
    sweep.add_argument("--apply", action="store_true")
    commands.add_parser("archives", help="list recoverable archives")
    restore = commands.add_parser("restore", help="restore an archive as a detached worktree")
    restore.add_argument("archive_id")
    restore.add_argument("--path", type=Path, required=True)
    return result


def emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def emit_verbose(payload: dict) -> None:
    print(
        f"worktree-janitor: {payload.get('status')} "
        f"planned={sum(payload.get('planned', {}).values())} "
        f"applied={sum(payload.get('applied', {}).values())} "
        f"failures={len(payload.get('failures', []))}",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    root = Path.home() / ".worktrees"
    cache_dir = Path.home() / ".cache" / "worktree-janitor"
    try:
        args = parser().parse_args(argv)
        policy = policy_from(args)
    except (JanitorError, ValueError, OverflowError) as exc:
        emit({"status": "error", "error": str(exc), "root": str(root)})
        return 1
    janitor = Janitor(root, cache_dir, policy)
    lock = ProcessLock(janitor.cache_dir)
    try:
        acquired = lock.acquire()
    except OSError as exc:
        emit({"status": "error", "error": f"cannot acquire lock: {exc}", "root": str(janitor.root)})
        return 1
    if not acquired:
        emit({"status": "already_running", "root": str(janitor.root)})
        return 0
    try:
        if args.command == "sweep":
            payload, exit_code = janitor.sweep(args.apply)
        elif args.command == "archives":
            payload, exit_code = janitor.archives(), 0
        else:
            payload, exit_code = janitor.restore(args.archive_id, args.path)
        if args.verbose:
            emit_verbose(payload)
        emit(payload)
        return exit_code
    except (JanitorError, OSError, ValueError) as exc:
        emit({"status": "error", "error": str(exc), "root": str(janitor.root)})
        return 1
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
