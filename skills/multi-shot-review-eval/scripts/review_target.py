from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TargetKind(StrEnum):
    UNCOMMITTED = "uncommitted"
    BASE = "base"
    COMMIT = "commit"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Target:
    kind: TargetKind
    value: str | None = None
