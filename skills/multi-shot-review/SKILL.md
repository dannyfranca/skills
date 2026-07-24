---
name: multi-shot-review
description: Review code changes through classified, narrowly scoped parallel harness passes. Use for iterative review barriers by default or one-wave review reports when explicitly requested.
---

# Multi-Shot Review

1. Choose the mode from the user's authority:

- **Barrier (default)**: start every review in this mode.
- **Report**: switch only when the user explicitly requests a report-only review with the target
  unchanged.

2. Initialize the review with its live Git target and exact original request:

```bash
SKILL_DIR="/path/to/this-skill"
REVIEW_DIR="$(python3 "$SKILL_DIR/scripts/init_state.py" \
  --uncommitted \
  --task-file - <<'EOF'
<original user request>
EOF
)"
```

Use `--base <branch>` or `--commit <sha>` when appropriate. The state stores only that target
descriptor; classifiers and reviewers inspect Git directly. Finish when `$REVIEW_DIR/task.md`
contains exact task context.

3. Run the clean classifier:

```bash
python3 "$SKILL_DIR/scripts/classify_slices.py" \
  --review-dir "$REVIEW_DIR"
```

Use `--harness`, `--model`, or `--reasoning` only to override the configured classifier profile.

When useful, pass verbatim supplemental user directions with
`--user-directives-file <path>` and advisory parent context with
`--executor-context-file <path>`. The classifier reads the target, task, rules, code, current
slices, tombstones, runs, and history. It contextually calls `add_slice.py` and `remove_slice.py`;
there is no classification plan artifact.
Selection behavior lives in
[`references/slice-selection.md`](references/slice-selection.md) and is loaded by the classifier.
The launcher automatically resolves global and changed-path review instruction chains into
classifier-only context. Reviewers receive only concrete requirements that the classifier
deliberately translates into focused slice prompts.

Reclassify on **coverage drift**: the target, task, or guidance makes slices incomplete, mis-scoped,
obsolete, or incoherent. Also reclassify after partial failure or explicit request. Otherwise rerun
incomplete slices.

4. Run one review wave exclusively in the foreground with a timeout of at least one hour:

```bash
python3 "$SKILL_DIR/scripts/run_reviews.py" --review-dir "$REVIEW_DIR" \
  --child-timeout-seconds 3600
```

Wait silently for exit. All eligible slices run in one parallel wave. Reviewers emit the strict
JSON shape in `references/review-result.schema.json`; the runner validates it, assigns
session-scoped `f_` IDs, and replaces the raw result with generated Markdown. Invalid results are
retryable slice failures. Consume only final JSON, finding IDs and Markdown paths in `out`, and
diagnostics in `err`. Treat each finding as a hypothesis and validate it against the code and task.

If the harness detaches while the wave continues, await that wave in the foreground:

```bash
python3 "$SKILL_DIR/scripts/await_reviews.py" --review-dir "$REVIEW_DIR"
```

Repeat after any further detachment. The awaiter is a pure join: it captures the active wave, waits
silently, and emits one final JSON summary.

5. Complete the chosen mode:

### Report

Return the validated, consolidated findings with file and line references. Include slice failures
as unavailable review coverage. Interpret `ok` as execution success and `rem` as follow-up
eligibility. Complete Report mode after consuming the wave for any `rem` value.

On a requested follow-up, reclassify first when the target, task, or desired coverage changed,
then run another wave. Reuse the session to run eligible slices; initialize a new session for an
independent repeat of every slice.

### Barrier

Fix validated findings and add focused regression tests where they materially reduce risk. Ignore
one rejected finding by ID with an immutable reason:

```bash
python3 "$SKILL_DIR/scripts/ignore_finding.py" \
  --review-dir "$REVIEW_DIR" \
  --id "<finding-id>" \
  --reason "<why it is not actionable>"
```

Use `--reason-file <path>` for a longer reason. Mark a repeated finding as a duplicate of another
currently open finding:

```bash
python3 "$SKILL_DIR/scripts/dedupe_finding.py" \
  --review-dir "$REVIEW_DIR" \
  --id "<duplicate-id>" \
  --canonical-id "<open-finding-id>"
```

Run another wave after fixes. A slice also completes when all findings in its latest run are
ignored or deduplicated. Finish when every finding is fixed or recorded terminal, relevant checks
pass, and JSON returns `"ok":true` and `"rem":0`.

## Explicit user slice changes

Only an explicit user request authorizes parent-driven mutation. Preserve that request:

```bash
python3 "$SKILL_DIR/scripts/add_slice.py" \
  --review-dir "$REVIEW_DIR" \
  --name "<slice-name>" \
  --harness "<harness>" \
  --prompt-file "<prompt-file>" \
  --user-directive-file "<verbatim-user-request-file>"
```

Remove a slice with the same authority marker:

```bash
python3 "$SKILL_DIR/scripts/remove_slice.py" \
  --review-dir "$REVIEW_DIR" \
  --name "<slice-name>" \
  --user-directive-file "<verbatim-user-request-file>"
```

Removal tombstones the slice; re-adding its name reactivates it. Definitions may change, while
runs, outputs, and history remain.

Treat scripts as sole owners of state, locking, output names, rendering, retries, and completion.
Do not edit generated review Markdown. Keep `.review/` uncommitted.
