"""Public harness seam, built-in registry, and profile resolution."""

from __future__ import annotations

from .base import (
    DEFAULT_HARNESS,
    HarnessError,
    HarnessProfile,
    Invocation,
    ResolvedProfile,
    ReviewHarness,
    non_empty,
)
from .claude_code import ClaudeCodeHarness
from .codex import CodexHarness


_HARNESSES: dict[str, ReviewHarness] = {
    adapter.name: adapter for adapter in (CodexHarness(), ClaudeCodeHarness())
}
SUPPORTED_HARNESSES = frozenset(_HARNESSES)


def get_harness(name: str) -> ReviewHarness:
    """Return a built-in harness adapter by stable configuration ID."""

    normalized = non_empty(name, "harness")
    try:
        return _HARNESSES[normalized]
    except KeyError as exc:
        supported = ", ".join(sorted(SUPPORTED_HARNESSES))
        raise HarnessError(
            f"unsupported harness {normalized!r}; expected one of: {supported}"
        ) from exc


def resolve_profile(
    configured: HarnessProfile | None,
    *,
    harness: str | None = None,
    model: str | None = None,
    reasoning: str | None = None,
    override_source: str,
) -> ResolvedProfile:
    """Resolve overrides without leaking choices across different harnesses."""

    configured_harness = (
        non_empty(configured.harness, "harness")
        if configured is not None
        else DEFAULT_HARNESS
    )
    resolved_harness = (
        non_empty(harness, "harness")
        if harness is not None
        else configured_harness
    )
    get_harness(resolved_harness)
    harness_changed = harness is not None and resolved_harness != configured_harness

    if harness is not None:
        harness_source = override_source
    elif configured is not None:
        harness_source = "configured-default"
    else:
        harness_source = "built-in-default"

    resolved_model, model_source = _resolve_choice(
        explicit=model,
        configured=None if configured is None or harness_changed else configured.model,
        override_source=override_source,
    )
    resolved_reasoning, reasoning_source = _resolve_choice(
        explicit=reasoning,
        configured=(
            None if configured is None or harness_changed else configured.reasoning
        ),
        override_source=override_source,
    )
    return ResolvedProfile(
        harness=resolved_harness,
        harness_source=harness_source,
        model=resolved_model,
        model_source=model_source,
        reasoning=resolved_reasoning,
        reasoning_source=reasoning_source,
    )


def _resolve_choice(
    *,
    explicit: str | None,
    configured: str | None,
    override_source: str,
) -> tuple[str | None, str]:
    if explicit is not None:
        return non_empty(explicit, "execution choice"), override_source
    if configured is not None:
        return configured, "configured-default"
    return None, "harness-default"


__all__ = [
    "DEFAULT_HARNESS",
    "SUPPORTED_HARNESSES",
    "HarnessError",
    "HarnessProfile",
    "Invocation",
    "ResolvedProfile",
    "ReviewHarness",
    "get_harness",
    "resolve_profile",
]
