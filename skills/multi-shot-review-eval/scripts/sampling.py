from __future__ import annotations

import random
from pathlib import Path
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from diff_size import target_commit
from eval_errors import EvalError
from findings import Finding, FindingClass, load_findings
from git_objects import GitGaveNoAnswer, file_version, git, resolved_commit
from review_target import TargetKind
from session_store import Session


# P0–P2 findings carry the review value the primary metric counts, so audits favor them over P3.
SEVERITY_WEIGHTS = {"P0": 3.0, "P1": 3.0, "P2": 2.0, "P3": 1.0}


class Judgeability(StrEnum):
    EXACT = "exact"
    APPROXIMATE = "approximate"
    UNJUDGEABLE = "unjudgeable"


@dataclass(frozen=True)
class SampleQuota:
    kept: int
    rejected: int

    def __post_init__(self) -> None:
        if self.kept < 0 or self.rejected < 0:
            raise EvalError(f"sample quotas must be nonnegative, got kept={self.kept} rejected={self.rejected}")


@dataclass(frozen=True)
class SampleEntry:
    arm: str
    session_id: str
    session_dir: str
    slice_name: str
    finding_id: str
    finding_class: FindingClass
    severity: str
    title: str
    location: str
    content: str
    report_path: str | None
    judgeability: Judgeability
    # The directory for `git -C`. For exact entries it can be the Git directory of a deleted worktree.
    repository: str | None
    commit: str | None
    # The file of the finding, from the top of the repository. Sessions from before the snapshots have none.
    path: str | None
    # Set when the staging area held another version of the file than the working copy.
    staged_commit: str | None
    # Set when the reviewed change deleted the file from the working copy or from the staging area:
    # the commit that still has it.
    base_commit: str | None
    # Set when the snapshot did not store the working copy: the `commit` without the file then does
    # not show a deletion.
    working_copy_left_out: bool = False


@dataclass(frozen=True)
class ReviewedCode:
    """Where an analyst reads the code of a finding."""

    judgeability: Judgeability
    repository: Path | None = None
    commit: str | None = None
    path: str | None = None
    staged_commit: str | None = None
    base_commit: str | None = None
    working_copy_left_out: bool = False


def reviewed_code(session: Session, finding: Finding) -> ReviewedCode:
    if finding.snapshot is None:
        return _target_code(session, finding)
    directories, staged = _snapshot_repositories(session, finding.snapshot)
    for repository in directories:
        try:
            code = _snapshot_code(repository, finding, staged)
        except GitGaveNoAnswer:
            # A file that Git could not look for is not a file that the reviewed change deleted.
            continue
        if code is not None:
            return code
    # A bare repository has commits and no current files.
    if session.root is None or not session.root.is_dir() or _is_bare_repository(session.root):
        return ReviewedCode(Judgeability.UNJUDGEABLE)
    # A later change can delete or replace the file, and then the analyst has no code to read. A
    # finding without a path is on a file outside the session root, which the analyst finds by its
    # location.
    if finding.path is not None and not (session.root / finding.path).is_file():
        return ReviewedCode(Judgeability.UNJUDGEABLE)
    # The current files of a commit review are the reviewed code only while the target still resolves.
    if session.target.kind in (TargetKind.BASE, TargetKind.UNCOMMITTED) or target_commit(session):
        return ReviewedCode(Judgeability.APPROXIMATE, session.root, None, finding.path)
    return ReviewedCode(Judgeability.UNJUDGEABLE)


def _target_code(session: Session, finding: Finding) -> ReviewedCode:
    """The rule for runs without a snapshot: a commit target that resolves is the reviewed code.

    These runs are older than the snapshots, or Git could not take one. The rule does not look
    for the file, so evaluations of older sessions keep their results.
    """

    if session.root is None or not session.root.is_dir():
        return ReviewedCode(Judgeability.UNJUDGEABLE)
    commit = target_commit(session)
    if commit is not None:
        return ReviewedCode(Judgeability.EXACT, session.root, commit, finding.path)
    if session.target.kind in (TargetKind.BASE, TargetKind.UNCOMMITTED):
        return ReviewedCode(Judgeability.APPROXIMATE, session.root, None, finding.path)
    return ReviewedCode(Judgeability.UNJUDGEABLE)


def _is_bare_repository(directory: Path) -> bool:
    return (git(directory, "rev-parse", "--is-bare-repository") or "").strip() == "true"


def _snapshot_repositories(session: Session, snapshot: str) -> tuple[list[Path], str | None]:
    """The directories that can hold the snapshot, and the staging-area commit of its record.

    The target of the session is not a candidate: a name such as HEAD can resolve to a later
    commit. A repository that moved keeps its commits, so the session root comes last.
    """

    records = session.state.get("snapshots")
    recorded = [
        record
        for record in (records if isinstance(records, list) else [])
        if isinstance(record, dict) and record.get("commit") == snapshot and isinstance(record.get("git_dir"), str)
    ]

    staged = next(
        (record["staged"] for record in recorded if isinstance(record.get("staged"), str) and record["staged"]), None
    )
    directories = [Path(record["git_dir"]) for record in recorded] + ([session.root] if session.root else [])
    return [directory for directory in directories if directory.is_dir()], staged


