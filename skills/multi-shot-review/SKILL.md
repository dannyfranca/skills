---
name: multi-shot-review
description: Review broad or risky code changes through classified, narrowly scoped, repeated Codex CLI passes. Use after refactors, migrations, contract changes, or when another skill requires a review barrier.
---

# Multi-Shot Review

1. Initialize the review with its live Git target and exact original request:

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

2. Run the clean classifier:

```bash
python3 "$SKILL_DIR/scripts/classify_slices.py" \
  --review-dir "$REVIEW_DIR"
```

When useful, pass verbatim supplemental user directions with
`--user-directives-file <path>` and advisory parent context with
`--executor-context-file <path>`. The classifier reads the target, task, rules, code, current
slices, tombstones, runs, and history. It contextually calls `add_slice.py` and `remove_slice.py`;
there is no classification plan artifact.
Selection behavior lives in
[`references/slice-selection.md`](references/slice-selection.md) and is loaded by the classifier.

Run classification again after material target changes, partial classifier failure, or a user
request to reconsider slices. Successful prior mutations remain context.

3. Run the review barrier exclusively in the foreground with a timeout of at least two hours:

```bash
python3 "$SKILL_DIR/scripts/run_reviews.py" --review-dir "$REVIEW_DIR" \
  --child-timeout-seconds 7200
```

Wait silently for exit. Default concurrency is six. Consume only final JSON, the review Markdown
paths in `out`, and diagnostics in `err`. Finish when every emitted result has been consumed.

4. Treat each finding as a hypothesis. Validate it against code and task, fix real findings, and
add focused regression tests where they materially reduce risk. Report rejected findings from a
slice's latest run:

```bash
python3 "$SKILL_DIR/scripts/report_ignored_findings.py" \
  --review-dir "$REVIEW_DIR" \
  --slice "<slice-name>" \
  --count "<ignored-count>"
```

Finish when every finding is fixed or reported ignored and relevant checks pass.

5. Run the barrier after fixes or ignored-finding reports. Repeat until JSON returns `"ok":true`
and `"rem":0`.

## Explicit user slice changes

Only an explicit user request authorizes parent-driven mutation. Preserve that request:

```bash
python3 "$SKILL_DIR/scripts/add_slice.py" \
  --review-dir "$REVIEW_DIR" \
  --name "<slice-name>" \
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

Treat scripts as sole owners of state, locking, output names, retries, and completion. Keep
`.review/` uncommitted.
