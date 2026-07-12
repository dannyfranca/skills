---
name: multi-shot-review
description: Review broad or risky code changes through repeated Codex CLI passes. Use after refactors, migrations, contract changes, or when another skill requires a review barrier.
---

# Multi-Shot Review

1. Scope the review to the changes you own, then initialize the review barrier with the original user request:

```bash
SKILL_DIR=/path/to/this-skill
REVIEW_DIR="$(python3 "$SKILL_DIR/scripts/init_state.py" --task-file - <<'EOF'
<original user request>
EOF
)"
```

Finish when `$REVIEW_DIR/task.md` contains the exact task context and unrelated changes are outside the review scope.

2. Before registering any slice, read [`references/slice-selection.md`](references/slice-selection.md) completely and apply its coverage rules. Finish when every meaningful risk belongs to at least one registered slice.

3. Run the review barrier exclusively in the foreground with a timeout of at least two hours:

```bash
python3 "$SKILL_DIR/scripts/run_reviews.py" --review-dir "$REVIEW_DIR"
```

Wait silently for exit. Consume only its final JSON, the review files in `out`, and any error logs in `err` needed for diagnosis. Finish when every emitted result has been consumed.

4. Treat each finding as a hypothesis. Validate it against the code and task, fix real findings, and add focused regression tests where they materially reduce risk. Report findings rejected from a slice's latest run:

```bash
python3 "$SKILL_DIR/scripts/report_ignored_findings.py" \
  --review-dir "$REVIEW_DIR" \
  --slice <slice-name> \
  --count <ignored-count>
```

Finish when every finding is fixed or reported as ignored and relevant checks pass.

5. Run the review barrier after fixes or ignored-finding reports. Repeat adjudication until its JSON returns `"ok":true` and `"rem":0`.

Treat the scripts as the sole owners of state, locking, output names, retries, and slice completion. Keep `.review/` uncommitted.
