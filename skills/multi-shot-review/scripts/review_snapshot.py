from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Reviewers read live Git. After the review, the worktree, the branch, or the uncommitted changes
# go away, and then an evaluation cannot judge a finding against the code that the reviewer read.
# A commit under a private ref namespace keeps that code in the shared object store.

REF_NAMESPACE = "refs/multi-shot-review"
SESSION_NAME_RE = re.compile(r"^(\d{8}-\d{4})-[0-9a-f]+$")
# Evaluations run days after the reviews. A longer retention keeps dead objects out of `git gc`.
RETENTION = timedelta(days=30)
# An untracked build product or data file that is not ignored would stay in the object store for
# the full retention. Reviewers do not read files of this size.
UNTRACKED_FILE_LIMIT_BYTES = 5 * 1024 * 1024
# A dependency directory that is not ignored has thousands of small files.
UNTRACKED_TOTAL_LIMIT_BYTES = 100 * 1024 * 1024
# The state keeps the names for each run, and an evaluation needs them only for its sampled findings.
OMITTED_LIMIT = 200
# A slow repository must not hold the reviewers; the wave then runs without a snapshot.
GIT_TIMEOUT_SECONDS = 120
# commit-tree fails without an identity, and the author did not write this commit.
COMMIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "multi-shot-review",
    "GIT_AUTHOR_EMAIL": "multi-shot-review@localhost",
    "GIT_COMMITTER_NAME": "multi-shot-review",
    "GIT_COMMITTER_EMAIL": "multi-shot-review@localhost",
}


class SnapshotError(Exception):
    """Git could not store the reviewed code. The cause goes to the session history, not to a traceback."""


class _GitRefused(SnapshotError):
    """Git exited with an error."""

    def __init__(self, message: str, stderr: str) -> None:
        super().__init__(message)
        self.stderr = stderr


class _GitDidNotStart(SnapshotError):
    """The Git program or the directory is not there."""


# The refusals that are an answer to a question. Each other refusal is a failure: a repository
# with dubious ownership is not "no repository".
NOT_A_REPOSITORY = "not a git repository"
NO_WORKING_TREE = "must be run in a work tree"
NO_SUCH_COMMIT = "Needed a single revision"


@dataclass(frozen=True)
class Snapshot:
    commit: str
    # The shared repository, not the worktree: the worktree can be gone when an evaluation reads the commit.
    git_dir: str
    ref: str
    # Reviewers read the working copy and also `git diff --cached`. The commit holds the working
    # copy, so a staging area with other content has its own commit.
    staged: str | None = None
    # The commit that the reviewed change starts from. A file that the change deleted is in this
    # commit, and it is not in `commit`.
    base: str | None = None
    # The files that are on the disk, that an earlier commit has, and that `commit` does not have.
    # Without the names, each of them reads as a file that the change deleted. None when the list
    # would be longer than OMITTED_LIMIT.
    omitted: tuple[str, ...] | None = ()


def pin_reviewed_code(
    root: Path, target: dict[str, str], *, session_name: str, now: datetime | None = None
) -> Snapshot | None:
    """None means that Git has nothing to store: no repository, or no working tree for a working-tree target.

    SnapshotError means that Git failed. A review must run in each of these cases.
    """

    common_dir = _outside_git_is_none(root, "rev-parse", "--git-common-dir")
    if common_dir is None:
        return None
    session_time = _session_time(session_name)
    if session_time is None:
        raise SnapshotError(f"review directory name {session_name!r} has no session time, so its ref cannot expire")
    if session_time < (now or datetime.now(timezone.utc)) - RETENTION:
        raise SnapshotError(
            f"session {session_name} is older than {RETENTION.days} days, so the wave would delete its new ref"
        )
    try:
        staged = None
        omitted: tuple[str, ...] | None = ()
        if target["kind"] == "commit":
            # A commit target is immutable; later edits of the working tree are not part of the review.
            commit = _resolve(root, target["value"])
            if commit is None:
                raise SnapshotError(f"commit target {target['value']!r} does not resolve")
            base = _resolve(root, f"{commit}^")
        else:
            # The session root can be a subdirectory, and the reviewers read the full working tree.
            top = _probe(root, "rev-parse", "--show-toplevel", miss=NO_WORKING_TREE)
            if top is None:
                return None
            working_tree = Path(_path(top))
            commit, staged = _working_tree_commit(working_tree, session_name)
            base = _change_start(root, target)
            omitted = _left_out_of_the_snapshot(working_tree, commit, [earlier for earlier in (base, staged) if earlier])
        # The commit names the ref, so two runners of one session cannot move the ref of the other.
        ref = f"{REF_NAMESPACE}/{session_name}/{commit}"
        _run(root, "update-ref", ref, commit)
        git_dir = str((root / _path(common_dir)).resolve())
        return Snapshot(commit=commit, git_dir=git_dir, ref=ref, staged=staged, base=base, omitted=omitted)
    except OSError as error:
        raise SnapshotError(f"snapshot files: {error}") from error


