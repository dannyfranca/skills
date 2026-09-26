---
name: multi-shot-review
description: Review changes in content slices sized for manageable context, using configured harnesses and repository review policy. Use for iterative review barriers or explicitly requested one-wave reports.
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
contains exact task context. Add `--variant <tag>` only when the user explicitly asks for a config
variant.

3. Run the clean classifier:

```bash
python3 "$SKILL_DIR/scripts/classify_slices.py" \
  --review-dir "$REVIEW_DIR"
```

Join it (see [Joining long runs](#joining-long-runs)). Use `--harness`, `--model`, or
`--reasoning` only to override the configured classifier profile.

When useful, pass verbatim supplemental user directions with
`--user-directives-file <path>` and advisory parent context with
`--executor-context-file <path>`. The classifier reads the target, task, rules, code, current
slices, tombstones, runs, and history. It contextually calls `add_slice.py` and `remove_slice.py`;
there is no classification plan artifact.
Selection behavior lives in
[`references/slice-selection.md`](references/slice-selection.md) and is loaded by the classifier.
The launcher automatically resolves global and changed-path review instruction chains into
scoped context. The classifier authors each complete reviewer prompt, including applicable review
policy with its wording and scope preserved. Without review policy, use broad reviews split by
content only as needed for context. Reviewers may read supporting context but report only on their
assigned changes.

Classification runs once per session. `classify_slices.py` fails while the session has active
slices. After a user directive removes every slice, the classifier can run again. Change the slice set only through
[Explicit user slice changes](#explicit-user-slice-changes). Otherwise rerun incomplete slices.

4. Run one review wave, then join it. Complete when you hold the wave's final JSON:

```bash
python3 "$SKILL_DIR/scripts/run_reviews.py" --review-dir "$REVIEW_DIR" \
  --child-timeout-seconds 3600
```

All eligible slices run in one parallel wave. A slice with `shots > 1` runs that many
independent reviewer shots of the same prompt in the wave; the runner marks a finding that a
sibling shot already reported as an automatic duplicate. Reviewers emit the strict
JSON shape in `references/review-result.schema.json`; the runner validates it, assigns
session-scoped `f_` IDs, and replaces the raw result with generated Markdown. Invalid results are
retryable slice failures. Consume only final JSON, finding IDs and Markdown paths in `out`, and
diagnostics in `err`. Each `out` record names its shot in `sh` and carries `final`. Treat each
finding as a hypothesis and validate it against the code and task.

5. Complete the chosen mode:

### Report

Return the validated, consolidated findings with file and line references. Include slice failures
as unavailable review coverage. Interpret `ok` as execution success and `rem` as follow-up
eligibility. Complete Report mode after consuming the wave for any `rem` value.

On a requested follow-up, run another wave. Reuse the session to run eligible slices; apply
explicit user slice changes when the desired coverage changed. Initialize a new session for an
independent repeat of every slice or a changed target.

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

Run another wave after fixes. A slice also completes when all findings in its latest wave are
ignored or deduplicated.

Each slice has a pass budget of `max_passes` (default 3). When a slice still has findings after
its budget, the next `run_reviews.py` call runs a clean judge for that slice before any wave. The
JSON `judge` array lists each verdict with its reason:

- `continue`: the slice earned another `max_passes` window. The judge prompt asks for a reason
  that names a design seam or fix-quality problem. The runner does not validate the reason text.
  Act on the reason before the next wave.
- `stop`: the slice is complete. Its last-wave findings return in `out` with `"final":true`.
  Fix or ignore every `final` finding without running another wave for it.

A judge failure returns an `err` record with `"st":"judge_failed"` and leaves the slice untouched;
rerun `run_reviews.py` to retry it. A verdict that contradicts the judge rule, in either direction,
is also a judge failure. Finish when every non-final finding is fixed or recorded
terminal, every `final` finding is fixed or ignored, relevant checks pass, and JSON returns
`"ok":true` and `"rem":0`.

## Joining long runs

`classify_slices.py` and `run_reviews.py` can run for an hour or more. Start each one, then
_join_ it: hold its final JSON before the next step. Pick the most efficient join the harness
offers: a background run with an exit notification first, a foreground run with the longest
timeout last.

Until the join completes, keep the review target _frozen_: reviewers read live Git. Run one
script per review directory at a time, and let it run to exit.

If a wave join breaks (detach, timeout, lost session), rejoin with:

```bash
python3 "$SKILL_DIR/scripts/await_reviews.py" --review-dir "$REVIEW_DIR"
```

## Explicit user slice changes

Only an explicit user request authorizes parent-driven mutation. Supply a complete prompt using
the reviewer-prompt contract in `references/classifier-rules.md`, and preserve that request:

```bash
python3 "$SKILL_DIR/scripts/add_slice.py" \
  --review-dir "$REVIEW_DIR" \
  --name "<slice-name>" \
  --harness "<harness>" \
  --prompt-file "<prompt-file>" \
  --user-directive-file "<verbatim-user-request-file>"
```

`--shots <n>` sets the number of parallel reviewer shots per pass. Omit it to use the configured
default. Every shot that returns no kept findings reduces later waves for that slice by one shot,
down to one. `--shot-passes <n|always>` sets how many passes, counted from the slice definition,
run more than one shot; later passes run one shot. Omit it to use the configured default of `1`.

Remove a slice with the same authority marker:

```bash
python3 "$SKILL_DIR/scripts/remove_slice.py" \
  --review-dir "$REVIEW_DIR" \
  --name "<slice-name>" \
  --user-directive-file "<verbatim-user-request-file>"
```

Removal tombstones the slice; re-adding its name reactivates it. Definitions may change, while
runs, outputs, and history remain. A reactivated slice gets a new `max_passes` window.

Treat scripts as sole owners of state, locking, output names, rendering, retries, and completion.
Do not edit generated review Markdown. Keep `.review/` uncommitted.
