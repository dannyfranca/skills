#!/usr/bin/env python3
"""Resolve optional multi-shot review settings from the user-to-repository chain."""

from __future__ import annotations

import json
import os
import random
import re
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from harnesses import DEFAULT_HARNESS, HarnessError, HarnessProfile, get_harness
from review_state import ReviewStateError


CONFIG_FILENAME = "multi-shot-review.toml"
DEFAULT_REVIEW_FILE = "REVIEW"
DEFAULT_MAX_PASSES = 3
# Sessions created before reviewer lanes stored these settings in their config snapshot.
LEGACY_SHOT_KEYS = ("shots", "shot_passes")
LEGACY_DEFAULT_SHOT_PASSES = 1
SHOT_PASSES_ALWAYS = "always"
MAX_PARALLEL_AUTO = "auto"
DEFAULT_MAX_PARALLEL = MAX_PARALLEL_AUTO
# Reviewers mostly wait on model APIs, but they also run builds and test suites in the review
# target. An unbounded wave of them once filled 62G of RAM and 62G of swap, so auto keeps room
# for one such command per child.
AUTO_MEMORY_PER_CHILD = 2 * 1024**3
DEFAULT_VARIANT = "default"
_SETTING_KEYS = {
    "review_file",
    "review_root",
    "max_passes",
    "reviewers",
    "max_parallel",
    "classifier",
    "slice_default",
    "judge",
}
_EXPERIMENT_KEYS = {"variants", "variant"}
# multi-shot-review-eval shares this config chain and owns the [eval] table.
_EVAL_KEYS = {"eval"}
_SUPPORTED_KEYS = _SETTING_KEYS | _EXPERIMENT_KEYS | _EVAL_KEYS
_PROFILE_KEYS = {"harness", "model", "reasoning"}
_PROFILE_SETTINGS = {"classifier", "slice_default", "judge"}
_LANE_STRING_KEYS = {"name"}
_LANE_POSITIVE_INT_KEYS = {"from_pass", "passes", "shots"}
_LANE_BOOL_KEYS = {"required", "retire_when_clean"}
_LANE_KEYS = _PROFILE_KEYS | _LANE_STRING_KEYS | _LANE_POSITIVE_INT_KEYS | _LANE_BOOL_KEYS
# A snapshot stores unset optional lane fields as null; a null anywhere else is corruption.
_OPTIONAL_LANE_KEYS = {"model", "reasoning", "name", "passes"}
_REVIEW_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
VARIANT_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SESSION_OWNED_FIELDS = {"variant", "review_root"}


@dataclass(frozen=True)
class ReviewerLane:
    """One reviewer profile and the passes of a slice definition where it runs."""

    harness: str
    model: str | None = None
    reasoning: str | None = None
    name: str | None = None
    from_pass: int = 1
    passes: int | None = None
    shots: int = 1
    required: bool = False
    retire_when_clean: bool = True

    @property
    def profile(self) -> HarnessProfile:
        return HarnessProfile(harness=self.harness, model=self.model, reasoning=self.reasoning)


