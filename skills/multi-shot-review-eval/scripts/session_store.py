from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from diff_size import DiffSize
from eval_errors import EvalError
from review_target import Target, TargetKind
from timestamps import parse_timestamp


SESSION_DIR_RE = re.compile(r"^\d{8}-\d{4}-[0-9a-z]+$")
STATE_FILE = "_state.json"
__all__ = ["Session", "Target", "TargetKind", "load_session", "load_sessions"]
DEFAULT_VARIANT = "default"


@dataclass(frozen=True)
class Session:
    id: str
    directory: Path
    variant: str
    created_at: datetime
    completed: bool
    repository: str
    root: Path | None
    target: Target
    skill_revision: str | None
    target_size: DiffSize | None
    state: dict[str, Any] = field(repr=False, compare=False)

    @property
    def slices(self) -> dict[str, dict[str, Any]]:
        slices = self.state.get("slices")
        return slices if isinstance(slices, dict) else {}

    @property
    def classifications(self) -> list[dict[str, Any]]:
        items = self.state.get("classifications")
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def load_sessions(review_root: Path) -> list[Session]:
    """Every readable session under `<root>/<repository>/<session>`; unreadable ones are skipped.

    The review state writer names a repository `owner/repo` when it has a hosted remote and `repo` otherwise.
    """

    sessions = []
    state_paths = [*review_root.glob(f"*/*/{STATE_FILE}"), *review_root.glob(f"*/*/*/{STATE_FILE}")]
    for state_path in sorted(state_paths):
        if not SESSION_DIR_RE.fullmatch(state_path.parent.name):
            continue
        session = _read_session(review_root, state_path.parent)
        if session is not None:
            sessions.append(session)
    return sessions


def load_session(review_root: Path, session_id: str) -> Session:
    session = _read_session(review_root, review_root / session_id)
    if session is None:
        raise EvalError(f"session is not readable: {session_id}")
    return session


def _read_session(review_root: Path, directory: Path) -> Session | None:
    try:
        state = json.loads((directory / STATE_FILE).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict) or not isinstance(state.get("session"), dict):
        return None
    meta = state["session"]
    created_at = parse_timestamp(meta.get("created_at"))
    if created_at is None:
        return None
    repository = meta.get("repository") or {}
    session_id = directory.relative_to(review_root).as_posix()
    root = meta.get("root")
    return Session(
        id=session_id,
        directory=directory,
        variant=meta.get("variant") or DEFAULT_VARIANT,
        created_at=created_at,
        completed=state.get("completed") is True,
        repository=repository.get("name") or session_id.rsplit("/", 1)[0],
        root=Path(root) if isinstance(root, str) and root else None,
        target=_target(meta.get("target")),
        skill_revision=meta.get("skill_revision") or None,
        target_size=_target_size(meta.get("target_size")),
        state=state,
    )


def _target(value: Any) -> Target:
    if not isinstance(value, dict):
        return Target(TargetKind.UNKNOWN)
    try:
        kind = TargetKind(value.get("kind"))
    except ValueError:
        kind = TargetKind.UNKNOWN
    target_value = value.get("value")
    return Target(kind, target_value if isinstance(target_value, str) else None)


def _target_size(value: Any) -> DiffSize | None:
    if not isinstance(value, dict):
        return None
    try:
        return DiffSize(files=int(value["files"]), added=int(value["added"]), deleted=int(value["deleted"]))
    except (KeyError, TypeError, ValueError):
        return None
