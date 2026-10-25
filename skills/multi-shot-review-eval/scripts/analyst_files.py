from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeVar

from eval_errors import EvalError


# Keep in step with the analyst table in SKILL.md step 3.
DEFAULT_ANALYSTS = ("claude-code/claude-opus-5-5", "codex/gpt-6.1-sol")


class Verdict(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    UNJUDGEABLE = "unjudgeable"


class Proposal(StrEnum):
    PROMOTE = "promote"
    DROP = "drop"
    KEEP_TESTING = "keep_testing"
    NEW_VARIANT = "new_variant"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


EntryKey = tuple[str, str]
_Choice = TypeVar("_Choice", bound=StrEnum)


@dataclass(frozen=True)
class SampleScope:
    """What the analyst files must cover: every sampled finding and every non-baseline arm."""

    unjudgeable: Mapping[EntryKey, bool]
    arms: tuple[str, ...]
    baseline: str


@dataclass(frozen=True)
class Judgment:
    verdict: Verdict
    reason: str


@dataclass(frozen=True)
class View:
    arm: str
    proposal: Proposal
    confidence: Confidence
    reasoning: str


@dataclass(frozen=True)
class AnalystFile:
    analyst: str
    effort: str
    judgments: dict[EntryKey, Judgment]
    views: tuple[View, ...]
    notes: str


@dataclass(frozen=True)
class MissingAnalyst:
    analyst: str
    reason: str


@dataclass(frozen=True)
class Synthesis:
    recommendations: tuple[View, ...]
    analysts_missing: tuple[MissingAnalyst, ...]


def parse_analyst(document: Any, scope: SampleScope, source: str) -> AnalystFile:
    data = _object(document, source)
    judgments = _judgments(_list(data, "judgments", source), scope, source)
    views = tuple(_view(item, scope, f"{source} views[{index}]") for index, item in enumerate(_list(data, "views", source)))
    _check_view_coverage(views, scope, source)
    return AnalystFile(
        analyst=_text(data, "analyst", source),
        effort=_text(data, "effort", source),
        judgments=judgments,
        views=views,
        notes=_text(data, "notes", source, allow_empty=True),
    )


def parse_synthesis(document: Any, scope: SampleScope, source: str) -> Synthesis:
    data = _object(document, source)
    recommendations = tuple(
        _view(item, scope, f"{source} recommendations[{index}]")
        for index, item in enumerate(_list(data, "recommendations", source))
    )
    _check_view_coverage(recommendations, scope, source)
    missing = tuple(
        MissingAnalyst(
            analyst=_text(_object(item, where), "analyst", where),
            reason=_text(_object(item, where), "reason", where),
        )
        for index, item in enumerate(_list(data, "analysts_missing", source))
        for where in [f"{source} analysts_missing[{index}]"]
    )
    return Synthesis(recommendations=recommendations, analysts_missing=missing)


def _judgments(items: Sequence[Any], scope: SampleScope, source: str) -> dict[EntryKey, Judgment]:
    judgments: dict[EntryKey, Judgment] = {}
    for index, item in enumerate(items):
        where = f"{source} judgments[{index}]"
        data = _object(item, where)
        key = (_text(data, "session_id", where), _text(data, "finding_id", where))
        if key not in scope.unjudgeable:
            raise EvalError(f"{where}: judgment for an entry that is not in the sample: {key[0]} {key[1]}")
        if key in judgments:
            raise EvalError(f"{where}: second judgment for {key[0]} {key[1]}")
        verdict = _choice(Verdict, data, "verdict", where)
        if scope.unjudgeable[key] and verdict != Verdict.UNJUDGEABLE:
            raise EvalError(f"{where}: {key[0]} {key[1]} is marked unjudgeable, so the verdict must be unjudgeable")
        judgments[key] = Judgment(verdict=verdict, reason=_text(data, "reason", where))
    missing = [key for key in scope.unjudgeable if key not in judgments]
    if missing:
        listed = ", ".join(f"{session} {finding}" for session, finding in missing[:5])
        raise EvalError(f"{source}: no judgment for {len(missing)} sample entries: {listed}")
    return judgments


def _check_view_coverage(views: Sequence[View], scope: SampleScope, source: str) -> None:
    # Analyst views and synthesis recommendations share this rule: one base proposal per compared arm; new_variant entries are extras and may start anywhere.
    for arm in scope.arms:
        base = [view for view in views if view.arm == arm and view.proposal != Proposal.NEW_VARIANT]
        if arm == scope.baseline and base:
            raise EvalError(f"{source}: the baseline arm {arm} takes only new_variant views")
        if arm != scope.baseline and len(base) != 1:
            raise EvalError(f"{source}: arm {arm} needs exactly one view that is not new_variant, found {len(base)}")


def _view(item: Any, scope: SampleScope, where: str) -> View:
    data = _object(item, where)
    arm = _text(data, "arm", where)
    if arm not in scope.arms:
        raise EvalError(f"{where}: unknown arm {arm}; arms are {list(scope.arms)}")
    return View(
        arm=arm,
        proposal=_choice(Proposal, data, "proposal", where),
        confidence=_choice(Confidence, data, "confidence", where),
        reasoning=_text(data, "reasoning", where),
    )


def _choice(kind: type[_Choice], data: Mapping[str, Any], key: str, where: str) -> _Choice:
    value = data.get(key)
    try:
        return kind(value)
    except ValueError as exc:
        raise EvalError(f"{where}: {key} must be one of {[choice.value for choice in kind]}, got {value!r}") from exc


def _text(data: Mapping[str, Any], key: str, where: str, *, allow_empty: bool = False) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise EvalError(f"{where}: {key} must be a string")
    if not allow_empty and not value.strip():
        raise EvalError(f"{where}: {key} must not be empty")
    return value


def _list(data: Mapping[str, Any], key: str, where: str) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list):
        raise EvalError(f"{where}: {key} must be a list")
    return value


def _object(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise EvalError(f"{where}: expected a JSON object")
    return value
