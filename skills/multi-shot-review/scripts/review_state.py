#!/usr/bin/env python3
"""State management and runner helpers for multi-shot reviews."""

from __future__ import annotations

import argparse
import copy
import fcntl
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from harnesses import HarnessError, ResolvedProfile, get_harness, resolve_profile
from review_result import (
    RESULT_SCHEMA_VERSION,
    RESULT_SCHEMA_PATH,
    ReviewResultError,
    assign_finding_ids,
    parse_review_result,
    render_review_failure_markdown,
    render_review_markdown,
    validate_stored_finding,
)


SCHEMA_VERSION = 3
MAX_ACTIVE_SLICES = 10
HARNESS_SOURCES = frozenset(
    {
        "slice-override",
        "configured-default",
        "built-in-default",
    }
)
EXECUTION_SOURCES = frozenset(
    {
        "slice-override",
        "configured-default",
        "harness-default",
    }
)
TASK_ENTRYPOINT = "task.md"
RELATED_TASKS_DIR = "related-tasks"
ORIGINAL_REQUEST_START = "<!-- multi-shot-review:original-request:start -->"
ORIGINAL_REQUEST_END = "<!-- multi-shot-review:original-request:end -->"
SLICE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class ReviewStateError(RuntimeError):
    """Raised when review state is invalid or a requested mutation is rejected."""


def _validate_execution_choice(
    value: Any,
    source: Any,
    *,
    field: str,
    owner: str,
) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ReviewStateError(f"{owner} must have a {field} string or null")
    if source not in EXECUTION_SOURCES:
        raise ReviewStateError(f"{owner} has invalid {field}_source")
    if (value is None) != (source == "harness-default"):
        raise ReviewStateError(
            f"{owner} {field} must be null exactly when {field}_source is harness-default"
        )


