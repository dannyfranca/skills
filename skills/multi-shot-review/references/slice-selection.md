# Slice Selection

Slice selection belongs to one clean classifier session. The parent initializes the review, may
provide supplemental user directions and advisory context, invokes the classifier, then consumes
ordinary review state. The classifier alone chooses and contextually manages slices.

The authoritative selection rules are in
[`classifier-rules.md`](classifier-rules.md).

## Review targets

The session stores only a target descriptor:

- `uncommitted`: inspect `git status`, staged and unstaged diffs, and relevant untracked files.
- `base`: inspect the current `git diff <base>...HEAD`.
- `commit`: inspect the named commit.

This is live Git context, not a pinned snapshot. Focused prompts state the target explicitly.
The classifier writes that target into each complete slice prompt.

## Scoped classifier guidance

The launcher may provide guidance already resolved to changed-path scopes. Discovery, precedence,
source filenames, and loading are launcher responsibilities, not classifier responsibilities.

Use applicable guidance when selecting slices and deliver review policy directly in each prompt
as specified by `classifier-rules.md`. Review-instruction discovery and inheritance remain the
launcher's responsibility.

## State mutations

Use `add_slice.py --prompt-file` for new slices. Adding a removed name reactivates it while
preserving its runs and history. The reactivated definition gets a new pass window. Use `remove_slice.py` to tombstone an obsolete slice. Successful
mutations remain if classification stops early; the next clean classifier reasons from that state.
To revise an active classifier slice, remove it and add the same name with its new definition.

Pass `--harness <harness>` only when a specific harness materially suits a slice. Apply the same
rule to `--model <model>` and `--reasoning <effort>`. Omitting them uses the configured slice
profile or harness defaults. Pass `--shots <n>` only when scoped guidance asks for parallel
reviewer shots on that slice. Apply the same rule to `--shot-passes <n|always>`, which sets how many
passes of a slice run more than one shot. A guidance rule can use the change size and the slice
verbosity as factors. All selections are persisted with the slice and snapshotted per run.

Classifier calls normally omit `--user-directive-file`. A parent acting on an explicit user request
supplies that file, making the mutation user-controlled. A classifier may pass a forwarded
supplemental user-directions file only when its text explicitly authorizes changing that
user-controlled slice.

## Related work

Register intentionally deferred work so reviewers distinguish it from missing scope:

```bash
python3 "$SKILL_DIR/scripts/add_related_task.py" \
  --review-dir "$REVIEW_DIR" \
  --name <task-name> \
  --text "<what will be addressed later>"
```

Use `--file <path>` or `--dir <path>` for larger related tasks.
