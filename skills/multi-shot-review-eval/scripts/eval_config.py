from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eval_errors import EvalError


CONFIG_FILENAME = "multi-shot-review.toml"
DEFAULT_VARIANT = "default"


@dataclass(frozen=True)
class EvalConfig:
    review_root: Path
    active_tags: tuple[str, ...]


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
    for path in chain:
        data = _read_toml(path)
        if "review_root" in data:
            review_root = _expand(data["review_root"], home)
        if "variants" in data or "variant" in data:
            weights = _experiment_weights(data)
    return EvalConfig(
        review_root=review_root,
        active_tags=tuple(tag for tag, weight in weights.items() if weight > 0),
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
