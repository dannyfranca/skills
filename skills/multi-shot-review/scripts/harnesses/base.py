"""Shared harness interface and execution profile types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


DEFAULT_HARNESS = "codex"


class HarnessError(ValueError):
    """Raised when a harness selection or result envelope is invalid."""


@dataclass(frozen=True)
class HarnessProfile:
    """User-configurable harness choices for one execution role."""

    harness: str
    model: str | None = None
    reasoning: str | None = None


@dataclass(frozen=True)
class ResolvedProfile:
    """A fully resolved, auditable harness execution profile."""

    harness: str
    harness_source: str
    model: str | None
    model_source: str
    reasoning: str | None
    reasoning_source: str


@dataclass(frozen=True)
class Invocation:
    """A non-interactive harness process invocation."""

    command: list[str]
    input_text: str | None = None


class ReviewHarness(ABC):
    """Deep module interface for one supported agent harness."""

    name: str

    @abstractmethod
    def classifier_invocation(
        self,
        *,
        prompt: str,
        review_dir: Path,
        profile: ResolvedProfile,
        add_slice_script: Path,
        remove_slice_script: Path,
    ) -> Invocation:
        """Build one classifier invocation."""

    @abstractmethod
    def review_invocation(
        self,
        *,
        prompt: str,
        output_file: Path,
        profile: ResolvedProfile,
    ) -> Invocation:
        """Build one reviewer invocation."""

    def materialize_review_result(
        self,
        *,
        stdout_log: Path,
        output_file: Path,
    ) -> None:
        """Normalize a successful harness result into the shared result document."""


def non_empty(value: str, label: str) -> str:
    """Return one trimmed, non-empty profile value."""

    if not isinstance(value, str) or not value.strip():
        raise HarnessError(f"{label} must be a non-empty string")
    return value.strip()
