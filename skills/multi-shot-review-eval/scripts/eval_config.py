from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eval_errors import EvalError


CONFIG_FILENAME = "multi-shot-review.toml"
DEFAULT_VARIANT = "default"


@dataclass(frozen=True)
class AnalystSpec:
    harness: str
    model: str
    effort: str | None

    @property
    def name(self) -> str:
        return f"{self.harness}/{self.model}"

    def as_dict(self) -> dict[str, str | None]:
        return {"analyst": self.name, "harness": self.harness, "model": self.model, "effort": self.effort}


@dataclass(frozen=True)
class EvalConfig:
    review_root: Path
    active_tags: tuple[str, ...]
    analysts: tuple[AnalystSpec, ...] | None


def load_eval_config(cwd: Path, *, home: Path | None = None, explicit: Path | None = None) -> EvalConfig:
    """Mirror the multi-shot-review config chain: nearer review_root wins, nearest experiment owns all tags."""

    home = (Path.home() if home is None else home).resolve()
    if explicit is not None:
        if not explicit.is_file():
            raise EvalError(f"config is not a file: {explicit}")
        chain: tuple[Path, ...] = (explicit,)
    else:
        chain = _config_chain(cwd.resolve(), home)
    review_root = home / ".reviews"
    weights: dict[str, int] = {DEFAULT_VARIANT: 1}
    analysts_source: tuple[Path, Any] | None = None
    for path in chain:
        data = _read_toml(path)
        if "review_root" in data:
            review_root = _expand(data["review_root"], home)
        if "variants" in data or "variant" in data:
            weights = _experiment_weights(data)
        evaluation = data.get("eval", {})
        if not isinstance(evaluation, dict):
            raise EvalError(f"{path}: eval must be a TOML table")
        if "analysts" in evaluation:
            analysts_source = (path, evaluation["analysts"])
    return EvalConfig(
        review_root=review_root,
        active_tags=tuple(tag for tag, weight in weights.items() if weight > 0),
        analysts=None if analysts_source is None else _analysts(analysts_source[1], analysts_source[0]),
    )


def _config_chain(cwd: Path, home: Path) -> tuple[Path, ...]:
    locations = [home]
    if cwd != home:
        try:
            relative = cwd.relative_to(home)
        except ValueError:
            locations.append(cwd)
        else:
            current = home
            for part in relative.parts:
                current /= part
                locations.append(current)
    return tuple(location / ".agents" / CONFIG_FILENAME for location in locations)


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, ValueError) as exc:
        raise EvalError(f"could not read config {path}: {exc}") from exc


def _expand(value: Any, home: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise EvalError("review_root must be a non-empty string")
    text = value.strip()
    if text == "~" or text.startswith("~/"):
        return home / text[2:]
    return Path(text)


def _experiment_weights(data: dict[str, Any]) -> dict[str, int]:
    # Matches the reviewer's draw: default and every body start at weight 1 unless weighted explicitly.
    bodies = data.get("variant") or {}
    explicit = data.get("variants") or {}
    if not isinstance(bodies, dict) or not isinstance(explicit, dict):
        raise EvalError("variants and variant must be TOML tables")
    return {DEFAULT_VARIANT: 1, **{tag: 1 for tag in bodies}, **explicit}


def _analysts(value: Any, path: Path) -> tuple[AnalystSpec, ...]:
    # Model names age fast, so they live only in user config; without it the agent picks current models at run time.
    if not isinstance(value, list) or len(value) < 2:
        raise EvalError(f"{path}: [eval] analysts needs at least two tables, so an independent second opinion exists")
    specs = tuple(_analyst(item, f"{path}: [eval] analysts[{index}]") for index, item in enumerate(value))
    names = [spec.name for spec in specs]
    if len(set(names)) != len(names):
        raise EvalError(f"{path}: [eval] analysts names one harness/model more than once")
    return specs


def _analyst(item: Any, where: str) -> AnalystSpec:
    if not isinstance(item, dict):
        raise EvalError(f"{where} must be a table with harness, model, and optional effort")
    fields = {key: item.get(key) for key in ("harness", "model", "effort")}
    for key in ("harness", "model"):
        if not isinstance(fields[key], str) or not fields[key].strip():
            raise EvalError(f"{where}: {key} must be a non-empty string")
    if fields["effort"] is not None and (not isinstance(fields["effort"], str) or not fields["effort"].strip()):
        raise EvalError(f"{where}: effort must be a non-empty string when set")
    return AnalystSpec(
        harness=fields["harness"].strip(),
        model=fields["model"].strip(),
        effort=fields["effort"].strip() if fields["effort"] else None,
    )
