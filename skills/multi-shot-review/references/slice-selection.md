# Slice Selection Contract

Slice selection belongs to one clean classifier session. The executor initializes the review, optionally supplies mandatory user directives and advisory context, invokes the classifier, then consumes its atomic plan. It must not choose or edit slices itself.

The authoritative classifier prompt is [`classifier-rules.md`](classifier-rules.md). Validation enforces its structural invariants even when classifier output is malformed or incomplete.

## Classification artifact

`classification.json` records:

- Immutable session target and exact changed files.
- Deterministic meaningful line counts.
- Loaded global, repository, and scoped rule sources.
- User directives and executor context as separate authority channels.
- Explicit directive-to-lens/slice coverage, validated before atomic registration.
- Coherent change areas and database/risk classification.
- Native-review eligibility and rationale.
- Structured slice intents, exact primary/context scopes, lenses, risks, and grouping rationale.
- Coverage matrix from every mandatory/contextual concern to slices.
- Deterministically rendered reviewer prompts.

Invalid output registers no slices. Active classification may change atomically after the changed-file or changed-line footprint changes. The state keeps every full applied classification as immutable audit history while `classification.json` remains the current runtime plan; each review run also preserves an immutable definition snapshot.

Explicit user add/remove commands remain free-form overrides. A user-added native slice still receives the original task, its authoritative user directive, all loaded repository/global review rules, and the core correctness/design/readability/simplicity lens rules.

## Rules convention

Projects may add `REVIEW.md` at repository root or in nested directories. Nested files apply to changed descendants. One global file is supported at `~/.agents/REVIEW.md`.

Precedence:

1. Explicit user directives.
2. Closest scoped `REVIEW.md` / `AGENTS.md`.
3. Repository-root review, agent, contributing, and coding-standard files.
4. Global `~/.agents/REVIEW.md`.
5. Built-in lens rules.

At the same directory, `REVIEW.md` precedes `AGENTS.md`. At repository root, order is `REVIEW.md`, `AGENTS.md`, lexically sorted `CONTRIBUTING*`, then lexically sorted `CODING_STANDARDS*`. Only the closest directory containing scoped review/agent rules applies to a changed file; intermediate scoped directories are not stacked.

Classifier and reviewers read sources directly; the parent does not summarize them.

## Coverage invariants

- Every executable area: correctness, design, readability, simplicity.
- Every runtime area: the above plus a dedicated test-coverage slice.
- Docs/metadata: correctness and readability.
- Correctness stays dedicated in focused review; native whole-change review is the exception.
- Design/readability/simplicity may group only for one area with at most three meaningful files, at most 200 meaningful lines, and no architectural change.
- Native whole-change review: one coherent area, at most three meaningful files, at most 250 meaningful lines, no architectural change, and no database, concurrency, migration, security, public-contract, or cross-subsystem risk.
- Small coherent database changes may group database correctness, concurrency, indexing, and execution coverage. Complex or >200-line database changes split all four.
- Contextual slices cover material technical risks and domain workflows/invariants; irrelevant categories are omitted.
- Findings stay within primary scope. Context scope exists for understanding only.
- Unrelated pre-existing debt is out of scope.

## Related work

Register intentionally deferred work so reviewers distinguish it from missing scope:

```bash
python3 "$SKILL_DIR/scripts/add_related_task.py" \
  --review-dir "$REVIEW_DIR" \
  --name <task-name> \
  --text "<what will be addressed later>"
```

Use `--file <path>` or `--dir <path>` for larger related tasks. Reclassify afterward when the changed-file or changed-line footprint changes; task context is read directly by every review slice.
