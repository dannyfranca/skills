#!/usr/bin/env python3
"""Resolve optional multi-shot review settings from the user-to-repository chain."""

from __future__ import annotations

import random
import re
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from harnesses import HarnessError, HarnessProfile, get_harness
from review_state import ReviewStateError


CONFIG_FILENAME = "multi-shot-review.toml"
DEFAULT_REVIEW_FILE = "REVIEW"
DEFAULT_MAX_PASSES = 3
DEFAULT_SHOTS = 1
DEFAULT_SHOT_PASSES = 1
SHOT_PASSES_ALWAYS = "always"
DEFAULT_VARIANT = "default"
_SETTING_KEYS = {
    "review_file",
    "max_passes",
    "shots",
    "shot_passes",
    "classifier",
    "slice_default",
    "judge",
}
_EXPERIMENT_KEYS = {"variants", "variant"}
_SUPPORTED_KEYS = _SETTING_KEYS | _EXPERIMENT_KEYS
_PROFILE_KEYS = {"harness", "model", "reasoning"}
_PROFILE_SETTINGS = {"classifier", "slice_default", "judge"}
_REVIEW_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
VARIANT_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@dataclass(frozen=True)
class ReviewConfig:
    review_file: str = DEFAULT_REVIEW_FILE
    max_passes: int = DEFAULT_MAX_PASSES
    shots: int = DEFAULT_SHOTS
    shot_passes: int | str = DEFAULT_SHOT_PASSES
    classifier: HarnessProfile | None = None
    slice_default: HarnessProfile | None = None
    judge: HarnessProfile | None = None
    variant: str = DEFAULT_VARIANT

    @property
    def judge_profile(self) -> HarnessProfile | None:
        """The judge shares the classifier profile unless configured on its own."""

        return self.classifier if self.judge is None else self.judge

    def to_snapshot(self) -> dict[str, Any]:
        """Effective settings without the variant tag, which the session stores on its own."""

        snapshot: dict[str, Any] = {}
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name == "variant":
                continue
            if isinstance(value, HarnessProfile):
                value = {"harness": value.harness, "model": value.model, "reasoning": value.reasoning}
            snapshot[field.name] = value
        return snapshot

    @classmethod
    def from_snapshot(cls, snapshot: Any, *, variant: Any) -> "ReviewConfig":
        """Rebuild the session config with the same validation as a config file."""

        owner = "session config snapshot"
        expected = {field.name for field in fields(cls)} - {"variant"}
        if not isinstance(snapshot, dict) or set(snapshot) != expected:
            raise ReviewStateError(f"invalid {owner}")
        if not isinstance(variant, str) or not VARIANT_TAG_RE.fullmatch(variant):
            raise ReviewStateError(f"invalid session variant: {variant!r}")
        settings = {
            key: (
                {name: part for name, part in value.items() if part is not None}
                if key in _PROFILE_SETTINGS and isinstance(value, dict)
                else value
            )
            for key, value in snapshot.items()
            if value is not None
        }
        return cls(**_validate_settings(owner, settings), variant=variant)


def load_review_config(
    root: Path,
    *,
    home: Path | None = None,
    variant: str | None = None,
    rng: random.Random | None = None,
) -> ReviewConfig:
    """Merge `.agents/multi-shot-review.toml` files, nearer values winning, then apply one variant.

    Without `variant`, the tag comes from a weighted draw. The nearest file that declares an
    experiment owns all of it, so a repository experiment never mixes with a parent one.
    """

    root = root.resolve()
    home = (Path.home() if home is None else home).resolve()
    merged: dict[str, Any] = {}
    experiment = _NO_EXPERIMENT
    for path in _config_chain(root, home):
        settings, file_experiment = _load_config_file(path)
        merged.update(settings)
        if file_experiment is not None:
            experiment = file_experiment
    tag = experiment.draw(rng or random.Random()) if variant is None else experiment.require(variant)
    merged.update(experiment.bodies.get(tag, {}))
    return ReviewConfig(**merged, variant=tag)


@dataclass(frozen=True)
class _Experiment:
    weights: dict[str, int]
    bodies: dict[str, dict[str, Any]]

    def draw(self, rng: random.Random) -> str:
        # Integer arithmetic keeps any valid TOML weight exact; float weights overflow or drop
        # small variants when the total is huge.
        ticket = rng.randrange(sum(self.weights.values()))
        for tag in sorted(self.weights):
            ticket -= self.weights[tag]
            if ticket < 0:
                return tag
        raise AssertionError("weighted draw exhausted all variants")

    def require(self, tag: str) -> str:
        if tag not in self.weights:
            known = ", ".join(sorted(self.weights))
            raise ReviewStateError(f"unknown review config variant {tag!r}; known variants: {known}")
        return tag