def _validate_harness_choice(value: Any, source: Any, *, owner: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ReviewStateError(f"{owner} must have a harness string")
    try:
        get_harness(value)
    except HarnessError as exc:
        raise ReviewStateError(f"{owner} has invalid harness: {exc}") from exc
    if source not in HARNESS_SOURCES:
        raise ReviewStateError(f"{owner} has invalid harness_source")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def filename_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")


def session_id() -> str:
    return f"{filename_timestamp()}-{secrets.token_hex(4)}"


def parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def repo_root(path: Path | None = None) -> Path:
    start = Path.cwd() if path is None else path
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=start,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError:
        return start.resolve()
    if proc.returncode == 0 and proc.stdout.strip():
        return Path(proc.stdout.strip()).resolve()
    return start.resolve()


def create_review_dir(root: Path) -> Path:
    review_root = root.resolve() / ".review"
    for _ in range(10):
        review_dir = review_root / session_id()
        try:
            review_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        return review_dir
    raise ReviewStateError("could not create a unique review directory after 10 attempts")


def init_review_state(root: Path, task: str, *, target: dict[str, str] | None = None) -> Path:
    task = _require_non_empty_text(task, "task")
    root = repo_root(root)
    review_dir = create_review_dir(root)
    write_task_entrypoint(review_dir, task)
    state = ReviewState.new(
        review_dir=review_dir,
        root=root,
        target=target or {"kind": "uncommitted"},
    )
    state.save()
    return review_dir


def task_entrypoint(review_dir: Path) -> Path:
    return review_dir.resolve() / TASK_ENTRYPOINT


def related_tasks_dir(review_dir: Path) -> Path:
    return review_dir.resolve() / RELATED_TASKS_DIR


def write_task_entrypoint(review_dir: Path, original_request: str) -> None:
    original_request = _require_non_empty_text(original_request, "task")
    review_dir = review_dir.resolve()
    related_tasks_dir(review_dir).mkdir(parents=True, exist_ok=True)
    _atomic_write_text(task_entrypoint(review_dir), _render_task_entrypoint(review_dir, original_request))


def add_related_task(review_dir: Path, name: str, *, text: str | None, file: Path | None, directory: Path | None) -> None:
    ReviewState._validate_slice_name(name)
    input_count = sum(value is not None for value in (text, file, directory))
    if input_count != 1:
        raise ReviewStateError("choose exactly one related task input: --text, --file, or --dir")

    review_dir = review_dir.resolve()
    with ReviewState.locked(review_dir):
        entrypoint = task_entrypoint(review_dir)
        if not entrypoint.exists():
            raise ReviewStateError(f"missing task entrypoint: {entrypoint}")

        related_dir = related_tasks_dir(review_dir)
        related_dir.mkdir(parents=True, exist_ok=True)
        file_target = related_dir / f"{name}.md"
        dir_target = related_dir / name
        tmp_target: Path | None = None

        try:
            if text is not None:
                tmp_target = related_dir / f".{name}.{uuid.uuid4().hex}.tmp.md"
                tmp_target.write_text(_require_non_empty_text(text, "related task text"), encoding="utf-8")
                final_target = file_target
            elif file is not None:
                if not file.is_file():
                    raise ReviewStateError(f"related task file is not a file: {file}")
                source_text = _require_non_empty_text(file.read_text(encoding="utf-8"), "related task file")
                tmp_target = related_dir / f".{name}.{uuid.uuid4().hex}.tmp.md"
                tmp_target.write_text(source_text, encoding="utf-8")
                final_target = file_target
            elif directory is not None:
                if not directory.is_dir():
                    raise ReviewStateError(f"related task directory is not a directory: {directory}")
                if _path_is_relative_to(related_dir.resolve(), directory.resolve()):
                    raise ReviewStateError("related task directory cannot contain the review directory")
                tmp_target = related_dir / f".{name}.{uuid.uuid4().hex}.tmp"
                shutil.copytree(directory, tmp_target)
                final_target = dir_target

            backups = _backup_related_task_targets(
                file_target=file_target,
                dir_target=dir_target,
            )
            replaced = False
            try:
                os.replace(tmp_target, final_target)
                replaced = True
                tmp_target = None
                refresh_task_entrypoint(review_dir)
            except Exception:
                if replaced:
                    _remove_related_task_target(final_target)
                _restore_related_task_backups(backups)
                raise
            _remove_related_task_backups(backups)
            tmp_target = None
        finally:
            if tmp_target is not None and tmp_target.exists():
                _remove_related_task_target(tmp_target)


def refresh_task_entrypoint(review_dir: Path) -> None:
    review_dir = review_dir.resolve()
    entrypoint = task_entrypoint(review_dir)
    if not entrypoint.exists():
        raise ReviewStateError(f"missing task entrypoint: {entrypoint}")
    original_request = _extract_original_request(entrypoint.read_text(encoding="utf-8"))
    _atomic_write_text(entrypoint, _render_task_entrypoint(review_dir, original_request))


def build_task_context_prompt(review_dir: Path) -> str:
    entrypoint = task_entrypoint(review_dir)
    if not entrypoint.exists():
        raise ReviewStateError(f"missing task entrypoint: {entrypoint}")
    return (
        "Review task context:\n"
        f"- Read {entrypoint} before reviewing.\n"
        "- It contains the original user request and any related/future tasks.\n"
        "- Treat related/future tasks as deferred-work context, not as part of the current review scope.\n"
        "- Avoid flagging missing follow-up work when it is clearly covered by related/future tasks.\n"
        f"- Return only one JSON object matching {RESULT_SCHEMA_PATH}.\n"
        "- Do not wrap the JSON in Markdown fences or add prose outside it.\n"
    )


def _require_non_empty_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewStateError(f"{label} must be non-empty")
    return value.strip()


def _validate_session_target(target: Any) -> dict[str, str]:
    if not isinstance(target, dict):
        raise ReviewStateError("target must be an object")
    kind = target.get("kind")
    if kind == "uncommitted":
        if set(target) != {"kind"}:
            raise ReviewStateError("uncommitted target accepts only kind")
        return {"kind": "uncommitted"}
    if kind not in {"base", "commit"}:
        raise ReviewStateError("target kind must be uncommitted, base, or commit")
    value = target.get("value")
    if not isinstance(value, str) or not value.strip():
        raise ReviewStateError(f"{kind} target requires a value")
    if set(target) != {"kind", "value"}:
        raise ReviewStateError(f"{kind} target accepts only kind and value")
    return {"kind": kind, "value": value.strip()}


def _native_target_from_session(target: dict[str, str]) -> dict[str, Any]:
    target = _validate_session_target(target)
    if target["kind"] == "uncommitted":
        return {"uncommitted": True}
    return {target["kind"]: target["value"]}


def _render_task_entrypoint(review_dir: Path, original_request: str) -> str:
    original_request = _require_non_empty_text(original_request, "task")
    related_items = _related_task_index_items(review_dir)
    related_section = "\n".join(related_items) if related_items else "No related/future tasks registered."
    return (
        "# Review Task\n\n"
        "## Original User Request\n\n"
        f"{ORIGINAL_REQUEST_START}\n"
        f"{original_request}\n\n"
        f"{ORIGINAL_REQUEST_END}\n\n"
        "## Related/Future Tasks\n\n"
        f"{related_section}\n\n"
        "## Reviewer Guidance\n\n"
        "- Review the current slice against the original user request.\n"
        "- Treat related/future tasks as deferred-work context, not as current review scope.\n"
        "- Do not flag missing follow-up work when it is clearly covered by a related/future task.\n"
        "- Report actionable findings introduced, worsened, or made reachable by the change when "
        "they have plausible production impact or imminent maintainability impact.\n"
        "- Missing-test findings require a meaningful regression path.\n"
        "- Return no findings when this threshold is unmet.\n"
        "- An explicit lower threshold in the original user request takes precedence.\n"
    )


def _related_task_index_items(review_dir: Path) -> list[str]:
    related_dir = related_tasks_dir(review_dir)
    if not related_dir.exists():
        return []
    items: list[str] = []
    for path in sorted(related_dir.iterdir(), key=lambda item: item.name):
        if path.name.startswith("."):
            continue
        if path.is_file() and path.suffix == ".md":
            items.append(f"- [{path.stem}]({RELATED_TASKS_DIR}/{path.name})")
        elif path.is_dir():
            items.append(f"- [{path.name}]({RELATED_TASKS_DIR}/{path.name}/)")
    return items


def _extract_original_request(task_text: str) -> str:
    if ORIGINAL_REQUEST_START in task_text and ORIGINAL_REQUEST_END in task_text:
        original_request = task_text.split(ORIGINAL_REQUEST_START, 1)[1].rsplit(ORIGINAL_REQUEST_END, 1)[0]
        return _require_non_empty_text(original_request, "task")

    start_marker = "## Original User Request\n\n"
    end_marker = "\n\n## Related/Future Tasks\n\n"
    if start_marker not in task_text or end_marker not in task_text:
        raise ReviewStateError("task entrypoint has an unsupported format")
    original_request = task_text.split(start_marker, 1)[1].split(end_marker, 1)[0]
    return _require_non_empty_text(original_request, "task")


def _remove_related_task_target(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _backup_related_task_targets(
    *,
    file_target: Path,
    dir_target: Path,
) -> list[tuple[Path, Path]]:
    targets = [file_target, dir_target]
    backups: list[tuple[Path, Path]] = []
    for target in targets:
        if not target.exists():
            continue
        backup = target.parent / f".{target.name}.{uuid.uuid4().hex}.bak"
        os.replace(target, backup)
        backups.append((target, backup))
    return backups


def _restore_related_task_backups(backups: list[tuple[Path, Path]]) -> None:
    for target, backup in reversed(backups):
        if backup.exists():
            _remove_related_task_target(target)
            os.replace(backup, target)


def _remove_related_task_backups(backups: list[tuple[Path, Path]]) -> None:
    for _target, backup in backups:
        if backup.exists():
            _remove_related_task_target(backup)


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with tmp_path.open("wb") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def append_error(review_dir: Path, title: str, details: str) -> None:
    errors = review_dir / "_errors.md"
    with errors.open("a", encoding="utf-8") as fh:
        fh.write(f"## {now_iso()} {title}\n\n")
        fh.write(details.rstrip())
        fh.write("\n\n")


@dataclass(frozen=True)
class Reservation:
    run_id: str
    slice_name: str
    pass_number: int
    output_file: Path
    slice_data: dict[str, Any]


@dataclass(frozen=True)
class ReviewExecution:
    reservation: Reservation
    proc: subprocess.CompletedProcess[str]
    stdout_log: Path
    stderr_log: Path
    launch_error: OSError | None = None
    timed_out: bool = False


class LockedReviewState(AbstractContextManager["ReviewState"]):
    def __init__(self, review_dir: Path):
        self.review_dir = review_dir.resolve()
        self._fh: Any = None
        self.state: ReviewState | None = None

    def __enter__(self) -> "ReviewState":
        try:
            self.review_dir.mkdir(parents=True, exist_ok=True)
            self._fh = (self.review_dir / "_state.lock").open("a+", encoding="utf-8")
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
            self.state = ReviewState.load(self.review_dir)
            return self.state
        except OSError as exc:
            self._release()
            raise ReviewStateError(f"could not open review state lock: {exc}") from exc
        except Exception:
            self._release()
            raise

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        self._release()
        return False

    def _release(self) -> None:
        if self._fh is not None:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None
        self.state = None


class LockedClassifierSession(AbstractContextManager[None]):
    """Prevent overlapping classifiers and make abandoned attempts detectable."""

    def __init__(self, review_dir: Path):
        self.review_dir = review_dir.resolve()
        self._fh: Any = None

    def __enter__(self) -> None:
        try:
            self.review_dir.mkdir(parents=True, exist_ok=True)
            self._fh = (self.review_dir / "_classifier.lock").open(
                "a+", encoding="utf-8"
            )
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._release()
            raise ReviewStateError("classifier is already running") from exc
        except OSError as exc:
            self._release()
            raise ReviewStateError(f"could not open classifier lock: {exc}") from exc

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        self._release()
        return False

    def _release(self) -> None:
        if self._fh is not None:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None


class ReviewState:
    """Rich wrapper around the persisted review session state."""

    def __init__(self, review_dir: Path, data: dict[str, Any]):
        self.review_dir = review_dir.resolve()
        self.data = data
        self._artifact_snapshots: dict[Path, bytes | None] = {}
        self.validate()

    @classmethod
    def new(
        cls,
        review_dir: Path,
        root: Path,
        target: dict[str, str] | None = None,
    ) -> "ReviewState":
        target = _validate_session_target(target or {"kind": "uncommitted"})
        data: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "session": {
                "created_at": now_iso(),
                "review_dir": str(review_dir.resolve()),
                "root": str(root.resolve()),
                "target": target,
            },
            "classifications": [],
            "slices": {},
            "history": [],
            "completed": False,
            "last_error": None,
        }
        return cls(review_dir, data)

    @classmethod
    def load(cls, review_dir: Path) -> "ReviewState":
        state_path = review_dir.resolve() / "_state.json"
        if not state_path.exists():
            raise ReviewStateError(f"missing review state: {state_path}")
        try:
            with state_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ReviewStateError(f"invalid review state JSON: {exc}") from exc
        return cls(review_dir.resolve(), data)

    @classmethod
    def locked(cls, review_dir: Path) -> LockedReviewState:
        return LockedReviewState(review_dir)

    @classmethod
    def classifier_locked(cls, review_dir: Path) -> LockedClassifierSession:
        return LockedClassifierSession(review_dir)

    def save(self) -> None:
        tmp_name: str | None = None
        try:
            self.validate()
            self.review_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix="_state.",
                suffix=".tmp",
                dir=str(self.review_dir),
                text=True,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2, sort_keys=True)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.review_dir / "_state.json")
            tmp_name = None
        except Exception:
            self._rollback_artifacts()
            raise
        else:
            self._artifact_snapshots.clear()
        finally:
            if tmp_name is not None and os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def _remember_artifact(self, path: Path) -> None:
        path = path.resolve()
        if path in self._artifact_snapshots:
            return
        try:
            self._artifact_snapshots[path] = path.read_bytes()
        except FileNotFoundError:
            self._artifact_snapshots[path] = None

    def _write_artifact(self, path: Path, text: str) -> None:
        self._remember_artifact(path)
        _atomic_write_text(path, text)

    def _remove_artifact(self, path: Path) -> None:
        self._remember_artifact(path)
        path.unlink(missing_ok=True)

    def _rollback_artifacts(self) -> None:
        snapshots = list(self._artifact_snapshots.items())
        self._artifact_snapshots.clear()
        for path, content in reversed(snapshots):
            try:
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    _atomic_write_bytes(path, content)
            except OSError:
                pass

    def validate(self) -> None:
        data = self.data
        if not isinstance(data, dict):
            raise ReviewStateError("state must be a JSON object")
        if type(data.get("schema_version")) is not int or data["schema_version"] != SCHEMA_VERSION:
            raise ReviewStateError(f"unsupported review state schema: {data.get('schema_version')!r}")
        if not isinstance(data.get("session"), dict):
            raise ReviewStateError("state session must be an object")
        session = data["session"]
        if not isinstance(session.get("created_at"), str) or not session["created_at"]:
            raise ReviewStateError("state session must have created_at")
        if not isinstance(session.get("review_dir"), str) or not session["review_dir"]:
            raise ReviewStateError("state session must have review_dir")
        if not isinstance(session.get("root"), str) or not session["root"]:
            raise ReviewStateError("state session must have root")
        try:
            _validate_session_target(session.get("target"))
        except ReviewStateError as exc:
            raise ReviewStateError(f"state session has invalid target: {exc}") from exc
        if not isinstance(data.get("slices"), dict):
            raise ReviewStateError("state slices must be an object")
        if not isinstance(data.get("classifications"), list):
            raise ReviewStateError("state classifications must be an array")
        for classification in data["classifications"]:
            self._validate_classification(classification)
        if not isinstance(data.get("history"), list):
            raise ReviewStateError("state history must be an array")
        if not isinstance(data.get("completed"), bool):
            raise ReviewStateError("state completed must be a boolean")
        session_finding_ids: set[str] = set()
        for name, item in data["slices"].items():
            self._validate_slice_name(name)
            if not isinstance(item, dict):
                raise ReviewStateError(f"slice {name!r} must be an object")
            if item.get("name") != name:
                raise ReviewStateError(f"slice {name!r} has mismatched name")
            if item.get("mode") not in {"native", "prompt"}:
                raise ReviewStateError(f"slice {name!r} has invalid mode")
            if item["mode"] == "native":
                self._validate_native_target(name, item.get("target"))
                if item["target"] != _native_target_from_session(session["target"]):
                    raise ReviewStateError(
                        f"native slice {name!r} target must match session target"
                    )
                if item.get("prompt") is not None:
                    raise ReviewStateError(f"native slice {name!r} cannot have prompt text")
            if item["mode"] == "prompt":
                if item.get("target") is not None:
                    raise ReviewStateError(f"prompt slice {name!r} cannot have native target")
                if not isinstance(item.get("prompt"), str) or not item["prompt"].strip():
                    raise ReviewStateError(f"prompt slice {name!r} must have prompt text")
            if not isinstance(item.get("cwd"), str) or not item["cwd"]:
                raise ReviewStateError(f"slice {name!r} must have cwd")
            _validate_harness_choice(
                item.get("harness"),
                item.get("harness_source"),
                owner=f"slice {name!r}",
            )
            _validate_execution_choice(
                item.get("model"),
                item.get("model_source"),
                field="model",
                owner=f"slice {name!r}",
            )
            _validate_execution_choice(
                item.get("reasoning"),
                item.get("reasoning_source"),
                field="reasoning",
                owner=f"slice {name!r}",
            )
            if type(item.get("next_pass")) is not int or item["next_pass"] < 1:
                raise ReviewStateError(f"slice {name!r} must have a positive next_pass")
            if not isinstance(item.get("complete"), bool):
                raise ReviewStateError(f"slice {name!r} must have complete boolean")
            if not isinstance(item.get("runs"), list):
                raise ReviewStateError(f"slice {name!r} must have runs array")
            if item.get("source", "classifier") not in {"classifier", "user"}:
                raise ReviewStateError(f"slice {name!r} has invalid source")
            if not isinstance(item.get("removed", False), bool):
                raise ReviewStateError(f"slice {name!r} has invalid removed marker")
            if item.get("removal_source") not in {None, "classifier", "user"}:
                raise ReviewStateError(f"slice {name!r} has invalid removal source")
            definition_version = item.get("definition_version", 1)
            if type(definition_version) is not int or definition_version < 1:
                raise ReviewStateError(f"slice {name!r} has invalid definition version")
            for run in item["runs"]:
                self._validate_run(name, run)
                stored_findings = run.get("findings") or self._load_findings_archive(
                    name, run
                )
                for finding in stored_findings:
                    finding_id = finding["id"]
                    if finding_id in session_finding_ids:
                        raise ReviewStateError(
                            f"duplicate session finding id: {finding_id}"
                        )
                    session_finding_ids.add(finding_id)

    @staticmethod
    def _validate_run(slice_name: str, run: Any) -> None:
        if not isinstance(run, dict):
            raise ReviewStateError(f"slice {slice_name!r} has non-object run entry")
        if not isinstance(run.get("id"), str) or not run["id"]:
            raise ReviewStateError(f"slice {slice_name!r} has run with invalid id")
        if type(run.get("pass")) is not int or run["pass"] < 1:
            raise ReviewStateError(f"slice {slice_name!r} has run with invalid pass")
        if not isinstance(run.get("output_file"), str) or not run["output_file"]:
            raise ReviewStateError(f"slice {slice_name!r} has run with invalid output_file")
        if run.get("status") not in {"running", "findings", "no_findings", "failed", "timeout", "ignored"}:
            raise ReviewStateError(f"slice {slice_name!r} has run with invalid status")
        if not isinstance(run.get("started_at"), str) or not run["started_at"]:
            raise ReviewStateError(f"slice {slice_name!r} has run with invalid started_at")
        if run.get("ended_at") is not None and not isinstance(run.get("ended_at"), str):
            raise ReviewStateError(f"slice {slice_name!r} has run with invalid ended_at")
        if run.get("exit_code") is not None and type(run.get("exit_code")) is not int:
            raise ReviewStateError(f"slice {slice_name!r} has run with invalid exit_code")
        if run.get("classification") is not None and not isinstance(run.get("classification"), str):
            raise ReviewStateError(f"slice {slice_name!r} has run with invalid classification")
        finding_count = run.get("finding_count")
        if finding_count is not None and (type(finding_count) is not int or finding_count < 0):
            raise ReviewStateError(f"slice {slice_name!r} has run with invalid finding_count")
        findings = run.get("findings")
        if findings is not None and not isinstance(findings, list):
            raise ReviewStateError(f"slice {slice_name!r} has invalid findings")
        if isinstance(findings, list):
            try:
                for index, finding in enumerate(findings):
                    validate_stored_finding(
                        finding, owner=f"slice {slice_name!r} run finding {index}"
                    )
            except ReviewResultError as exc:
                raise ReviewStateError(str(exc)) from exc
            if finding_count != len(findings):
                raise ReviewStateError(
                    f"slice {slice_name!r} run finding_count does not match findings"
                )
        findings_archive = run.get("findings_archive")
        if findings_archive is not None and (
            not isinstance(findings_archive, str) or not findings_archive
        ):
            raise ReviewStateError(
                f"slice {slice_name!r} run has invalid findings_archive"
            )
        if findings is not None and findings_archive is not None:
            raise ReviewStateError(
                f"slice {slice_name!r} run cannot have active and archived findings"
            )
        status = run["status"]
        if status == "findings" and not (
            (findings and findings_archive is None)
            or (
                findings is None
                and findings_archive is not None
                and type(finding_count) is int
                and finding_count > 0
            )
        ):
            raise ReviewStateError(
                f"slice {slice_name!r} finding run must have active or archived findings"
            )
        if status == "no_findings" and findings != []:
            raise ReviewStateError(
                f"slice {slice_name!r} no-findings run must have an empty finding list"
            )
        if status in {"running", "failed", "timeout"} and findings is not None:
            raise ReviewStateError(
                f"slice {slice_name!r} unfinished or failed run cannot have findings"
            )
        if run.get("runner_pid") is not None and not isinstance(run.get("runner_pid"), int):
            raise ReviewStateError(f"slice {slice_name!r} has run with invalid runner_pid")
        if run.get("runner_key") is not None and not isinstance(run.get("runner_key"), str):
            raise ReviewStateError(f"slice {slice_name!r} has run with invalid runner_key")
        if run.get("error") is not None and not isinstance(run.get("error"), str):
            raise ReviewStateError(f"slice {slice_name!r} has run with invalid error")
        _validate_execution_choice(
            run.get("model"),
            run.get("model_source"),
            field="model",
            owner=f"slice {slice_name!r} run",
        )
        _validate_harness_choice(
            run.get("harness"),
            run.get("harness_source"),
            owner=f"slice {slice_name!r} run",
        )
        _validate_execution_choice(
            run.get("reasoning"),
            run.get("reasoning_source"),
            field="reasoning",
            owner=f"slice {slice_name!r} run",
        )
        definition_version = run.get("definition_version", 1)
        if type(definition_version) is not int or definition_version < 1:
            raise ReviewStateError(f"slice {slice_name!r} has run with invalid definition version")

    @staticmethod
    def _validate_classification(value: Any) -> None:
        if not isinstance(value, dict):
            raise ReviewStateError("state classification entries must be objects")
        expected = {
            "id",
            "harness",
            "model",
            "reasoning",
            "started_at",
            "ended_at",
            "status",
            "exit_code",
        }
        if set(value) != expected:
            raise ReviewStateError("state classification entry has invalid fields")
        for field in ("id", "harness", "started_at"):
            if not isinstance(value[field], str) or not value[field]:
                raise ReviewStateError(f"state classification has invalid {field}")
        try:
            get_harness(value["harness"])
        except HarnessError as exc:
            raise ReviewStateError(f"state classification has invalid harness: {exc}") from exc
        for field in ("model", "reasoning", "ended_at"):
            if value[field] is not None and (
                not isinstance(value[field], str) or not value[field].strip()
            ):
                raise ReviewStateError(f"state classification has invalid {field}")
        if value["status"] not in {"running", "succeeded", "failed"}:
            raise ReviewStateError("state classification has invalid status")
        if value["exit_code"] is not None and type(value["exit_code"]) is not int:
            raise ReviewStateError("state classification has invalid exit_code")
        status = value["status"]
        ended_at = value["ended_at"]
        exit_code = value["exit_code"]
        if status == "running":
            if ended_at is not None or exit_code is not None:
                raise ReviewStateError(
                    "state running classification must not have terminal fields"
                )
        elif ended_at is None or exit_code is None:
            raise ReviewStateError(
                "state terminal classification must have terminal fields"
            )
        elif status == "succeeded" and exit_code != 0:
            raise ReviewStateError(
                "state succeeded classification must have exit_code 0"
            )
        elif status == "failed" and exit_code == 0:
            raise ReviewStateError(
                "state failed classification must have nonzero exit_code"
            )

    @staticmethod
    def _validate_native_target(slice_name: str, target: Any) -> None:
        if not isinstance(target, dict):
            raise ReviewStateError(f"native slice {slice_name!r} must have target object")
        target_count = sum(
            (
                target.get("uncommitted") is True,
                isinstance(target.get("base"), str) and bool(target["base"]),
                isinstance(target.get("commit"), str) and bool(target["commit"]),
            )
        )
        if target_count != 1:
            raise ReviewStateError(f"native slice {slice_name!r} must have exactly one target")
        allowed = {"uncommitted", "base", "commit"}
        extra = set(target) - allowed
        if extra:
            raise ReviewStateError(f"native slice {slice_name!r} has unsupported target keys")
        if "uncommitted" in target and target.get("uncommitted") is not True:
            raise ReviewStateError(f"native slice {slice_name!r} has invalid uncommitted target")
        for key in ("base", "commit"):
            if key in target and (not isinstance(target[key], str) or not target[key]):
                raise ReviewStateError(f"native slice {slice_name!r} has invalid {key} target")

    @staticmethod
    def _validate_slice_name(name: str) -> None:
        if not isinstance(name, str) or not SLICE_RE.fullmatch(name):
            raise ReviewStateError(
                "slice names must be 1-64 chars of lowercase letters, digits, '.', '_' or '-', "
                "starting with a letter or digit"
            )
        if "/" in name or "\\" in name or name in {".", ".."}:
            raise ReviewStateError("slice names cannot contain path separators or dot names")

    def start_classification(self, profile: ResolvedProfile) -> str:
        classification_id = uuid.uuid4().hex
        self.data["classifications"].append(
            {
                "id": classification_id,
                "harness": profile.harness,
                "model": profile.model,
                "reasoning": profile.reasoning,
                "started_at": now_iso(),
                "ended_at": None,
                "status": "running",
                "exit_code": None,
            }
        )
        return classification_id

    def recover_running_classifications(self) -> int:
        """Fail attempts abandoned after their exclusive process lock was released."""

        recovered = 0
        for item in self.data["classifications"]:
            if item["status"] != "running":
                continue
            item["ended_at"] = now_iso()
            item["exit_code"] = 1
            item["status"] = "failed"
            recovered += 1
        return recovered

    def complete_classification(self, classification_id: str, exit_code: int) -> None:
        matches = [
            item
            for item in self.data["classifications"]
            if item["id"] == classification_id
        ]
        if len(matches) != 1:
            raise ReviewStateError(f"classification not found: {classification_id}")
        item = matches[0]
        if item["status"] != "running":
            raise ReviewStateError(f"classification is already complete: {classification_id}")
        item["ended_at"] = now_iso()
        item["exit_code"] = exit_code
        item["status"] = "succeeded" if exit_code == 0 else "failed"

    def add_slice(
        self,
        *,
        name: str,
        mode: str,
        target: dict[str, Any] | None,
        prompt: str | None,
        cwd: Path,
        harness: str = "codex",
        harness_source: str = "built-in-default",
        model: str | None = None,
        model_source: str | None = None,
        reasoning: str | None = None,
        reasoning_source: str | None = None,
        source: str = "classifier",
        user_directive: str | None = None,
    ) -> None:
        self._validate_slice_name(name)
        if source not in {"classifier", "user"}:
            raise ReviewStateError("slice source must be classifier or user")
        if source == "user":
            user_directive = _require_non_empty_text(user_directive or "", "user directive")
        if mode not in {"native", "prompt"}:
            raise ReviewStateError("slice mode must be native or prompt")
        if mode == "native":
            if not target:
                raise ReviewStateError("native slices require --uncommitted, --base, or --commit")
            expected_target = _native_target_from_session(self.data["session"]["target"])
            if target != expected_target:
                raise ReviewStateError(
                    f"native slice target must match session target: {expected_target}"
                )
            if prompt:
                raise ReviewStateError("native target flags cannot be combined with prompt input")
        if mode == "prompt":
            if target:
                raise ReviewStateError("prompt slices cannot be combined with native target flags")
            if not prompt or not prompt.strip():
                raise ReviewStateError("prompt slices require non-empty prompt text")
        _validate_harness_choice(harness, harness_source, owner="slice")
        if model_source is None:
            model_source = "harness-default" if model is None else "slice-override"
        if reasoning_source is None:
            reasoning_source = (
                "harness-default" if reasoning is None else "slice-override"
            )
        _validate_execution_choice(
            model,
            model_source,
            field="model",
            owner="slice",
        )
        _validate_execution_choice(
            reasoning,
            reasoning_source,
            field="reasoning",
            owner="slice",
        )
        definition = {
            "name": name,
            "mode": mode,
            "target": target,
            "prompt": prompt,
            "cwd": str(cwd.resolve()),
            "harness": harness,
            "harness_source": harness_source,
            "model": model,
            "model_source": model_source,
            "reasoning": reasoning,
            "reasoning_source": reasoning_source,
            "next_pass": 1,
            "complete": False,
            "last_error": None,
            "source": source,
            "user_directive": user_directive,
            "removed": False,
            "definition_version": 1,
        }
        existing = self.data["slices"].get(name)
        if existing is not None:
            if not existing.get("removed"):
                raise ReviewStateError(f"slice already exists: {name}")
            if source == "classifier" and (
                existing.get("source") == "user"
                or existing.get("removal_source") == "user"
            ):
                raise ReviewStateError(f"slice is controlled by an explicit user directive: {name}")
        active_count = sum(
            not item.get("removed")
            for item in self.data["slices"].values()
        )
        if active_count >= MAX_ACTIVE_SLICES:
            raise ReviewStateError(
                f"maximum of {MAX_ACTIVE_SLICES} active slices reached; "
                "remove or consolidate an active slice first"
            )
        if existing is not None:
            existing_snapshot = copy.deepcopy(existing)
            definition["runs"] = existing["runs"]
            definition["next_pass"] = existing["next_pass"]
            definition["definition_version"] = existing.get("definition_version", 1) + 1
            superseded_at = now_iso()
            try:
                for run in definition["runs"]:
                    if not run.get("findings"):
                        continue
                    for finding in run["findings"]:
                        if finding.get("status") == "open":
                            finding["status"] = "superseded"
                            finding["resolution"] = {
                                "kind": "superseded",
                                "definition_version": definition["definition_version"],
                                "at": superseded_at,
                            }
                    self._archive_and_render_run(name, run)
            except (OSError, UnicodeError):
                existing.clear()
                existing.update(existing_snapshot)
                self._rollback_artifacts()
                raise
            self.data["slices"][name] = definition
            self.data["history"].append(
                {
                    "event": "slice_reactivated",
                    "slice": name,
                    "source": source,
                    "at": now_iso(),
                }
            )
        else:
            definition["runs"] = []
            self.data["slices"][name] = definition
            self.data["history"].append(
                {"event": "slice_added", "slice": name, "source": source, "at": now_iso()}
            )
        self.data["completed"] = False

    def remove_slice(
        self,
        name: str,
        *,
        source: str = "classifier",
        user_directive: str | None = None,
    ) -> None:
        self._validate_slice_name(name)
        if source not in {"classifier", "user"}:
            raise ReviewStateError("slice source must be classifier or user")
        if source == "user":
            user_directive = _require_non_empty_text(user_directive or "", "user directive")
        item = self.data["slices"].get(name)
        if item is None:
            raise ReviewStateError(f"slice not found: {name}")
        if item.get("removed"):
            raise ReviewStateError(f"slice already removed: {name}")
        if source == "classifier" and item.get("source") == "user":
            raise ReviewStateError(f"slice is controlled by an explicit user directive: {name}")
        item_snapshot = copy.deepcopy(item)
        superseded_at = now_iso()
        try:
            for run in item["runs"]:
                if not run.get("findings"):
                    continue
                for finding in run["findings"]:
                    if finding.get("status") == "open":
                        finding["status"] = "superseded"
                        finding["resolution"] = {
                            "kind": "superseded",
                            "removed": True,
                            "at": superseded_at,
                        }
                self._archive_and_render_run(name, run)
        except (OSError, UnicodeError):
            item.clear()
            item.update(item_snapshot)
            self._rollback_artifacts()
            raise
        item["removed"] = True
        item["removal_source"] = source
        item["removal_directive"] = user_directive
        item["complete"] = False
        item["last_error"] = None
        self.data["history"].append(
            {
                "event": "slice_removed",
                "slice": name,
                "source": source,
                "user_directive": user_directive,
                "at": now_iso(),
            }
        )
        self._refresh_completed()

    def reserve_eligible(self) -> list[Reservation]:
        reservations: list[Reservation] = []
        self._recover_stale_running_runs()
        if self._has_running_runs():
            self._refresh_completed()
            return reservations
        for name in sorted(self.data["slices"]):
            item = self.data["slices"][name]
            if item.get("removed") or item["complete"]:
                continue
            if any(run.get("status") == "running" for run in item["runs"]):
                continue
            pass_number = item["next_pass"]
            output_file = self._next_output_file(pass_number, name, item["runs"])
            run_id = uuid.uuid4().hex
            run = {
                "id": run_id,
                "pass": pass_number,
                "output_file": str(output_file),
                "status": "running",
                "started_at": now_iso(),
                "ended_at": None,
                "exit_code": None,
                "classification": None,
                "finding_count": None,
                "findings": None,
                "findings_archive": None,
                "runner_pid": os.getpid(),
                "runner_key": _process_key(os.getpid()),
                "error": None,
                "definition_version": item.get("definition_version", 1),
                "harness": item["harness"],
                "harness_source": item["harness_source"],
                "model": item.get("model"),
                "model_source": item["model_source"],
                "reasoning": item.get("reasoning"),
                "reasoning_source": item["reasoning_source"],
            }
            item["runs"].append(run)
            item["last_error"] = None
            self.data["history"].append(
                {"event": "run_reserved", "slice": name, "run_id": run_id, "pass": pass_number, "at": now_iso()}
            )
            reservations.append(
                Reservation(
                    run_id=run_id,
                    slice_name=name,
                    pass_number=pass_number,
                    output_file=output_file,
                    slice_data={
                        **json.loads(json.dumps(item)),
                        "session_target": json.loads(
                            json.dumps(self.data["session"]["target"])
                        ),
                    },
                )
            )
        self._refresh_completed()
        return reservations

    def complete_run(
        self,
        *,
        run_id: str,
        slice_name: str,
        status: str,
        exit_code: int | None,
        classification: str | None,
        findings: list[dict[str, Any]] | None = None,
        error: str | None = None,
    ) -> bool:
        item = self.data["slices"][slice_name]
        run = next((candidate for candidate in item["runs"] if candidate.get("id") == run_id), None)
        if run is None:
            raise ReviewStateError(f"run not found: {run_id}")
        if run.get("status") != "running":
            self.data["history"].append(
                {
                    "event": "late_run_completion_ignored",
                    "slice": slice_name,
                    "run_id": run_id,
                    "current_status": run.get("status"),
                    "attempted_status": status,
                    "at": now_iso(),
                }
            )
            self._refresh_completed()
            return False
        if item.get("removed"):
            run["status"] = "ignored"
            run["ended_at"] = now_iso()
            run["exit_code"] = exit_code
            run["classification"] = "removed_during_execution"
            run["finding_count"] = 0
            run["error"] = None
            self.data["history"].append(
                {
                    "event": "removed_run_completion_ignored",
                    "slice": slice_name,
                    "run_id": run_id,
                    "at": now_iso(),
                }
            )
            self._refresh_completed()
            return True
        if run.get("definition_version", 1) != item.get("definition_version", 1):
            run["status"] = "ignored"
            run["ended_at"] = now_iso()
            run["exit_code"] = exit_code
            run["classification"] = "superseded_during_execution"
            run["finding_count"] = 0
            run["error"] = None
            item["complete"] = False
            self.data["history"].append(
                {
                    "event": "superseded_run_completion_ignored",
                    "slice": slice_name,
                    "run_id": run_id,
                    "at": now_iso(),
                }
            )
            self._refresh_completed()
            return True
        item_snapshot = copy.deepcopy(item)
        if status == "findings":
            findings = assign_finding_ids(findings or [], used_ids=self._finding_ids())
            if not findings:
                raise ReviewStateError("finding run must contain at least one finding")
        elif status == "no_findings":
            findings = []
        elif status not in {"failed", "timeout"}:
            raise ReviewStateError(f"invalid run status: {status}")

        if status in {"findings", "no_findings"}:
            output_path = Path(run["output_file"])
            try:
                self._write_artifact(
                    output_path,
                    render_review_markdown(
                        findings or [],
                        harness=run["harness"],
                        harness_source=run["harness_source"],
                        model=run.get("model"),
                        model_source=run["model_source"],
                        reasoning=run.get("reasoning"),
                        reasoning_source=run["reasoning_source"],
                    ),
                )
                self._supersede_prior_findings(slice_name, item, run)
            except (OSError, UnicodeError) as exc:
                item.clear()
                item.update(item_snapshot)
                run = next(
                    candidate
                    for candidate in item["runs"]
                    if candidate.get("id") == run_id
                )
                error = f"could not persist review artifacts: {exc}"
                self._rollback_artifacts()
                try:
                    append_error(
                        self.review_dir,
                        f"failed to persist review artifacts for {slice_name}",
                        f"Slice: {slice_name}\nOutput: {run['output_file']}\nError: {exc}",
                    )
                except (OSError, UnicodeError):
                    pass
                status = "failed"
                classification = None
                findings = None

        run["status"] = status
        run["ended_at"] = now_iso()
        run["exit_code"] = exit_code
        run["classification"] = classification
        run["findings"] = findings
        run["finding_count"] = len(findings) if findings is not None else None
        run["error"] = error

        if status == "findings":
            item["next_pass"] = max(item["next_pass"], int(run["pass"]) + 1)
            item["complete"] = False
            item["last_error"] = None
        elif status == "no_findings":
            item["complete"] = True
            item["last_error"] = None
        elif status in {"failed", "timeout"}:
            item["complete"] = False
            item["last_error"] = error or ("review process timed out" if status == "timeout" else "review process failed")
            self.data["last_error"] = {"slice": slice_name, "run_id": run_id, "error": item["last_error"], "at": now_iso()}

        self.data["history"].append(
            {
                "event": "run_completed",
                "slice": slice_name,
                "run_id": run_id,
                "status": status,
                "classification": classification,
                "at": now_iso(),
            }
        )
        self._refresh_completed()
        return True

    def _finding_ids(self) -> set[str]:
        return {finding["id"] for finding in self._session_findings()}

    def _session_findings(self) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        for slice_name, item in self.data["slices"].items():
            for run in item["runs"]:
                findings.extend(
                    run.get("findings")
                    or self._load_findings_archive(slice_name, run)
                )
        return findings

    def _load_findings_archive(
        self, slice_name: str, run: dict[str, Any]
    ) -> list[dict[str, Any]]:
        archive = run.get("findings_archive")
        if not archive:
            return []
        archive_path = Path(archive)
        expected_path = self.review_dir / "history" / f"{run['id']}.json"
        if archive_path.resolve() != expected_path.resolve():
            raise ReviewStateError(
                f"run {run['id']} has unexpected findings archive path: {archive}"
            )
        try:
            archive_data = json.loads(archive_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ReviewStateError(
                f"could not read findings archive {archive}: {exc}"
            ) from exc
        if not isinstance(archive_data, dict) or set(archive_data) != {
            "schema_version",
            "run_id",
            "slice",
            "pass",
            "archived_at",
            "findings",
        }:
            raise ReviewStateError(f"invalid findings archive shape: {archive}")
        if (
            type(archive_data["schema_version"]) is not int
            or archive_data["schema_version"] != RESULT_SCHEMA_VERSION
            or archive_data["run_id"] != run["id"]
            or archive_data["slice"] != slice_name
            or archive_data["pass"] != run["pass"]
            or not isinstance(archive_data["archived_at"], str)
            or not archive_data["archived_at"]
            or not isinstance(archive_data["findings"], list)
            or len(archive_data["findings"]) != run.get("finding_count")
        ):
            raise ReviewStateError(f"invalid findings archive metadata: {archive}")
        findings: list[dict[str, Any]] = []
        for index, finding in enumerate(archive_data["findings"]):
            try:
                validated = validate_stored_finding(
                    finding, owner=f"archive {archive} finding {index}"
                )
            except ReviewResultError as exc:
                raise ReviewStateError(str(exc)) from exc
            if validated["status"] == "open":
                raise ReviewStateError(
                    f"archive {archive} contains open finding {validated['id']}"
                )
            findings.append(validated)
        return findings

    def _finding_ids_for_run(
        self, slice_name: str, run: dict[str, Any]
    ) -> list[str]:
        return [
            finding["id"]
            for finding in (
                run.get("findings")
                or self._load_findings_archive(slice_name, run)
            )
        ]

    def _duplicate_targets(self) -> set[str]:
        return {
            finding["resolution"]["finding_id"]
            for finding in self._session_findings()
            if finding.get("status") == "ignored"
            and isinstance(finding.get("resolution"), dict)
            and finding["resolution"].get("kind") == "duplicate"
        }

    def _supersede_prior_findings(
        self,
        slice_name: str,
        item: dict[str, Any],
        successor_run: dict[str, Any],
    ) -> None:
        superseded_at = now_iso()
        for prior_run in item["runs"]:
            if prior_run is successor_run or not prior_run.get("findings"):
                continue
            for finding in prior_run["findings"]:
                if finding.get("status") != "open":
                    continue
                finding["status"] = "superseded"
                finding["resolution"] = {
                    "kind": "superseded",
                    "successor_run_id": successor_run["id"],
                    "at": superseded_at,
                }
            self._archive_and_render_run(slice_name, prior_run)

    def ignore_finding(self, finding_id: str, reason: str) -> tuple[bool, str]:
        reason = _require_non_empty_text(reason, "ignore reason")
        name, item, run, finding = self._find_active_finding(finding_id)
        item_snapshot = copy.deepcopy(item)
        if finding["status"] != "open":
            raise ReviewStateError(f"finding is already terminal: {finding_id}")
        finding["status"] = "ignored"
        finding["resolution"] = {
            "kind": "rejected",
            "text": reason,
            "at": now_iso(),
        }
        try:
            if self._all_findings_terminal(run):
                run["status"] = "ignored"
                run["classification"] = "ignored_findings"
                item["complete"] = True
                item["last_error"] = None
                self._archive_and_render_run(name, run)
            else:
                self._render_run_findings(run)
                item["complete"] = False
        except (OSError, UnicodeError):
            item.clear()
            item.update(item_snapshot)
            self._rollback_artifacts()
            raise
        self._refresh_completed()
        return True, f"ignored: {finding_id}"

    def dedupe_finding(
        self, finding_id: str, canonical_id: str
    ) -> tuple[bool, str]:
        if finding_id == canonical_id:
            raise ReviewStateError("a finding cannot duplicate itself")
        name, item, run, finding = self._find_active_finding(finding_id)
        item_snapshot = copy.deepcopy(item)
        _canonical_name, _canonical_item, _canonical_run, canonical = (
            self._find_active_finding(canonical_id)
        )
        if finding["status"] != "open":
            raise ReviewStateError(f"finding is already terminal: {finding_id}")
        if canonical["status"] != "open":
            raise ReviewStateError(f"canonical finding must be open: {canonical_id}")
        if finding_id in self._duplicate_targets():
            raise ReviewStateError(
                f"canonical finding cannot become a duplicate: {finding_id}"
            )
        finding["status"] = "ignored"
        finding["resolution"] = {
            "kind": "duplicate",
            "finding_id": canonical_id,
            "at": now_iso(),
        }
        try:
            if self._all_findings_terminal(run):
                run["status"] = "ignored"
                run["classification"] = "ignored_findings"
                item["complete"] = True
                item["last_error"] = None
                self._archive_and_render_run(name, run)
            else:
                self._render_run_findings(run)
                item["complete"] = False
        except (OSError, UnicodeError):
            item.clear()
            item.update(item_snapshot)
            self._rollback_artifacts()
            raise
        self._refresh_completed()
        return True, f"deduplicated: {finding_id} -> {canonical_id}"

    def _find_active_finding(
        self, finding_id: str
    ) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
        matches = [
            (name, item, run, finding)
            for name, item in self.data["slices"].items()
            for run in item["runs"]
            for finding in (run.get("findings") or [])
            if finding.get("id") == finding_id
        ]
        if not matches:
            raise ReviewStateError(f"active finding not found: {finding_id}")
        if len(matches) > 1:
            raise ReviewStateError(f"duplicate active finding id: {finding_id}")
        return matches[0]

    @staticmethod
    def _all_findings_terminal(run: dict[str, Any]) -> bool:
        findings = run.get("findings") or []
        return bool(findings) and all(finding.get("status") == "ignored" for finding in findings)

    def _render_run_findings(self, run: dict[str, Any]) -> None:
        self._write_artifact(
            Path(run["output_file"]),
            render_review_markdown(
                run.get("findings") or [],
                harness=run["harness"],
                harness_source=run["harness_source"],
                model=run.get("model"),
                model_source=run["model_source"],
                reasoning=run.get("reasoning"),
                reasoning_source=run["reasoning_source"],
            ),
        )

    def _archive_run_findings(self, slice_name: str, run: dict[str, Any]) -> None:
        findings = run.get("findings")
        if not findings:
            return
        archive_dir = self.review_dir / "history"
        archive_path = archive_dir / f"{run['id']}.json"
        archived_at = now_iso()
        self._write_artifact(
            archive_path,
            json.dumps(
                {
                    "schema_version": RESULT_SCHEMA_VERSION,
                    "run_id": run["id"],
                    "slice": slice_name,
                    "pass": run["pass"],
                    "archived_at": archived_at,
                    "findings": findings,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
        run["findings"] = None
        run["findings_archive"] = str(archive_path)

    def _archive_and_render_run(self, slice_name: str, run: dict[str, Any]) -> None:
        active_findings = copy.deepcopy(run.get("findings"))
        previous_archive = run.get("findings_archive")
        rendered = render_review_markdown(
            run.get("findings") or [],
            harness=run["harness"],
            harness_source=run["harness_source"],
            model=run.get("model"),
            model_source=run["model_source"],
            reasoning=run.get("reasoning"),
            reasoning_source=run["reasoning_source"],
        )
        self._archive_run_findings(slice_name, run)
        archive_path = Path(run["findings_archive"])
        try:
            self._write_artifact(Path(run["output_file"]), rendered)
        except (OSError, UnicodeError):
            try:
                self._remove_artifact(archive_path)
            finally:
                run["findings"] = active_findings
                run["findings_archive"] = previous_archive
            raise

    def _recover_stale_running_runs(self) -> None:
        for name, item in self.data["slices"].items():
            for run in item["runs"]:
                if run.get("status") != "running":
                    continue
                if _running_reservation_is_active(run):
                    continue
                if run.get("definition_version", 1) != item.get(
                    "definition_version", 1
                ):
                    run["status"] = "ignored"
                    run["ended_at"] = now_iso()
                    run["exit_code"] = None
                    run["classification"] = "superseded_stale_recovered"
                    run["finding_count"] = 0
                    run["error"] = None
                    item["last_error"] = None
                    self.data["history"].append(
                        {
                            "event": "superseded_stale_run_recovered",
                            "slice": name,
                            "run_id": run["id"],
                            "pass": run.get("pass"),
                            "at": now_iso(),
                        }
                    )
                    continue
                if item.get("removed"):
                    run["status"] = "ignored"
                    run["ended_at"] = now_iso()
                    run["exit_code"] = None
                    run["classification"] = "removed_stale_recovered"
                    run["finding_count"] = 0
                    run["error"] = None
                    item["last_error"] = None
                    self.data["history"].append(
                        {
                            "event": "removed_stale_run_recovered",
                            "slice": name,
                            "run_id": run["id"],
                            "pass": run.get("pass"),
                            "at": now_iso(),
                        }
                    )
                    continue
                error = "stale running reservation recovered; review run will be retried"
                run["status"] = "failed"
                run["ended_at"] = now_iso()
                run["exit_code"] = None
                run["classification"] = None
                run["error"] = error
                item["complete"] = False
                item["last_error"] = error
                self.data["last_error"] = {"slice": name, "run_id": run["id"], "error": error, "at": now_iso()}
                append_error(
                    self.review_dir,
                    f"stale running review recovered for {name}",
                    f"Slice: {name}\nOutput: {run.get('output_file')}\nError: {error}",
                )
                self.data["history"].append(
                    {
                        "event": "stale_run_recovered",
                        "slice": name,
                        "run_id": run["id"],
                        "pass": run.get("pass"),
                        "at": now_iso(),
                    }
                )

    def _has_running_runs(self) -> bool:
        return any(
            run.get("status") == "running"
            for item in self.data["slices"].values()
            if not item.get("removed")
            for run in item["runs"]
        )

    def _refresh_completed(self) -> None:
        slices = self.data["slices"].values()
        self.data["completed"] = bool(self.data["slices"]) and all(
            item.get("removed")
            or (item["complete"] and not any(run.get("status") == "running" for run in item["runs"]))
            for item in slices
        )
        if self.data["completed"] and all(item.get("last_error") is None for item in self.data["slices"].values()):
            self.data["last_error"] = None

    def _next_output_file(self, pass_number: int, name: str, runs: Iterable[dict[str, Any]]) -> Path:
        used = {str(run.get("output_file")) for run in runs}
        attempt = sum(1 for run in runs if run.get("pass") == pass_number) + 1
        timestamp = filename_timestamp()
        while True:
            retry_suffix = "" if attempt == 1 else f"-retry{attempt}"
            candidate = self.review_dir / f"{timestamp}-{pass_number}-{name}{retry_suffix}.md"
            if str(candidate) not in used and not candidate.exists():
                return candidate
            attempt += 1


def build_review_command(slice_data: dict[str, Any], output_file: Path) -> tuple[list[str], str | None]:
    task_prompt = build_task_context_prompt(output_file.parent)
    session_target = slice_data.get("session_target")
    target = (
        _native_target_from_session(session_target)
        if session_target is not None
        else slice_data.get("target")
    )
    target_prompt = ""
    if session_target is not None:
        session_target = _validate_session_target(session_target)
        if session_target["kind"] == "uncommitted":
            target_prompt = "Review the current staged, unstaged, and untracked changes.\n"
        elif session_target["kind"] == "base":
            target_prompt = (
                f"Review the current branch against base {session_target['value']}, "
                f"equivalent to `git diff {session_target['value']}...HEAD`.\n"
            )
        else:
            target_prompt = f"Review the changes introduced by commit {session_target['value']}.\n"
    elif target is not None:
        if target.get("uncommitted") is True:
            target_prompt = "Review the current staged, unstaged, and untracked changes.\n"
        elif isinstance(target.get("base"), str):
            target_prompt = (
                f"Review the current branch against base {target['base']}, "
                f"equivalent to `git diff {target['base']}...HEAD`.\n"
            )
        elif isinstance(target.get("commit"), str):
            target_prompt = f"Review the changes introduced by commit {target['commit']}.\n"
        else:
            raise ReviewStateError("slice target is invalid")

    slice_prompt = (
        slice_data["prompt"]
        if slice_data["mode"] == "prompt"
        else "Review this target comprehensively for actionable defects."
    )
    prompt = f"{task_prompt}{target_prompt}\nSlice instructions:\n{slice_prompt}"
    profile = ResolvedProfile(
        harness=slice_data.get("harness", "codex"),
        harness_source=slice_data.get("harness_source", "built-in-default"),
        model=slice_data.get("model"),
        model_source=slice_data.get(
            "model_source",
            "harness-default" if slice_data.get("model") is None else "slice-override",
        ),
        reasoning=slice_data.get("reasoning"),
        reasoning_source=slice_data.get(
            "reasoning_source",
            "harness-default"
            if slice_data.get("reasoning") is None
            else "slice-override",
        ),
    )
    invocation = get_harness(profile.harness).review_invocation(
        prompt=prompt,
        output_file=output_file,
        profile=profile,
    )
    return invocation.command, invocation.input_text


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _process_key(pid: int) -> str | None:
    stat_path = Path("/proc") / str(pid) / "stat"
    try:
        stat = stat_path.read_text(encoding="utf-8")
    except OSError:
        try:
            proc = subprocess.run(
                ["ps", "-o", "lstart=", "-p", str(pid)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except OSError:
            return None
        if proc.returncode == 0 and proc.stdout.strip():
            return f"{pid}:{proc.stdout.strip()}"
        return None
    fields = stat.rsplit(") ", 1)
    if len(fields) != 2:
        return None
    parts = fields[1].split()
    if len(parts) < 20:
        return None
    return f"{pid}:{parts[19]}"


def _running_reservation_is_active(run: dict[str, Any]) -> bool:
    runner_pid = run.get("runner_pid")
    if not isinstance(runner_pid, int) or not _pid_is_alive(runner_pid):
        return False

    runner_key = run.get("runner_key")
    if isinstance(runner_key, str) and runner_key:
        current_key = _process_key(runner_pid)
        return current_key is not None and runner_key == current_key

    started_at = run.get("started_at")
    return isinstance(started_at, str) and parse_iso(started_at) is not None


Runner = Callable[[list[str], Path, str | None, Path, dict[str, Any]], subprocess.CompletedProcess[str]]


def default_runner(
    cmd: list[str],
    cwd: Path,
    input_text: str | None,
    output_file: Path,
    slice_data: dict[str, Any],
) -> subprocess.CompletedProcess[str]:
    del output_file
    timeout = slice_data.get("_child_timeout_seconds") or None
    stdout_log = Path(slice_data["_stdout_log"])
    stderr_log = Path(slice_data["_stderr_log"])
    stdout_log.parent.mkdir(parents=True, exist_ok=True)
    stderr_log.parent.mkdir(parents=True, exist_ok=True)
    with stdout_log.open("w", encoding="utf-8") as out_fh, stderr_log.open("w", encoding="utf-8") as err_fh:
        return subprocess.run(
            cmd,
            cwd=cwd,
            input=input_text,
            text=True,
            stdout=out_fh,
            stderr=err_fh,
            timeout=timeout,
            check=False,
        )


def _log_paths(review_dir: Path, reservation: Reservation) -> tuple[Path, Path]:
    log_dir = review_dir / "_logs"
    safe_slice = re.sub(r"[^a-zA-Z0-9._-]+", "-", reservation.slice_name)
    prefix = f"{reservation.run_id}-{reservation.pass_number}-{safe_slice}"
    return log_dir / f"{prefix}.stdout.log", log_dir / f"{prefix}.stderr.log"


def _write_completed_process_logs(proc: subprocess.CompletedProcess[str], stdout_log: Path, stderr_log: Path) -> None:
    stdout_log.parent.mkdir(parents=True, exist_ok=True)
    if proc.stdout:
        stdout_log.write_text(str(proc.stdout), encoding="utf-8")
    else:
        stdout_log.touch(exist_ok=True)
    if proc.stderr:
        stderr_log.write_text(str(proc.stderr), encoding="utf-8")
    else:
        stderr_log.touch(exist_ok=True)


def run_reserved_review(
    reservation: Reservation,
    command_runner: Runner,
    child_timeout_seconds: float | None = None,
) -> ReviewExecution:
    stdout_log, stderr_log = _log_paths(reservation.output_file.parent, reservation)
    slice_data = json.loads(json.dumps(reservation.slice_data))
    slice_data["_stdout_log"] = str(stdout_log)
    slice_data["_stderr_log"] = str(stderr_log)
    slice_data["_child_timeout_seconds"] = child_timeout_seconds
    enriched = Reservation(
        run_id=reservation.run_id,
        slice_name=reservation.slice_name,
        pass_number=reservation.pass_number,
        output_file=reservation.output_file,
        slice_data=slice_data,
    )
    cmd, input_text = build_review_command(slice_data, enriched.output_file)
    try:
        proc = command_runner(
            cmd,
            Path(slice_data["cwd"]),
            input_text,
            enriched.output_file,
            slice_data,
        )
        _write_completed_process_logs(proc, stdout_log, stderr_log)
        if proc.returncode == 0:
            try:
                get_harness(slice_data.get("harness", "codex")).materialize_review_result(
                    stdout_log=stdout_log,
                    output_file=enriched.output_file,
                )
            except (HarnessError, OSError, UnicodeError) as exc:
                _append_runner_error(stderr_log, str(exc))
                proc = subprocess.CompletedProcess(
                    cmd,
                    1,
                    proc.stdout,
                    proc.stderr,
                )
    except subprocess.TimeoutExpired as exc:
        timeout_msg = f"review command timed out after {exc.timeout} seconds"
        proc = subprocess.CompletedProcess(cmd, 124, exc.stdout or "", exc.stderr or "")
        _write_completed_process_logs(proc, stdout_log, stderr_log)
        with stderr_log.open("a", encoding="utf-8") as fh:
            if stderr_log.stat().st_size:
                fh.write("\n")
            fh.write(f"[runner] {timeout_msg}\n")
        return ReviewExecution(reservation=enriched, proc=proc, stdout_log=stdout_log, stderr_log=stderr_log, timed_out=True)
    except OSError as exc:
        proc = subprocess.CompletedProcess(cmd, 1, "", str(exc))
        _write_completed_process_logs(proc, stdout_log, stderr_log)
        return ReviewExecution(reservation=enriched, proc=proc, stdout_log=stdout_log, stderr_log=stderr_log, launch_error=exc)
    return ReviewExecution(reservation=enriched, proc=proc, stdout_log=stdout_log, stderr_log=stderr_log)


def _append_runner_error(stderr_log: Path, error: str) -> None:
    try:
        with stderr_log.open("a", encoding="utf-8") as fh:
            if stderr_log.stat().st_size:
                fh.write("\n")
            fh.write(f"[runner] {error}\n")
    except (OSError, UnicodeError):
        pass


def evaluate_completed_process(
    review_dir: Path,
    reservation: Reservation,
    proc: subprocess.CompletedProcess[str],
    *,
    stdout_log: Path,
    stderr_log: Path,
    timed_out: bool = False,
) -> tuple[str, str | None, list[dict[str, Any]] | None, str | None]:
    output_file = reservation.output_file
    if timed_out:
        error = (
            f"Slice: {reservation.slice_name}\n"
            f"Output: {output_file}\n"
            f"Exit code: {proc.returncode}\n"
            f"stdout log: {stdout_log}\n"
            f"stderr log: {stderr_log}\n"
            "Error: review command timed out"
        )
        append_error(review_dir, f"timed out review for {reservation.slice_name}", error)
        return "timeout", None, None, "review command timed out"
    if proc.returncode != 0:
        error = (
            f"Slice: {reservation.slice_name}\n"
            f"Output: {output_file}\n"
            f"Exit code: {proc.returncode}\n"
            f"stdout log: {stdout_log}\n"
            f"stderr log: {stderr_log}"
        )
        append_error(review_dir, f"failed review for {reservation.slice_name}", error)
        return "failed", None, None, f"review command exited with {proc.returncode}"

    try:
        text = output_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        error = f"Slice: {reservation.slice_name}\nOutput: {output_file}\nError: {exc}"
        append_error(review_dir, f"unreadable review output for {reservation.slice_name}", error)
        message = f"review output is unreadable: {exc}"
        _write_failure_review_artifact(reservation, message)
        return "failed", None, None, message

    if not text.strip():
        error = f"Slice: {reservation.slice_name}\nOutput: {output_file}\nError: empty review output"
        append_error(review_dir, f"empty review output for {reservation.slice_name}", error)
        message = "review output is empty"
        _write_failure_review_artifact(reservation, message)
        return "failed", None, None, message

    try:
        findings = parse_review_result(text)
    except ReviewResultError as exc:
        error = f"Slice: {reservation.slice_name}\nOutput: {output_file}\nError: {exc}"
        append_error(
            review_dir,
            f"invalid review output for {reservation.slice_name}",
            error,
        )
        _append_raw_review_output(stderr_log, text)
        _write_failure_review_artifact(reservation, str(exc))
        return "failed", None, None, str(exc)
    classification = "findings" if findings else "no_findings"
    status = "findings" if findings else "no_findings"
    return status, classification, findings, None


def _append_raw_review_output(stderr_log: Path, text: str) -> None:
    try:
        with stderr_log.open("a", encoding="utf-8") as fh:
            if stderr_log.stat().st_size:
                fh.write("\n")
            fh.write("[runner] raw invalid review output follows\n")
            fh.write(text)
            if not text.endswith("\n"):
                fh.write("\n")
    except (OSError, UnicodeError):
        pass


def _write_failure_review_artifact(
    reservation: Reservation,
    error: str,
) -> None:
    try:
        _atomic_write_text(
            reservation.output_file,
            render_review_failure_markdown(
                error,
                harness=reservation.slice_data.get("harness", "codex"),
                harness_source=reservation.slice_data.get(
                    "harness_source", "built-in-default"
                ),
                model=reservation.slice_data.get("model"),
                model_source=reservation.slice_data["model_source"],
                reasoning=reservation.slice_data.get("reasoning"),
                reasoning_source=reservation.slice_data["reasoning_source"],
            ),
        )
    except (OSError, UnicodeError):
        pass


def _relative_path(path: Path, *, base: Path | None = None) -> str:
    resolved = path.resolve()
    base = Path.cwd().resolve() if base is None else base.resolve()
    try:
        return resolved.relative_to(base).as_posix()
    except ValueError:
        return resolved.as_posix()


def _remaining_count(state: ReviewState) -> int:
    return sum(
        1
        for item in state.data["slices"].values()
        if not item.get("complete") and not item.get("removed")
    )


def _summary(
    review_dir: Path,
    *,
    status: str,
    ok: bool,
    ran: int,
    remaining: int,
    out_records: list[dict[str, Any]],
    err_records: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    ordered_errors = (
        sorted(err_records, key=lambda rec: (rec.get("p", 0), rec.get("s", ""), rec.get("f", "")))
        if err_records
        else None
    )
    return {
        "dir": _relative_path(review_dir),
        "err": ordered_errors,
        "ok": ok,
        "out": sorted(out_records, key=lambda rec: (rec["p"], rec["s"], rec["f"])),
        "ran": ran,
        "rem": remaining,
        "st": status,
        "state": _relative_path(review_dir / "_state.json"),
        "v": 1,
    }


def _error_record_for_run(
    review_dir: Path,
    *,
    slice_name: str,
    pass_number: int,
    output_file: Path,
    run_id: str,
    status: str,
    code: int | None,
    msg: str | None,
) -> dict[str, Any]:
    reservation = Reservation(
        run_id=run_id,
        slice_name=slice_name,
        pass_number=pass_number,
        output_file=output_file,
        slice_data={},
    )
    stdout_log, stderr_log = _log_paths(review_dir, reservation)
    return {
        "code": code,
        "f": _relative_path(output_file),
        "msg": msg or status,
        "p": pass_number,
        "s": slice_name,
        "st": "timeout" if status == "timeout" else "failed",
        "stderr": _relative_path(stderr_log),
        "stdout": _relative_path(stdout_log),
    }


def _running_run_ids(state: ReviewState) -> set[str]:
    return {
        str(run["id"])
        for item in state.data["slices"].values()
        for run in item.get("runs", [])
        if run.get("status") == "running"
    }


def _await_run_ids(
    review_dir: Path,
    run_ids: set[str],
) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
    while run_ids:
        time.sleep(0.25)
        with ReviewState.locked(review_dir) as state:
            state._recover_stale_running_runs()
            state._refresh_completed()
            state.save()
            remaining = _remaining_count(state)
            selected = [
                (slice_name, run)
                for slice_name, item in state.data["slices"].items()
                for run in item.get("runs", [])
                if run.get("id") in run_ids
            ]
            if any(run.get("status") == "running" for _slice_name, run in selected):
                continue

            errors: list[dict[str, Any]] = []
            out: list[dict[str, Any]] = []
            for slice_name, run in selected:
                status = str(run.get("status"))
                if status in {"failed", "timeout"}:
                    errors.append(
                        _error_record_for_run(
                            review_dir,
                            slice_name=slice_name,
                            pass_number=int(run["pass"]),
                            output_file=Path(run["output_file"]),
                            run_id=str(run["id"]),
                            status=status,
                            code=run.get("exit_code"),
                            msg=run.get("error"),
                        )
                    )
                elif status in {"no_findings", "findings", "ignored"}:
                    out.append(
                        {
                            "f": _relative_path(Path(run["output_file"])),
                            "ids": state._finding_ids_for_run(slice_name, run),
                            "p": int(run["pass"]),
                            "s": slice_name,
                            "st": run.get("classification") or "done",
                        }
                    )
            return remaining, out, errors

    with ReviewState.locked(review_dir) as state:
        state._recover_stale_running_runs()
        state._refresh_completed()
        state.save()
        return _remaining_count(state), [], []


def compact_summary_json(summary: dict[str, Any], *, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(summary, indent=2, sort_keys=True)
    return json.dumps(summary, separators=(",", ":"), sort_keys=True)


def write_summary_json(path: Path, summary: dict[str, Any], *, pretty: bool = False) -> None:
    text = compact_summary_json(summary, pretty=pretty) + "\n"
    _atomic_write_text(path, text)


def _emit_summary(
    summary: dict[str, Any],
    *,
    review_dir: Path,
    summary_json: Path | None,
    no_stdout: bool,
    stdout_json: bool,
    stdout: Any,
    pretty_json: bool,
) -> None:
    default_summary_json = review_dir / "_last-run.json"
    write_summary_json(default_summary_json, summary, pretty=pretty_json)
    if summary_json is not None:
        requested_summary_json = summary_json.resolve()
        if requested_summary_json != default_summary_json.resolve():
            write_summary_json(requested_summary_json, summary, pretty=pretty_json)
    if stdout_json and not no_stdout:
        stdout.write(compact_summary_json(summary, pretty=pretty_json) + "\n")
        stdout.flush()


def await_reviews(
    review_dir: Path,
    *,
    stdout: Any = sys.stdout,
    stdout_json: bool = False,
    pretty_json: bool = False,
) -> tuple[int, dict[str, Any]]:
    """Wait for the currently running review wave without reserving work."""
    review_dir = review_dir.resolve()
    with ReviewState.locked(review_dir) as state:
        run_ids = _running_run_ids(state)
        state._recover_stale_running_runs()
        state._refresh_completed()
        state.save()

    remaining, out_records, err_records = _await_run_ids(review_dir, run_ids)
    ok = not err_records
    if err_records:
        status = "partial" if out_records else "failed"
    elif run_ids:
        status = "partial" if remaining else "done"
    else:
        status = "no_work"
    summary = _summary(
        review_dir,
        status=status,
        ok=ok,
        ran=0,
        remaining=remaining,
        out_records=out_records,
        err_records=err_records,
    )
    if stdout_json:
        stdout.write(compact_summary_json(summary, pretty=pretty_json) + "\n")
        stdout.flush()
    return (0 if ok else 2), summary


def run_reviews(
    review_dir: Path,
    *,
    command_runner: Runner = default_runner,
    stdout: Any = sys.stdout,
    stream_progress: bool = False,
    progress_stream: Any | None = None,
    summary_json: Path | None = None,
    no_stdout: bool = False,
    stdout_json: bool = False,
    pretty_json: bool = False,
    child_timeout_seconds: float | None = None,
) -> tuple[int, dict[str, Any]]:
    if no_stdout and summary_json is None:
        raise ReviewStateError("--no-stdout requires --summary-json")
    if stream_progress and no_stdout:
        raise ReviewStateError("--stream-progress is incompatible with --no-stdout")
    if child_timeout_seconds is not None and child_timeout_seconds <= 0:
        child_timeout_seconds = None

    review_dir = review_dir.resolve()
    build_task_context_prompt(review_dir)
    remaining = 0
    any_running = False
    active_run_ids: set[str] = set()
    with ReviewState.locked(review_dir) as state:
        active_count = sum(
            not item.get("removed")
            for item in state.data["slices"].values()
        )
        if active_count > MAX_ACTIVE_SLICES:
            raise ReviewStateError(
                f"{active_count} active slices exceeds maximum of {MAX_ACTIVE_SLICES}; "
                "remove or consolidate slices before running reviews"
            )
        reservations = state.reserve_eligible()
        state.save()
        remaining = _remaining_count(state)
        any_running = state._has_running_runs()
        if any_running and not reservations:
            active_run_ids = {
                str(run["id"])
                for item in state.data["slices"].values()
                for run in item.get("runs", [])
                if run.get("status") == "running"
            }

    if not reservations:
        if any_running:
            remaining, waited_out, waited_errors = _await_run_ids(review_dir, active_run_ids)
        else:
            waited_errors = []
            waited_out = []
        ok = not waited_errors
        status = "failed" if waited_errors else ("partial" if remaining else "no_work")
        summary = _summary(
            review_dir,
            status=status,
            ok=ok,
            ran=0,
            remaining=remaining,
            out_records=waited_out,
            err_records=waited_errors,
        )
        _emit_summary(
            summary,
            review_dir=review_dir,
            summary_json=summary_json,
            no_stdout=no_stdout,
            stdout_json=stdout_json,
            stdout=stdout,
            pretty_json=pretty_json,
        )
        return (0 if ok else 2), summary

    out_records: list[dict[str, Any]] = []
    err_records: list[dict[str, Any]] = []
    progress_stream = sys.stderr if progress_stream is None else progress_stream
    with ThreadPoolExecutor(max_workers=len(reservations)) as executor:
        futures = [
            executor.submit(run_reserved_review, reservation, command_runner, child_timeout_seconds)
            for reservation in reservations
        ]
        for future in as_completed(futures):
            execution = future.result()
            reservation = execution.reservation
            proc = execution.proc
            if execution.launch_error is not None:
                exc = execution.launch_error
                append_error(
                    review_dir,
                    f"failed to launch review for {reservation.slice_name}",
                    f"Slice: {reservation.slice_name}\nOutput: {reservation.output_file}\nError: {exc}",
                )
                status, classification, findings, error = (
                    "failed",
                    None,
                    None,
                    f"review command failed to launch: {exc}",
                )
            else:
                status, classification, findings, error = evaluate_completed_process(
                    review_dir,
                    reservation,
                    proc,
                    stdout_log=execution.stdout_log,
                    stderr_log=execution.stderr_log,
                    timed_out=execution.timed_out,
                )
            with ReviewState.locked(review_dir) as state:
                completion_applied = state.complete_run(
                    run_id=reservation.run_id,
                    slice_name=reservation.slice_name,
                    status=status,
                    exit_code=proc.returncode,
                    classification=classification,
                    findings=findings,
                    error=error,
                )
                persisted_run = next(
                    run
                    for run in state.data["slices"][reservation.slice_name]["runs"]
                    if run.get("id") == reservation.run_id
                )
                persisted_status = str(persisted_run.get("status"))
                state.save()
                remaining = _remaining_count(state)
            display_status = persisted_status if completion_applied else "skipped-late-completion"
            if stream_progress:
                print(
                    f"{reservation.slice_name}: pass {reservation.pass_number} {display_status} -> {reservation.output_file}",
                    file=progress_stream,
                    flush=True,
                )
            if persisted_status in {"failed", "timeout"}:
                err_record: dict[str, Any] = {
                    "code": proc.returncode,
                    "f": _relative_path(reservation.output_file),
                    "msg": persisted_run.get("error") or error or display_status,
                    "p": reservation.pass_number,
                    "s": reservation.slice_name,
                    "st": "timeout" if persisted_status == "timeout" else "failed",
                    "stderr": _relative_path(execution.stderr_log),
                    "stdout": _relative_path(execution.stdout_log),
                }
                err_records.append(err_record)
            elif persisted_status != "ignored":
                out_records.append(
                    {
                        "f": _relative_path(reservation.output_file),
                        "ids": [
                            finding["id"] for finding in (persisted_run.get("findings") or [])
                        ],
                        "p": reservation.pass_number,
                        "s": reservation.slice_name,
                        "st": persisted_run.get("classification") or "done",
                    }
                )

    ok = not err_records
    with ReviewState.locked(review_dir) as state:
        remaining = _remaining_count(state)
    if err_records:
        top_status = "partial" if out_records else "failed"
    elif remaining:
        top_status = "partial"
    else:
        top_status = "done"
    rc = 0 if ok else 2
    summary = _summary(
        review_dir,
        status=top_status,
        ok=ok,
        ran=len(reservations),
        remaining=remaining,
        out_records=out_records,
        err_records=err_records,
    )
    _emit_summary(
        summary,
        review_dir=review_dir,
        summary_json=summary_json,
        no_stdout=no_stdout,
        stdout_json=stdout_json,
        stdout=stdout,
        pretty_json=pretty_json,
    )
    return rc, summary


def parse_add_slice_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Register one review slice in an initialized review state.")
    parser.add_argument("--review-dir", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--uncommitted", action="store_true")
    parser.add_argument("--base")
    parser.add_argument("--commit")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument(
        "--harness",
        help="Use a specific harness for this slice instead of the configured default.",
    )
    parser.add_argument(
        "--model",
        help="Use a specific model for this slice instead of the configured or harness default.",
    )
    parser.add_argument(
        "--reasoning",
        help=(
            "Use a specific reasoning effort for this slice instead of the "
            "configured or harness default."
        ),
    )
    parser.add_argument("--cwd", type=Path)
    parser.add_argument(
        "--user-directive-file",
        type=Path,
        help="Record the explicit user instruction authorizing this slice.",
    )
    return parser.parse_args(argv)


def add_slice_from_args(args: argparse.Namespace, *, stdin: Any = sys.stdin) -> None:
    native_count = sum(bool(value) for value in (args.uncommitted, args.base, args.commit))
    prompt_requested = args.prompt_file is not None
    if native_count > 1:
        raise ReviewStateError("choose only one native target flag: --uncommitted, --base, or --commit")
    if native_count and prompt_requested:
        raise ReviewStateError("native target flags cannot be combined with --prompt-file")

    target: dict[str, Any] | None = None
    prompt: str | None = None
    mode: str
    if native_count:
        mode = "native"
        if args.uncommitted:
            target = {"uncommitted": True}
        elif args.base:
            target = {"base": args.base}
        else:
            target = {"commit": args.commit}
    else:
        mode = "prompt"
        if args.prompt_file is not None:
            if str(args.prompt_file) == "-":
                prompt = stdin.read()
            else:
                prompt = args.prompt_file.read_text(encoding="utf-8")
        else:
            prompt = stdin.read()

    with ReviewState.locked(args.review_dir) as state:
        from review_config import load_review_config

        session_root = Path(state.data["session"]["root"]).resolve()
        config = load_review_config(session_root)
        cwd = args.cwd.resolve() if args.cwd is not None else session_root
        if not cwd.is_dir():
            raise ReviewStateError(f"slice cwd is not a directory: {cwd}")
        if not _path_is_relative_to(cwd, session_root):
            raise ReviewStateError("slice cwd must remain within the session repository")
        user_directive = (
            args.user_directive_file.read_text(encoding="utf-8")
            if args.user_directive_file is not None
            else None
        )
        try:
            profile = resolve_profile(
                config.slice_default,
                harness=args.harness,
                model=args.model,
                reasoning=args.reasoning,
                override_source="slice-override",
            )
        except HarnessError as exc:
            raise ReviewStateError(str(exc)) from exc
        state.add_slice(
            name=args.name,
            mode=mode,
            target=target,
            prompt=prompt,
            cwd=cwd,
            harness=profile.harness,
            harness_source=profile.harness_source,
            model=profile.model,
            model_source=profile.model_source,
            reasoning=profile.reasoning,
            reasoning_source=profile.reasoning_source,
            source="user" if user_directive is not None else "classifier",
            user_directive=user_directive,
        )
        state.save()