def prune_expired_snapshots(root: Path, *, now: datetime | None = None) -> list[str]:
    """Delete the snapshot refs of sessions older than the retention; Git then collects their objects."""

    listed = _outside_git_is_none(root, "for-each-ref", "--format=%(refname)", REF_NAMESPACE)
    if not listed:
        return []
    cutoff = (now or datetime.now(timezone.utc)) - RETENTION
    expired = [ref for ref in _text(listed).splitlines() if (_ref_session_time(ref) or cutoff) < cutoff]
    if not expired:
        return []
    _run(root, "update-ref", "--stdin", stdin="".join(f"delete {ref}\n" for ref in expired).encode())
    return expired


def _ref_session_time(ref: str) -> datetime | None:
    """Refs that this tool did not name have no session time, so they never expire here."""

    parts = ref.split("/")
    return _session_time(parts[2]) if len(parts) == 4 else None


def _session_time(session_name: str) -> datetime | None:
    match = SESSION_NAME_RE.fullmatch(session_name)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d-%H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _change_start(root: Path, target: dict[str, str]) -> str | None:
    """The commit before the changes of a working-tree target. It is an ancestor of the snapshot
    commit, so the ref of the snapshot keeps it.

    None when the change has no start: a repository without a commit, or a base without a shared
    history.
    """

    if target["kind"] != "base":
        return _resolve(root, "HEAD")
    try:
        return _text(_run(root, "merge-base", target["value"], "HEAD")) or None
    except _GitRefused:
        # Git has no message for "no shared history", so the refusals cannot be told apart. A
        # base that does not resolve stops the reviewers, not the snapshot.
        return None


def _left_out_of_the_snapshot(top: Path, commit: str, earlier_commits: list[str]) -> tuple[str, ...] | None:
    """The files of the earlier commits that are on the disk and not in the snapshot commit.

    The snapshot does not hold an ignored file, a nested repository, the content of a submodule,
    a large untracked file, or a file that Git cannot store. Git shows each of them as a deleted
    file, and only the disk shows that it is still there.
    """

    names: set[bytes] = set()
    for earlier in earlier_commits:
        deleted = _run(top, "diff-tree", "-r", "-z", "--name-only", "--no-renames", "--diff-filter=D", earlier, commit)
        for name in filter(None, deleted.split(b"\0")):
            on_the_disk = _on_the_disk(top, name)
            # A directory that replaced the file did delete the file.
            if os.path.lexists(on_the_disk) and not _is_directory(on_the_disk):
                names.add(name)
    return tuple(sorted(map(os.fsdecode, names))) if len(names) <= OMITTED_LIMIT else None


def _working_tree_commit(top: Path, session_name: str) -> tuple[str, str | None]:
    """The commit of the tracked, changed, and untracked files, and the commit of the staging area.

    A scratch index builds them, so the index and the working tree of the author stay as they are.
    """

    head = _resolve(top, "HEAD")
    # A repository without a commit compares against the empty tree.
    head_tree = _text(_run(top, "rev-parse", "HEAD^{tree}") if head else _run(top, "mktree", stdin=b""))
    with tempfile.TemporaryDirectory(prefix="multi-shot-review-snapshot-") as scratch:
        index = Path(scratch) / "index"
        # A copy keeps the stat cache, so Git hashes only the changed files. The copy also keeps
        # the time of the index: Git reads the content of each file that is not older than its
        # index, because an edit after `git add` can keep the size and the time of the file.
        live_index = top / _path(_run(top, "rev-parse", "--git-path", "index"))
        if live_index.is_file():
            shutil.copy2(live_index, index)
        env = {"GIT_INDEX_FILE": str(index)}
        conflicted = _conflicts_as_head_version(top, env, head)
        staged_tree = _text(_run(top, "write-tree", env=env))
        hidden = _hidden_from_git_add(top, env)
        _drop_tracked_files_that_git_cannot_store(top, env)
        # --sparse: Git refuses to update a file that the author restored outside the sparse
        # checkout, and the reviewers read that file.
        _run(top, "add", "--sparse", "--update", env=env)
        refresh = _files_to_store(top, hidden + conflicted) + _reviewable_untracked(top, env, scratch)
        if refresh:
            # update-index, not `git add`: it accepts paths outside a sparse checkout, and --remove
            # accepts a file that the author deleted after the listing. --replace: a new file can
            # have the name of a directory that the sparse checkout left out.
            _run(
                top, "update-index", "--add", "--remove", "--replace", "-z", "--stdin",
                env=env, stdin=b"".join(name + b"\0" for name in refresh),
            )
        tree = _text(_run(top, "write-tree", env=env))
    parents = [head] if head else []
    staged = None
    if staged_tree not in (head_tree, tree):
        staged = _commit_tree(top, staged_tree, f"multi-shot-review staging area {session_name}", parents)
    # A clean working tree is the HEAD commit; a second commit of the same tree adds nothing.
    if head is not None and staged is None and tree == head_tree:
        return head, None
    snapshot_parents = parents + ([staged] if staged else [])
    return _commit_tree(top, tree, f"multi-shot-review snapshot {session_name}", snapshot_parents), staged