@dataclass(frozen=True)
class ReviewConfig:
    review_file: str = DEFAULT_REVIEW_FILE
    review_root: Path = field(default_factory=lambda: Path.home() / ".reviews")
    max_passes: int = DEFAULT_MAX_PASSES
    reviewers: tuple[ReviewerLane, ...] = ()
    max_parallel: int | str = DEFAULT_MAX_PARALLEL
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
        for config_field in fields(self):
            value = getattr(self, config_field.name)
            # The session already records its review_dir, and sessions created before the
            # review_root setting existed must stay loadable.
            if config_field.name in _SESSION_OWNED_FIELDS:
                continue
            if isinstance(value, HarnessProfile):
                value = {"harness": value.harness, "model": value.model, "reasoning": value.reasoning}
            elif config_field.name == "reviewers":
                value = [asdict(lane) for lane in value]
            snapshot[config_field.name] = value
        return snapshot

    @classmethod
    def from_snapshot(cls, snapshot: Any, *, variant: Any) -> "ReviewConfig":
        """Rebuild the session config with the same validation as a config file."""

        owner = "session config snapshot"
        expected = {config_field.name for config_field in fields(cls)} - _SESSION_OWNED_FIELDS
        if isinstance(snapshot, dict):
            # Sessions created before max_parallel or reviewer lanes existed must stay loadable.
            snapshot = _lanes_from_legacy_snapshot({"max_parallel": DEFAULT_MAX_PARALLEL, **snapshot})
        if not isinstance(snapshot, dict) or set(snapshot) != expected:
            raise ReviewStateError(f"invalid {owner}")
        if not isinstance(variant, str) or not VARIANT_TAG_RE.fullmatch(variant):
            raise ReviewStateError(f"invalid session variant: {variant!r}")
        settings = {
            key: (
                _without_nulls(value)
                if key in _PROFILE_SETTINGS
                else [_without_optional_lane_nulls(lane) for lane in value]
                if key == "reviewers" and isinstance(value, list)
                else value
            )
            for key, value in snapshot.items()
            if value is not None
        }
        return cls(**_validate_settings(owner, settings), variant=variant)


def _without_nulls(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {name: part for name, part in value.items() if part is not None}


def _without_optional_lane_nulls(lane: Any) -> Any:
    if not isinstance(lane, dict):
        return lane
    return {
        name: part
        for name, part in lane.items()
        if part is not None or name not in _OPTIONAL_LANE_KEYS
    }


def _lanes_from_legacy_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    if "reviewers" in snapshot or not any(key in snapshot for key in LEGACY_SHOT_KEYS):
        return snapshot
    snapshot = dict(snapshot)
    shots = snapshot.pop("shots", 1)
    shot_passes = snapshot.pop("shot_passes", LEGACY_DEFAULT_SHOT_PASSES)
    # A one-shot lane is dropped as the default, so a malformed schedule would silently load as it.
    if not _is_positive_int(shots) or not (
        shot_passes == SHOT_PASSES_ALWAYS or _is_positive_int(shot_passes)
    ):
        raise ReviewStateError("invalid session config snapshot")
    lane = _legacy_lane(snapshot.get("slice_default"), shots, shot_passes)
    snapshot["reviewers"] = [] if lane is None else [lane]
    return snapshot


def _profile_lane(profile: Any) -> dict[str, Any]:
    if isinstance(profile, HarnessProfile):
        profile = asdict(profile)
    profile = profile if isinstance(profile, dict) else {}
    lane: dict[str, Any] = {"harness": profile.get("harness") or DEFAULT_HARNESS}
    lane.update({key: profile[key] for key in ("model", "reasoning") if profile.get(key) is not None})
    return lane


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _legacy_lane(profile: Any, shots: Any, shot_passes: Any) -> dict[str, Any] | None:
    """The lane that keeps the old `shots`/`shot_passes` schedule, or None for the default lane."""

    if shots == 1:
        return None
    lane = _profile_lane(profile)
    lane["shots"] = shots
    # Later passes kept one shot, which the last lane with findings now gives.
    if shot_passes != SHOT_PASSES_ALWAYS:
        lane["passes"] = shot_passes
    return lane


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
    legacy = _LegacyShots()
    for path in _config_chain(root, home):
        settings, file_experiment, file_legacy = _load_config_file(path)
        merged.update(settings)
        legacy.add(path, file_legacy, owns_experiment=file_experiment is not None)
        if file_experiment is not None:
            experiment = file_experiment
    legacy.reject(merged, experiment)
    tag = experiment.draw(rng or random.Random()) if variant is None else experiment.require(variant)
    merged.update(experiment.bodies.get(tag, {}))
    return ReviewConfig(**merged, variant=tag)


def load_explicit_review_config(path: Path, *, variant: str | None = None) -> ReviewConfig:
    settings, experiment, file_legacy = _load_config_file(path)
    if not path.is_file():
        raise ReviewStateError(f"explicit review config is not a file: {path}")
    legacy = _LegacyShots()
    legacy.add(path, file_legacy, owns_experiment=experiment is not None)
    legacy.reject(settings, experiment or _NO_EXPERIMENT)
    experiment = experiment or _NO_EXPERIMENT
    if variant is None and experiment != _NO_EXPERIMENT:
        raise ReviewStateError("explicit experiment config requires a variant")
    tag = experiment.require(variant or DEFAULT_VARIANT)
    settings.update(experiment.bodies.get(tag, {}))
    return ReviewConfig(**settings, variant=tag)


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


def load_reviewers_file(path: Path) -> tuple[ReviewerLane, ...]:
    """Read the `[[reviewers]]` lanes that replace the configured lanes for one slice."""

    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, ValueError) as exc:
        raise ReviewStateError(f"could not read reviewers file {path}: {exc}") from exc
    unknown = set(data) - {"reviewers"}
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ReviewStateError(f"reviewers file accepts only [[reviewers]] tables: {path}: {names}")
    lanes = _validate_reviewers(path, data.get("reviewers"))
    if not lanes:
        raise ReviewStateError(f"reviewers file must declare at least one [[reviewers]] lane: {path}")
    return lanes


