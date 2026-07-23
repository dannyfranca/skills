#!/usr/bin/env python3
"""Resolve optional multi-shot review settings from the user-to-repository chain."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harnesses import HarnessError, HarnessProfile, get_harness
from review_state import ReviewStateError


CONFIG_FILENAME = "multi-shot-review.toml"
DEFAULT_REVIEW_FILE = "REVIEW"
_SUPPORTED_KEYS = {
    "review_file",
    "classifier",
    "slice_default",
}
_PROFILE_KEYS = {"harness", "model", "reasoning"}
_REVIEW_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class ReviewConfig:
    review_file: str = DEFAULT_REVIEW_FILE
    classifier: HarnessProfile | None = None
    slice_default: HarnessProfile | None = None


def load_review_config(root: Path, *, home: Path | None = None) -> ReviewConfig:
    """Merge `.agents/multi-shot-review.toml` files, with nearer values winning."""

    root = root.resolve()
    home = (Path.home() if home is None else home).resolve()
    merged: dict[str, Any] = {}
    for path in _config_chain(root, home):
        merged.update(_load_config_file(path))
    return ReviewConfig(**merged)


def _config_chain(root: Path, home: Path) -> tuple[Path, ...]:
    locations: list[Path] = [home]
    if root != home:
        try:
            relative = root.relative_to(home)
        except ValueError:
            locations.append(root)
        else:
            current = home
            for part in relative.parts:
                current /= part
                locations.append(current)
    return tuple(location / ".agents" / CONFIG_FILENAME for location in locations)


def _load_config_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if not path.is_file():
        raise ReviewStateError(f"review config is not a file: {path}")
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ReviewStateError(f"could not read review config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ReviewStateError(f"review config must be a TOML table: {path}")
    unknown = set(data) - _SUPPORTED_KEYS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ReviewStateError(f"unsupported review config setting(s) in {path}: {names}")
    validated: dict[str, Any] = {}
    for key, value in data.items():
        if key == "review_file":
            validated[key] = _validate_review_file(path, value)
        else:
            validated[key] = _validate_profile(path, key, value)
    return validated


def _validate_review_file(path: Path, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewStateError(f"review config review_file must be a non-empty string: {path}")
    value = value.strip()
    if (
        not _REVIEW_FILE_RE.fullmatch(value)
        or value.lower().endswith(".md")
        or "/" in value
        or "\\" in value
    ):
        raise ReviewStateError(
            f"review config review_file must be a basename without .md: {path}"
        )
    return value


def _validate_profile(path: Path, key: str, value: Any) -> HarnessProfile:
    if not isinstance(value, dict):
        raise ReviewStateError(f"review config {key} must be a TOML table: {path}")
    unknown = set(value) - _PROFILE_KEYS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ReviewStateError(
            f"unsupported review config {key} setting(s) in {path}: {names}"
        )
    if "harness" not in value:
        raise ReviewStateError(f"review config {key}.harness is required: {path}")
    validated: dict[str, str | None] = {}
    for field in _PROFILE_KEYS:
        field_value = value.get(field)
        if field_value is None and field != "harness":
            validated[field] = None
            continue
        if not isinstance(field_value, str) or not field_value.strip():
            raise ReviewStateError(
                f"review config {key}.{field} must be a non-empty string: {path}"
            )
        validated[field] = field_value.strip()
    try:
        get_harness(str(validated["harness"]))
    except HarnessError as exc:
        raise ReviewStateError(f"review config {key}: {exc}: {path}") from exc
    return HarnessProfile(
        harness=str(validated["harness"]),
        model=validated["model"],
        reasoning=validated["reasoning"],
    )
