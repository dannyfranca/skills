---
name: implement-issues-github
description: Implement GitHub issues in isolated worktrees with review or autonomous PR handling
---

# Implement GitHub Issues

Read [`../to-tickets-gh/references/tracker.md`](../to-tickets-gh/references/tracker.md) completely. Resolve `autonomous` as a mode modifier and every other argument as a selector:

- leaf issue(s): exactly those issues
- Epic(s): their open descendant leaves
- `drain-ready`: repeatedly query the repository-wide Ready frontier until empty
- any combination of explicit leaves and Epics

## Modes

- `review` (default): a leaf completes when its ready-for-review PR is open.
- `autonomous`: a leaf completes when its PR is merged and the merge closes its issue. Continue unattended through required checks and merge at the first allowed point.

If an explicit leaf is blocked outside the selection, show the required expansion and get user confirmation before adding issues. Execute sequentially.

## Loop

1. Refresh ticket states: label every unblocked, unassigned Backlog leaf `ready-for-agent`; remove that label from blocked leaves. Continue when every affected label matches the refreshed state.
2. Select an unassigned `ready-for-agent` leaf in scope. Continue with one leaf, or evaluate the completion criterion when none remain.
3. Assign it, add `in-progress`, and remove `ready-for-agent`. Continue when GitHub reflects all three changes.
4. Derive a short kebab-case feature slug from its title. Use `$worktrees` to create `<repo-root>/.worktrees/issue-<number>-<feature-slug>` on branch `issue-<number>-<feature-slug>`. Continue when the hydrated worktree is on that branch.
5. Implement it using `/implement`, then run `/multi-shot-review`. Continue when the review is quiet.
6. Commit and push the branch. Open a ready-for-review PR whose body contains `Closes #<number>`. Continue when the commit, remote branch, and PR exist.
7. Drive the leaf to its mode's completion state. Continue when GitHub reflects that state.
8. Refresh dependent leaves. Close an Epic when all descendants close; reopen it when an open descendant appears. Continue when dependent labels and Epic states match their descendants.
9. Return to step 1 until the completion criterion holds.

## Completion Criterion

- Explicit selection finishes when every selected leaf reaches its mode's completion state. In `review` mode, also finish when each remaining selected leaf is blocked solely by selected leaves at their completion state.
- `drain-ready` finishes when a state refresh finds no `ready-for-agent` leaf and every leaf handled by the run has reached its mode's completion state.