def _load_config_file(
    path: Path,
) -> tuple[dict[str, Any], _Experiment | None, dict[str, dict[str, Any]]]:
    if not path.exists():
        return {}, None, {}
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
    legacy = {DEFAULT_VARIANT: _pop_legacy_shot_settings(path, data, prefix="")}
    if isinstance(data.get("variant"), dict):
        for tag, body in data["variant"].items():
            if isinstance(body, dict):
                legacy[tag] = _pop_legacy_shot_settings(path, body, prefix=f"variant.{tag}.")
    legacy = {tag: keys for tag, keys in legacy.items() if keys}
    unknown = set(data) - _SUPPORTED_KEYS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ReviewStateError(f"unsupported review config setting(s) in {path}: {names}")
    settings = _validate_settings(path, {k: v for k, v in data.items() if k in _SETTING_KEYS})
    if not _EXPERIMENT_KEYS & set(data):
        return settings, None, legacy
    return settings, _validate_experiment(path, data.get("variants"), data.get("variant")), legacy


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


def _pop_legacy_shot_settings(path: Path, data: dict[str, Any], *, prefix: str) -> dict[str, Any]:
    legacy = {key: data.pop(key) for key in LEGACY_SHOT_KEYS if key in data}
    if "shots" in legacy and not _is_positive_int(legacy["shots"]):
        raise ReviewStateError(f"review config {prefix}shots must be a positive integer: {path}")
    passes = legacy.get("shot_passes", SHOT_PASSES_ALWAYS)
    if passes != SHOT_PASSES_ALWAYS and not _is_positive_int(passes):
        raise ReviewStateError(
            f"review config {prefix}shot_passes must be a positive integer or "
            f'"{SHOT_PASSES_ALWAYS}": {path}'
        )
    return legacy


