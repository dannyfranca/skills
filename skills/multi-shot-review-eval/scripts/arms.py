from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from itertools import combinations

from eval_errors import EvalError
from session_store import Session
from timestamps import parse_timestamp


class PlanMode(StrEnum):
    TAGS = "tags"
    LATEST_CONCURRENT = "latest_concurrent"
    CUTOFF = "cutoff"


@dataclass(frozen=True)
class ArmRequest:
    tags: tuple[str, ...] | None = None
    since: datetime | None = None
    until: datetime | None = None
    repo: str | None = None
    cutoff: str | None = None


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment <= self.end


@dataclass(frozen=True)
class Arm:
    name: str
    sessions: tuple[Session, ...]

    @property
    def completed(self) -> tuple[Session, ...]:
        return tuple(session for session in self.sessions if session.completed)


@dataclass(frozen=True)
class ArmPlan:
    mode: PlanMode
    arms: tuple[Arm, ...]
    window: Window
    contrast_key: str

    @property
    def baseline(self) -> Arm:
        return self.arms[0]


def plan_arms(sessions: Sequence[Session], active_tags: Sequence[str], request: ArmRequest) -> ArmPlan:
    """The first arm is the baseline every other arm is contrasted against."""

    pool = sorted(
        (session for session in sessions if request.repo is None or session.repository == request.repo),
        key=lambda session: session.created_at,
    )
    if request.tags is not None and len(set(request.tags)) != len(request.tags):
        raise EvalError(f"duplicate tags: {', '.join(request.tags)}")
    plan = _cutoff_plan(pool, request, request.cutoff) if request.cutoff is not None else _tags_plan(pool, active_tags, request)
    empty = [arm.name for arm in plan.arms if not arm.completed]
    if empty:
        raise EvalError(f"no completed session inside the window for arm {', '.join(empty)}")
    return plan


def _tags_plan(pool: Sequence[Session], active_tags: Sequence[str], request: ArmRequest) -> ArmPlan:
    tags = request.tags or tuple(active_tags)
    mode = PlanMode.TAGS
    if len(tags) < 2:
        required = request.tags[0] if request.tags else None
        tags, window = _latest_concurrent(pool, tags, required)
        mode = PlanMode.LATEST_CONCURRENT
    else:
        window = _overlap([_span(pool, tag) for tag in tags])
        if window is None:
            raise EvalError(f"tags {', '.join(tags)} have no overlapping completed sessions")
    window = Window(request.since or window.start, request.until or window.end)
    arms = tuple(
        Arm(tag, tuple(s for s in pool if s.variant == tag and window.contains(s.created_at))) for tag in tags
    )
    return ArmPlan(mode=mode, arms=arms, window=window, contrast_key=_tags_key(tags, request.repo))


def _span(pool: Iterable[Session], tag: str) -> Window | None:
    created = [session.created_at for session in pool if session.variant == tag and session.completed]
    return Window(min(created), max(created)) if created else None


def _overlap(spans: Iterable[Window | None]) -> Window | None:
    spans = list(spans)
    if not spans or any(span is None for span in spans):
        return None
    start, end = max(span.start for span in spans), min(span.end for span in spans)
    return Window(start, end) if start <= end else None


def _latest_concurrent(
    pool: Sequence[Session], active: Sequence[str], required: str | None
) -> tuple[tuple[str, ...], Window]:
    spans = {tag: _span(pool, tag) for tag in dict.fromkeys(session.variant for session in pool)}
    spans = {tag: span for tag, span in spans.items() if span is not None}
    candidates = []
    for pair in combinations(spans, 2):
        window = _overlap(spans[tag] for tag in pair)
        # A tag whose span merely surrounds the window may have no session inside it, so it cannot form an arm.
        if window is None or not all(_has_completed_inside(pool, tag, window) for tag in pair):
            continue
        inside = [tag for tag in spans if _has_completed_inside(pool, tag, window)]
        if required is None or required in inside:
            candidates.append((window, inside))
    if not candidates:
        wanted = f" including {required}" if required else ""
        raise EvalError(f"no window has completed sessions from two or more tags{wanted}")
    window, inside = max(candidates, key=lambda candidate: (candidate[0].end, candidate[0].start))
    inside.sort(key=lambda tag: (tag not in active, spans[tag].start))
    return tuple(inside), window


def _has_completed_inside(pool: Iterable[Session], tag: str, window: Window) -> bool:
    return any(s.variant == tag and s.completed and window.contains(s.created_at) for s in pool)


def _cutoff_plan(pool: Sequence[Session], request: ArmRequest, cutoff: str) -> ArmPlan:
    if request.tags:
        pool = [session for session in pool if session.variant in request.tags]
    in_range = [
        s
        for s in pool
        if (request.since is None or s.created_at >= request.since)
        and (request.until is None or s.created_at <= request.until)
    ]
    split = parse_timestamp(cutoff)
    before, after = _split_by_time(in_range, split) if split is not None else _split_by_revision(pool, in_range, cutoff)
    if not before or not after:
        raise EvalError(f"cutoff {cutoff} leaves an empty arm")
    key = f"cutoff:{cutoff}"
    if request.tags:
        key += "#" + "|".join(sorted(request.tags))
    if request.repo:
        key += f"@{request.repo}"
    return ArmPlan(
        mode=PlanMode.CUTOFF,
        arms=(Arm("before", before), Arm("after", after)),
        window=Window(min(s.created_at for s in before + after), max(s.created_at for s in before + after)),
        contrast_key=key,
    )


def _split_by_time(sessions: Sequence[Session], split: datetime) -> tuple[tuple[Session, ...], tuple[Session, ...]]:
    return tuple(s for s in sessions if s.created_at < split), tuple(s for s in sessions if s.created_at >= split)


def _split_by_revision(
    pool: Sequence[Session], sessions: Sequence[Session], cutoff: str
) -> tuple[tuple[Session, ...], tuple[Session, ...]]:
    """Revisions are unordered hashes, so a revision is older than the cutoff when it first ran before the cutoff did.

    A checkout can return to an older revision after the cutoff ships, and a newer revision is a different change;
    neither belongs in the after arm. Sessions without a revision predate revision recording, so time places them.
    """

    first = next((s for s in pool if s.skill_revision == cutoff), None)
    if first is None:
        raise EvalError(f"no session carries skill revision {cutoff}")
    older = {s.skill_revision for s in pool if s.skill_revision is not None and s.created_at < first.created_at}
    before = tuple(
        s
        for s in sessions
        if s.skill_revision in older or (s.skill_revision is None and s.created_at < first.created_at)
    )
    return before, tuple(s for s in sessions if s.skill_revision == cutoff)


def _tags_key(tags: Sequence[str], repo: str | None) -> str:
    # The first tag is the baseline, so reordered tags are a different contrast and must not pool together.
    key = "tags:" + "|".join(tags)
    return f"{key}@{repo}" if repo else key