def _conflicts_as_head_version(top: Path, env: dict[str, str], head: str | None) -> list[bytes]:
    """The files with a merge conflict; the scratch index gets the `HEAD` version of each.

    An index with a conflict has no tree, and the staged content of the other files must not go
    with it. `git diff --cached` shows no content for a file with a conflict, so the file has no
    staged change: its staged version is the `HEAD` version. The "ours" side of the conflict is
    not always that version: `git stash apply` puts the earlier staging area there.
    """

    conflicted: list[bytes] = []
    removals = []
    for record in filter(None, _run(top, "ls-files", "--unmerged", "-z", env=env).split(b"\0")):
        details, _, name = record.partition(b"\t")
        # Git lists the stages of one file together. Mode 0 removes all of them.
        if name not in conflicted[-1:]:
            conflicted.append(name)
            removals.append(b"0 " + b"0" * len(details.split(b" ")[1]) + b"\t" + name + b"\0")
    if not conflicted:
        return []
    # The full listing and not one path for each file: Git reads a path argument as a pattern.
    committed = _run(top, "ls-tree", "-r", "-z", "--full-tree", head).split(b"\0") if head else []
    head_versions = [record + b"\0" for record in committed if record.partition(b"\t")[2] in set(conflicted)]
    _run(top, "update-index", "-z", "--index-info", env=env, stdin=b"".join(removals + head_versions))
    return conflicted


def _hidden_from_git_add(top: Path, env: dict[str, str]) -> list[bytes]:
    """The tracked files that Git does not compare with the index; the scratch index drops their flags.

    Reviewers read the file on the disk. With the `assume-unchanged` or the `skip-worktree` flag,
    `git add` keeps the committed content, and the snapshot would hold code that no reviewer read.
    """

    assume_unchanged, skip_worktree = [], []
    for record in filter(None, _run(top, "ls-files", "-v", "-z", env=env).split(b"\0")):
        tag, name = record[:1], record[2:]
        on_disk = os.path.lexists(_on_the_disk(top, name))
        # A sparse checkout has no file for a skip-worktree entry, and the entry must stay.
        if tag in (b"S", b"s") and on_disk:
            skip_worktree.append(name)
        if tag == b"h" or (tag == b"s" and on_disk):
            assume_unchanged.append(name)
    # One update-index call changes one flag.
    for flag, names in (("--no-assume-unchanged", assume_unchanged), ("--no-skip-worktree", skip_worktree)):
        if names:
            _run(top, "update-index", flag, "-z", "--stdin", env=env, stdin=b"".join(name + b"\0" for name in names))
    return sorted({*assume_unchanged, *skip_worktree})


def _drop_tracked_files_that_git_cannot_store(top: Path, env: dict[str, str]) -> None:
    """One file that Git refuses must not cost the snapshot of the other files.

    `git add` stops at the first file that it cannot read. No reviewer can read that file, so the
    snapshot is correct without it.
    """

    changed = _run(top, "diff-files", "--name-only", "-z", "--ignore-submodules", env=env).split(b"\0")
    refused = [name for name in filter(None, changed) if _git_cannot_store(_on_the_disk(top, name))]
    if refused:
        _run(
            top, "update-index", "--force-remove", "-z", "--stdin",
            env=env, stdin=b"".join(name + b"\0" for name in refused),
        )


def _files_to_store(top: Path, names: list[bytes]) -> list[bytes]:
    """The names that `git update-index` accepts: a file that Git can store, or a deleted file.

    A directory can replace a tracked file, and a symbolic link can replace the directory of a
    tracked file. `git add --update` then removes the file from the scratch index, and Git lists
    the directory or the link as untracked. Git refuses each name below a link, and one refusal
    would cost the snapshot of the whole wave.
    """

    paths = ((name, _on_the_disk(top, name)) for name in names)
    return [
        name
        for name, path in paths
        if not _below_a_symbolic_link(top, name) and not _is_directory(path) and not _git_cannot_store(path)
    ]