class _LegacyShots:
    """Former `shots`/`shot_passes` keys found in a config chain, rejected as one migration.

    The old keys merged across the chain independently of `[slice_default]`, so only the merged
    chain shows which lanes keep the same reviews. A per-file example can copy a profile that a
    nearer config overrides.
    """

    def __init__(self) -> None:
        self.sources: list[tuple[Path, str]] = []
        self.main: dict[str, Any] = {}
        self.variants: dict[str, dict[str, Any]] = {}
        self.experiment_owner: Path | None = None

    def add(self, path: Path, legacy: dict[str, dict[str, Any]], *, owns_experiment: bool) -> None:
        for tag, keys in legacy.items():
            prefix = "" if tag == DEFAULT_VARIANT else f"variant.{tag}."
            self.sources.extend((path, prefix + key) for key in keys)
        self.main.update(legacy.get(DEFAULT_VARIANT, {}))
        # The nearest file that declares an experiment owns all of its variant bodies.
        if owns_experiment:
            self.variants = {tag: keys for tag, keys in legacy.items() if tag != DEFAULT_VARIANT}
            self.experiment_owner = path

    def reject(self, merged: dict[str, Any], experiment: _Experiment) -> None:
        if not self.sources:
            return
        main_tables, variant_tables = self._lanes(merged, experiment)
        removals = "\n".join(f"- {key} in {path}" for path, key in self.sources)
        message = (
            "review config shots and shot_passes were replaced by [[reviewers]] lanes.\n"
            f"Remove:\n{removals}"
        )
        targets: dict[Path, list[str]] = {}
        if main_tables:
            targets.setdefault(self.sources[-1][0], []).extend(main_tables)
        if variant_tables:
            # A variant lane outside the file that owns the experiment would be ignored.
            assert self.experiment_owner is not None
            targets.setdefault(self.experiment_owner, []).extend(variant_tables)
        if not targets and "reviewers" in merged:
            # Former keys never applied on top of lanes, so the declared lanes stay authoritative.
            message += "\nNo lane is necessary: the configured [[reviewers]] lanes apply."
        elif not targets:
            message += "\nNo lane is necessary: one shot per pass is the default lane."
        sections = (
            f"To keep the same reviews, add to {target}:\n\n" + "\n\n".join(tables)
            for target, tables in targets.items()
        )
        message += "".join(f"\n{section}\n" for section in sections).rstrip("\n")
        raise ReviewStateError(message)

    def _lanes(
        self, merged: dict[str, Any], experiment: _Experiment
    ) -> tuple[list[str], list[str]]:
        default_lane = None
        main_tables: list[str] = []
        tables: list[str] = []
        # Lanes that the chain already declares replace the former shots.
        if "reviewers" not in merged:
            default_lane = self._lane(merged.get("slice_default"), self.main)
            if default_lane is not None:
                main_tables.append(_lane_table("reviewers", default_lane))
        for tag, body in sorted(experiment.bodies.items()):
            if "reviewers" in body or ("reviewers" in merged and tag not in self.variants):
                continue
            if tag not in self.variants and "slice_default" not in body:
                continue
            profile = body.get("slice_default", merged.get("slice_default"))
            lane = self._lane(profile, {**self.main, **self.variants.get(tag, {})})
            if lane is None and default_lane is not None:
                # Without its own lane the variant would inherit the main multi-shot lane.
                lane = _profile_lane(profile)
            if lane is not None and lane != default_lane:
                tables.append(_lane_table(f"variant.{tag}.reviewers", lane))
        return main_tables, tables

    @staticmethod
    def _lane(profile: Any, keys: dict[str, Any]) -> dict[str, Any] | None:
        return _legacy_lane(
            profile, keys.get("shots", 1), keys.get("shot_passes", LEGACY_DEFAULT_SHOT_PASSES)
        )


def _lane_table(name: str, lane: dict[str, Any]) -> str:
    body = "\n".join(f"{key} = {json.dumps(value)}" for key, value in lane.items())
    return f"[[{name}]]\n{body}"


def _validate_settings(path: Path | str, data: dict[str, Any]) -> dict[str, Any]:
    validated: dict[str, Any] = {}
    for key, value in data.items():
        if key == "review_file":
            validated[key] = _validate_review_file(path, value)
        elif key == "review_root":
            validated[key] = _validate_review_root(path, value)
        elif key == "max_passes":
            validated[key] = _validate_positive_int(path, key, value)
        elif key == "reviewers":
            validated[key] = _validate_reviewers(path, value)
        elif key == "max_parallel":
            validated[key] = _validate_max_parallel(path, value)
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


def _validate_review_root(path: Path | str, value: Any) -> Path:
    if isinstance(value, Path):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ReviewStateError(f"review config review_root must be a non-empty string: {path}")
    root = Path(value.strip()).expanduser()
    if not root.is_absolute():
        raise ReviewStateError(
            f"review config review_root must be an absolute path or start with ~: {path}"
        )
    return root


