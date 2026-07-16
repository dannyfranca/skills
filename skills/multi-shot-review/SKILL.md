---
name: multi-shot-review
description: Review broad or risky code changes through classified, narrowly scoped, repeated Codex CLI passes. Use after refactors, migrations, contract changes, or when another skill requires a review barrier.
---

# Multi-Shot Review

1. Scope the review to changes you own. Initialize one immutable review target with the exact original request:

```bash
SKILL_DIR="/path/to/this-skill"
REVIEW_DIR="$(python3 "$SKILL_DIR/scripts/init_state.py" --uncommitted --task-file - <<'EOF'
<original user request>
EOF
)"
```

Use `--base <branch>` or `--commit <sha>` instead of `--uncommitted` when appropriate. Base sessions pin both the resolved base and current HEAD; later commits cannot expand the target. Base/commit classifiers and reviewers run in isolated trees for those pinned commits. Finish when `$REVIEW_DIR/task.md` contains exact task context and the session target excludes unrelated changes.

Dirty files inside submodules fail closed because a superproject gitlink cannot preserve their exact review surface. Commit the submodule state or review that repository separately.

2. Run the clean slice classifier. The parent may omit both optional context channels:

```bash
python3 "$SKILL_DIR/scripts/classify_slices.py" \
  --review-dir "$REVIEW_DIR"
```

`task.md` is always the mandatory original user request. When applicable, write supplemental verbatim user directions or advisory context to files, then append `--user-directives-file <path>` and/or `--executor-context-file <path>`. Later reclassification reuses both persisted channels when those flags are omitted.

Read [`references/slice-selection.md`](references/slice-selection.md) completely before invoking it. The classifier alone selects areas, lenses, grouping, and contextual slices. The parent must not add, remove, merge, broaden, or rewrite slices unless explicitly directed by the user. Finish when `classification.json` exists and all slices were registered atomically.

3. Run the review barrier exclusively in the foreground with a timeout of at least two hours:

```bash
python3 "$SKILL_DIR/scripts/run_reviews.py" \
  --review-dir "$REVIEW_DIR" \
  --child-timeout-seconds 7200
```

Wait silently for exit. Default concurrency is six. Consume only final JSON, the review Markdown paths listed in its `out` array (stored directly under `$REVIEW_DIR`), and diagnostics listed in `err` (with child logs under `$REVIEW_DIR/_logs` and the aggregate log at `$REVIEW_DIR/_errors.md`). If the changed-file, changed-line, or content footprint changes, the runner blocks; rerun step 2 before continuing.

4. Treat each finding as a hypothesis. Validate against code and task, fix real findings, and add focused regression tests where they materially reduce risk. Report rejected findings from a slice's latest run:

```bash
python3 "$SKILL_DIR/scripts/report_ignored_findings.py" \
  --review-dir "$REVIEW_DIR" \
  --slice "<slice-name>" \
  --count "<ignored-count>"
```

Finish when every finding is fixed or reported ignored and relevant checks pass.

5. Run the barrier after fixes or ignored-finding reports. Repeat until JSON returns `"ok":true` and `"rem":0`.

## Explicit user slice changes

Only an explicit user request authorizes manual mutation. Preserve the directive in state. Classifier-owned slices always carry structured metadata; a free-form user slice is the deliberate override path:

```bash
PROMPT_FILE="$(mktemp)"
DIRECTIVE_FILE="$(mktemp)"
cat >"$PROMPT_FILE" <<'EOF'
<focused review prompt>
EOF
cat >"$DIRECTIVE_FILE" <<'EOF'
<verbatim user request>
EOF

python3 "$SKILL_DIR/scripts/add_slice.py" \
  --review-dir "$REVIEW_DIR" \
  --name "<slice-name>" \
  --prompt-file "$PROMPT_FILE" \
  --user-directive-file "$DIRECTIVE_FILE"
```

To remove a slice instead, record that distinct user request:

```bash
REMOVE_DIRECTIVE_FILE="$(mktemp)"
cat >"$REMOVE_DIRECTIVE_FILE" <<'EOF'
<verbatim user request to remove the slice>
EOF

python3 "$SKILL_DIR/scripts/remove_slice.py" \
  --review-dir "$REVIEW_DIR" \
  --name "<slice-name>" \
  --user-directive-file "$REMOVE_DIRECTIVE_FILE"
```

Removal tombstones a slice; it never erases definitions, runs, outputs, or history.

Treat scripts as sole owners of state, locks, output names, retries, classification, and completion. Keep `.review/` uncommitted.
