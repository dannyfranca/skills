# Extending Review Harnesses

Harness adapters are the contribution seam between review orchestration and an agent CLI. The
registry is intentionally closed to the built-ins today; contributors add a reviewed built-in,
not a dynamically loaded plugin.

## Stable contract

Add one `ReviewHarness` implementation in its own `scripts/harnesses/<harness>.py` file and
register a stable, lowercase configuration ID in `scripts/harnesses/__init__.py`. Shared interface
types live in `scripts/harnesses/base.py`. An adapter has three responsibilities:

1. Build a non-interactive classifier invocation.
2. Build a read-only reviewer invocation that requests the shared result schema.
3. Normalize the harness result envelope into that shared JSON result document.

The adapter receives a resolved `ResolvedProfile` and prompt. It returns only an `Invocation`.
Core orchestration remains responsible for subprocess execution, timeouts, concurrency, logs,
state, locking, retries, finding IDs, and Markdown rendering.

Do not add arbitrary pass-through CLI arguments. Map only named profile fields with clear,
testable semantics. Model and reasoning values remain opaque strings until the adapter translates
them.

## Isolation and permissions

Prefer ephemeral, non-persistent sessions. Disable external integrations and writable tools when
the harness supports it. Reviewers need repository read access only; enforce this beneath ambient
permission rules with the harness's native filesystem sandbox. A classifier may mutate review state
solely through `add_slice.py` and `remove_slice.py`; grant no broader write path.

Repository agent instructions remain available unless the project explicitly changes that policy.
Do not depend on user hooks, memory, MCP servers, or ambient extensions for correct operation.

## Result contract

All reviewers ultimately produce `references/review-result.schema.json`. If a CLI wraps structured
output, `materialize_review_result` extracts it into the output file. Missing, malformed, or
non-schema results must fail visibly and remain retryable; never synthesize a no-findings result.

## Contribution checklist

- Register the adapter; `SUPPORTED_HARNESSES` is derived from the registry.
- Preserve the default `codex` behavior.
- Test profile translation, isolation flags, permissions, structured-output normalization, and an
  end-to-end runner pass.
- Test unavailable executables and malformed envelopes as failures.
- Document user-facing profile examples in the README.
- Keep harness-specific policy out of state, rendering, and orchestration modules.