_NO_EXPERIMENT = _Experiment(weights={DEFAULT_VARIANT: 1}, bodies={})


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


def _load_config_file(path: Path) -> tuple[dict[str, Any], _Experiment | None]:
    if not path.exists():
        return {}, None
    if not path.is_file():
        raise ReviewStateError(f"review config is not a file: {path}")
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    # ValueError also covers integers beyond Python's digit limit, which tomllib does not wrap.
    except (OSError, ValueError) as exc:
        raise ReviewStateError(f"could not read review config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ReviewStateError(f"review config must be a TOML table: {path}")
    unknown = set(data) - _SUPPORTED_KEYS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ReviewStateError(f"unsupported review config setting(s) in {path}: {names}")
    settings = _validate_settings(path, {k: v for k, v in data.items() if k in _SETTING_KEYS})
    if not _EXPERIMENT_KEYS & set(data):
        return settings, None
    return settings, _validate_experiment(path, data.get("variants"), data.get("variant"))


def _validate_experiment(path: Path | str, weights: Any, bodies: Any) -> _Experiment:
    weights = {} if weights is None else weights
    bodies = {} if bodies is None else bodies
    if not isinstance(weights, dict):
        raise ReviewStateError(f"review config variants must be a TOML table: {path}")
    if not isinstance(bodies, dict):
        raise ReviewStateError(f"review config variant must be a TOML table: {path}")
    for tag in {*weights, *bodies}:
        if not VARIANT_TAG_RE.fullmatch(tag):
            raise ReviewStateError(
                f"review config variant tag {tag!r} must match {VARIANT_TAG_RE.pattern}: {path}"
            )
    if DEFAULT_VARIANT in bodies:
        raise ReviewStateError(
            f"review config variant.{DEFAULT_VARIANT} is reserved; the main settings are the "
            f"{DEFAULT_VARIANT} variant: {path}"
        )
    for tag, weight in weights.items():
        if isinstance(weight, bool) or not isinstance(weight, int) or weight < 0:
            raise ReviewStateError(
                f"review config variants.{tag} must be a non-negative integer: {path}"
            )
        if tag != DEFAULT_VARIANT and tag not in bodies:
            raise ReviewStateError(
                f"review config variants.{tag} has no [variant.{tag}] table: {path}"
            )
    validated_bodies: dict[str, dict[str, Any]] = {}
    for tag, body in bodies.items():
        if not isinstance(body, dict):
            raise ReviewStateError(f"review config variant.{tag} must be a TOML table: {path}")
        unknown = set(body) - _SETTING_KEYS
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ReviewStateError(
                f"unsupported review config variant.{tag} setting(s) in {path}: {names}"
            )
        validated_bodies[tag] = _validate_settings(path, body)
    # The default and every declared body join the draw at weight 1 unless weighted explicitly,
    # so an equal split needs no [variants] table at all.
    experiment = _Experiment(
        weights={DEFAULT_VARIANT: 1, **{tag: 1 for tag in validated_bodies}, **weights},
        bodies=validated_bodies,
    )
    if sum(experiment.weights.values()) == 0:
        raise ReviewStateError(f"review config variant weights must not all be zero: {path}")
    return experiment


def _validate_settings(path: Path | str, data: dict[str, Any]) -> dict[str, Any]:
    validated: dict[str, Any] = {}
    for key, value in data.items():
        if key == "review_file":
            validated[key] = _validate_review_file(path, value)
        elif key in {"max_passes", "shots"}:
            validated[key] = _validate_positive_int(path, key, value)
        elif key == "shot_passes":
            validated[key] = _validate_shot_passes(path, value)
        else:
            validated[key] = _validate_profile(path, key, value)
    return validated


def _validate_review_file(path: Path | str, value: Any) -> str:
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


def _validate_positive_int(path: Path | str, key: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ReviewStateError(
            f"review config {key} must be a positive integer: {path}"
        )
    return value


def _validate_shot_passes(path: Path | str, value: Any) -> int | str:
    if value == SHOT_PASSES_ALWAYS:
        return SHOT_PASSES_ALWAYS
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ReviewStateError(
            f'review config shot_passes must be a positive integer or "{SHOT_PASSES_ALWAYS}": {path}'
        )
    return value


def parse_shot_passes(value: str) -> int | str:
    """Parse a CLI `--shot-passes` value with the same rules as the config key."""

    text = value.strip()
    if text == SHOT_PASSES_ALWAYS:
        return SHOT_PASSES_ALWAYS
    if text.isdigit() and int(text) >= 1:
        return int(text)
    raise ValueError(f'expected a positive integer or "{SHOT_PASSES_ALWAYS}", got {value!r}')


def _validate_profile(path: Path | str, key: str, value: Any) -> HarnessProfile:
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