def _validate_positive_int(path: Path | str, key: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ReviewStateError(
            f"review config {key} must be a positive integer: {path}"
        )
    return value


def _validate_reviewers(path: Path | str, value: Any) -> tuple[ReviewerLane, ...]:
    if not isinstance(value, list):
        raise ReviewStateError(f"review config reviewers must be [[reviewers]] tables: {path}")
    lanes = tuple(_validate_lane(path, index, lane) for index, lane in enumerate(value, start=1))
    # Pass 1 has no earlier findings to fall back on, so some lane must start there.
    if lanes and not any(lane.from_pass == 1 for lane in lanes):
        raise ReviewStateError(f"review config reviewers need a lane with from_pass = 1: {path}")
    return lanes


def _validate_lane(path: Path | str, index: int, value: Any) -> ReviewerLane:
    owner = f"reviewers[{index}]"
    if not isinstance(value, dict):
        raise ReviewStateError(f"review config {owner} must be a TOML table: {path}")
    unknown = set(value) - _LANE_KEYS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ReviewStateError(f"unsupported review config {owner} setting(s) in {path}: {names}")
    profile = _validate_profile(path, owner, {key: value[key] for key in _PROFILE_KEYS & set(value)})
    schedule: dict[str, Any] = {}
    for key in _LANE_STRING_KEYS & set(value):
        if not isinstance(value[key], str) or not value[key].strip():
            raise ReviewStateError(f"review config {owner}.{key} must be a non-empty string: {path}")
        schedule[key] = value[key].strip()
    for key in _LANE_POSITIVE_INT_KEYS & set(value):
        schedule[key] = _validate_positive_int(path, f"{owner}.{key}", value[key])
    for key in _LANE_BOOL_KEYS & set(value):
        if not isinstance(value[key], bool):
            raise ReviewStateError(f"review config {owner}.{key} must be a boolean: {path}")
        schedule[key] = value[key]
    return ReviewerLane(
        harness=profile.harness, model=profile.model, reasoning=profile.reasoning, **schedule
    )


def _validate_max_parallel(path: Path | str, value: Any) -> int | str:
    if value == MAX_PARALLEL_AUTO:
        return MAX_PARALLEL_AUTO
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ReviewStateError(
            f'review config max_parallel must be a positive integer or "{MAX_PARALLEL_AUTO}": {path}'
        )
    return value


def parse_max_parallel(value: str) -> int | str:
    """Parse a CLI `--max-parallel` value with the same rules as the config key."""

    text = value.strip()
    if text == MAX_PARALLEL_AUTO:
        return MAX_PARALLEL_AUTO
    if text.isdigit() and int(text) >= 1:
        return int(text)
    raise ValueError(f'expected a positive integer or "{MAX_PARALLEL_AUTO}", got {value!r}')


def resolve_max_parallel(value: int | str) -> int:
    """Turn `auto` into a child limit from the cores and memory free when the wave starts."""

    if value != MAX_PARALLEL_AUTO:
        return value
    # process_cpu_count honors CPU affinity but only exists from Python 3.13.
    count_cpus = getattr(os, "process_cpu_count", os.cpu_count)
    cpus = count_cpus() or 1
    available = _available_memory_bytes()
    if available is None:
        return cpus
    return max(1, min(cpus, available // AUTO_MEMORY_PER_CHILD))


def _available_memory_bytes() -> int | None:
    # MemAvailable counts reclaimable cache and excludes swap: swap-backed children are the
    # failure mode the limit exists to prevent. Hosts without /proc fall back to cores only.
    try:
        meminfo = Path("/proc/meminfo").read_text(encoding="ascii")
    except OSError:
        return None
    match = re.search(r"^MemAvailable:\s+(\d+) kB$", meminfo, re.MULTILINE)
    return int(match.group(1)) * 1024 if match else None


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