def _snapshot_code(repository: Path, finding: Finding, staged: str | None) -> ReviewedCode | None:
    """None when the repository cannot show the file of the finding as the reviewers read it.

    A snapshot leaves out submodule content, nested repositories, and large untracked files, so
    the commit alone is not sufficient: a reviewed version must have the file, or the reviewed
    change deleted it. The run names the files that the snapshot left out.
    """

    commit = resolved_commit(repository, finding.snapshot)
    if commit is None or finding.path is None:
        return None
    # A shallow clone can be without the commit that the change starts from.
    base = resolved_commit(repository, finding.snapshot_base) if finding.snapshot_base else None
    working_copy = file_version(repository, commit, finding.path)
    # A file that the snapshot left out is on the disk, so the reviewed change did not delete it.
    # Its staged version is still reviewed code.
    left_out = working_copy is None and (finding.snapshot_omitted is None or finding.path in finding.snapshot_omitted)
    try:
        staged_change, in_staging_area = _staged_change(repository, staged, finding.path)
    except GitGaveNoAnswer:
        if working_copy is None:
            raise
        staged_change, in_staging_area = False, None
    # `git rm --cached` deletes the file from the staging area and keeps it on the disk.
    deleted = not left_out and (working_copy is None or (staged_change and in_staging_area is None))
    before_the_change = None
    if deleted and base:
        try:
            before_the_change = file_version(repository, base, finding.path)
        except GitGaveNoAnswer:
            # A partial clone can be without the tree of the base. The versions that Git did
            # find are still the reviewed code.
            if not (working_copy or in_staging_area):
                raise
    if not (working_copy or in_staging_area or before_the_change):
        return None
    return ReviewedCode(
        Judgeability.EXACT,
        repository,
        commit,
        finding.path,
        staged_commit=staged if staged_change and in_staging_area != working_copy else None,
        base_commit=base if deleted and before_the_change else None,
        working_copy_left_out=left_out,
    )


def _staged_change(repository: Path, staged: str | None, path: str) -> tuple[bool, str | None]:
    """Whether the staging area changed the file, and the version that it then has.

    The staging-area commit holds each file of the repository. Reviewers read the staging area as
    `git diff --cached`, which shows only the files with a staged change. For each other file, the
    commit has the `HEAD` version, which the working copy can have fixed: it is not a reviewed
    version. A file with a merge conflict has its `HEAD` side there, so it has no staged change.
    """

    # A record can name a commit that the repository does not have.
    if staged is None or resolved_commit(repository, staged) is None:
        return False, None
    head = _recorded_parent(repository, staged)
    in_staging_area = file_version(repository, staged, path)
    at_head = file_version(repository, head, path) if head else None
    changed = in_staging_area != at_head
    return changed, in_staging_area if changed else None


def _recorded_parent(repository: Path, staged: str) -> str | None:
    """The `HEAD` commit of the staging area, or None for a repository that had no commit.

    The snapshot gives the staging-area commit one parent, `HEAD`. The commit object names it: a
    shallow repository can have the parent and still refuse `<commit>^` across its boundary.
    """

    content = git(repository, "cat-file", "commit", staged)
    if content is None:
        raise GitGaveNoAnswer(f"git cat-file commit {staged} in {repository}")
    header = content.partition("\n\n")[0].splitlines()
    parent = next((line.split(" ", 1)[1] for line in header if line.startswith("parent ")), None)
    if parent is None:
        return None
    head = resolved_commit(repository, parent)
    if head is None:
        # Without the parent, a version of the staging area cannot show if it is a staged change.
        raise GitGaveNoAnswer(f"parent {parent} of {staged} is not in {repository}")
    return head


def draw_sample(
    arms: Mapping[str, Sequence[Session]],
    *,
    quota: SampleQuota,
    seed: int,
    exclude: Collection[tuple[str, str]],
    code_of: Callable[[Session, Finding], ReviewedCode] = reviewed_code,
) -> list[SampleEntry]:
    entries = []
    for arm, sessions in arms.items():
        by_id = {session.id: session for session in sessions}
        # Sessions with unreadable runs are absent from the finding metrics, so the audit must not see them either.
        loaded = (load_findings(session) for session in sessions)
        pool = [
            finding
            for findings in loaded
            if findings.complete
            for finding in findings.findings
            if (finding.session_id, finding.finding_id) not in exclude
        ]
        for finding_class, size in ((FindingClass.KEPT, quota.kept), (FindingClass.REJECTED, quota.rejected)):
            candidates = [finding for finding in pool if finding.finding_class == finding_class]
            rng = random.Random(f"{seed}:{arm}:{finding_class}")
            for finding in _weighted_without_replacement(candidates, size, rng):
                session = by_id[finding.session_id]
                entries.append(_entry(arm, session, finding, code_of(session, finding)))
    return entries


def _weighted_without_replacement(candidates: list[Finding], size: int, rng: random.Random) -> list[Finding]:
    # Efraimidis-Spirakis: the top keys u^(1/w) form a weighted sample without replacement.
    keyed = sorted(
        candidates,
        key=lambda finding: rng.random() ** (1 / SEVERITY_WEIGHTS.get(finding.severity, 1.0)),
        reverse=True,
    )
    return keyed[:size]


def _entry(arm: str, session: Session, finding: Finding, code: ReviewedCode) -> SampleEntry:
    return SampleEntry(
        arm=arm,
        session_id=session.id,
        session_dir=str(session.directory),
        slice_name=finding.slice_name,
        finding_id=finding.finding_id,
        finding_class=finding.finding_class,
        severity=finding.severity,
        title=finding.title,
        location=finding.location,
        content=finding.content,
        report_path=_current_report_path(session, finding.report_path),
        judgeability=code.judgeability,
        repository=None if code.repository is None else str(code.repository),
        commit=code.commit,
        path=code.path,
        staged_commit=code.staged_commit,
        base_commit=code.base_commit,
        working_copy_left_out=code.working_copy_left_out,
    )


def _current_report_path(session: Session, recorded: str | None) -> str | None:
    """Runs record the report path from the worktree that ran them, which may be gone by evaluation time."""
    if recorded is None:
        return None
    if Path(recorded).is_file():
        return recorded
    moved = session.directory / Path(recorded).name
    return str(moved) if moved.is_file() else None
