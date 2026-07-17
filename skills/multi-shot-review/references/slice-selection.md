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
Native slices receive the matching `--uncommitted`, `--base`, or `--commit` flag.

## Rules convention

Projects may add `REVIEW.md` at repository root or in nested directories. Nested files apply to
changed descendants. One global file is supported at `~/.agents/REVIEW.md`.

Precedence:

1. Explicit user directions.
2. Closest scoped `REVIEW.md` / `AGENTS.md`.
3. Repository-root review, agent, contributing, and coding-standard files.
4. Global `~/.agents/REVIEW.md`.
5. Built-in lens rules.

At the same directory, `REVIEW.md` precedes `AGENTS.md`. At repository root, order is `REVIEW.md`,
`AGENTS.md`, lexically sorted `CONTRIBUTING*`, then lexically sorted `CODING_STANDARDS*`. Only the
closest directory containing scoped review or agent rules applies to a changed file; intermediate
scoped directories are not stacked.

The classifier and reviewers read sources directly; the parent does not summarize them.

## State mutations

Use `add_slice.py` for new focused or native slices. Adding a removed name reactivates it while
preserving its runs and history. Use `remove_slice.py` to tombstone an obsolete slice. Successful
mutations remain if classification stops early; the next clean classifier reasons from that state.
To revise an active classifier slice, remove it and add the same name with its new definition.

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

Use `--file <path>` or `--dir <path>` for larger related tasks. Reclassify when deferred-work
context materially changes selection.