def _below_a_symbolic_link(top: Path, name: bytes) -> bool:
    directories = name.split(b"/")[:-1]
    return any(
        os.path.islink(_on_the_disk(top, b"/".join(directories[: depth + 1]))) for depth in range(len(directories))
    )


def _is_directory(path: bytes) -> bool:
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def _git_cannot_store(path: bytes) -> bool:
    """True for a file without read permission, and for a pipe, a socket, or a device."""

    try:
        mode = os.lstat(path).st_mode
    except OSError:
        return False
    if stat.S_ISREG(mode):
        return not os.access(path, os.R_OK)
    return not (stat.S_ISLNK(mode) or stat.S_ISDIR(mode))


def _on_the_disk(top: Path, name: bytes) -> bytes:
    return os.path.join(os.fsencode(top), name)


def _reviewable_untracked(top: Path, env: dict[str, str], scratch: str) -> list[bytes]:
    listed = _run(top, "ls-files", "--others", "--exclude-standard", "-z", env=env)
    # TMPDIR can be a directory of the working tree. The scratch index is then an untracked file
    # that is gone before a reviewer starts.
    scratch_files = os.fsencode(os.path.realpath(scratch)) + b"/"
    sized = []
    for name in filter(None, listed.split(b"\0")):
        # Git lists a nested repository as a directory. It has its own history, and the index
        # cannot hold one that has no commit.
        if name.endswith(b"/"):
            continue
        on_the_disk = _on_the_disk(top, name)
        if on_the_disk.startswith(scratch_files) or _git_cannot_store(on_the_disk):
            continue
        try:
            size = os.lstat(on_the_disk).st_size
        except OSError:
            continue
        if size <= UNTRACKED_FILE_LIMIT_BYTES:
            sized.append((size, name))
    # Source files are small, so the smallest files get the budget first.
    paths = []
    budget = UNTRACKED_TOTAL_LIMIT_BYTES
    for size, name in sorted(sized):
        if size > budget:
            break
        budget -= size
        paths.append(name)
    return paths


def _commit_tree(top: Path, tree: str, message: str, parents: list[str]) -> str:
    # --no-gpg-sign: commit.gpgSign would open a passphrase prompt in a process without a terminal.
    parent_args = [arg for parent in parents for arg in ("-p", parent)]
    return _text(_run(top, "commit-tree", tree, *parent_args, "--no-gpg-sign", "-m", message, env=COMMIT_IDENTITY))


def _text(output: bytes) -> str:
    return output.decode("utf-8", "replace").strip()


def _path(output: bytes) -> str:
    # The name of a directory can end with a space, and it can have bytes that are not UTF-8.
    return os.fsdecode(output.removesuffix(b"\n"))


def _outside_git_is_none(root: Path, *args: str) -> bytes | None:
    """None when there is nothing for Git to store: no repository, no directory, or no Git program."""

    try:
        return _probe(root, *args, miss=NOT_A_REPOSITORY)
    except _GitDidNotStart:
        return None


def _resolve(cwd: Path, name: str) -> str | None:
    commit = _probe(cwd, "rev-parse", "--verify", f"{name}^{{commit}}", miss=NO_SUCH_COMMIT)
    return _text(commit) if commit else None


def _probe(cwd: Path, *args: str, miss: str) -> bytes | None:
    """None when Git refuses with `miss` or has no answer. Each other failure goes to the caller."""

    try:
        return _run(cwd, *args).rstrip(b"\n") or None
    except _GitRefused as refusal:
        if miss in refusal.stderr:
            return None
        raise


def _run(cwd: Path, *args: str, env: dict[str, str] | None = None, stdin: bytes | None = None) -> bytes:
    try:
        proc = subprocess.run(
            # A command that writes a split index deletes the old shared index files of the
            # repository, and the index of the author can still use one of them.
            ["git", "-c", "core.splitIndex=false", *args],
            cwd=cwd,
            # The C locale keeps the messages that _probe reads in English.
            env={**os.environ, **(env or {}), "LC_ALL": "C"},
            input=stdin,
            # Without input, Git must not wait on the terminal of the runner.
            stdin=subprocess.DEVNULL if stdin is None else None,
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise SnapshotError(f"git {args[0]} did not finish in {GIT_TIMEOUT_SECONDS} seconds") from error
    except OSError as error:
        raise _GitDidNotStart(f"git {args[0]} did not start: {error}") from error
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        raise _GitRefused(f"git {args[0]} failed: {stderr[-500:] or f'exit code {proc.returncode}'}", stderr)
    return proc.stdout
