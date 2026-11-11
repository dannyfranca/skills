---
name: implement-issues-github
description: Implement GitHub issues in isolated worktrees through to merged PRs
---

# Implement GitHub Issues

Read [`../to-tickets-gh/references/tracker.md`](../to-tickets-gh/references/tracker.md) completely. Resolve every argument as a selector. Ask for a target when the request selects none:

- leaf issue(s): exactly those issues
- Epic(s): their open descendant leaves
- `repo`: every leaf in the repository
- any combination of explicit leaves and Epics, where Epics resolve to leaves
- One PR per leaf
- Run up to 5 workers in parallel, or the limit the user names. A worker is a subagent that owns one leaf from step 4 through step 10.
- You MUST run `/multi-shot-review` before committing and pushing the branch.

If an explicit leaf is blocked outside the selection, show the required expansion and get user confirmation before adding issues.

## Loop

1. Refresh ticket states: label every unblocked, unassigned Backlog leaf `ready-for-agent`; remove that label from blocked leaves. Continue when every affected label matches the refreshed state.
2. Select unassigned `ready-for-agent` leaves in scope, up to the free worker slots. Continue with the selected leaves, or evaluate the completion criterion when none remain and no worker runs.
3. Mutate the Metadata of each selected leaf:
   - Assign it to the user you are authenticated as
   - add `in-progress`
   - remove `ready-for-agent`
   - Continue when GitHub reflects all three changes.
4. Dispatch one worker per selected leaf. The worker derives a short kebab-case feature slug from its title. Use `/worktrees` with task slug and branch `issue-<leaf-number>-<feature-slug>`. Continue when the hydrated worktree is on that branch.
5. Implement it using `/implement`
6. Run `/multi-shot-review`. Continue until the review is quiet.
7. Commit and push the branch with `/micro-commits`.
8. Open a `ready-for-review` PR whose body contains `Closes #<leaf-number>`. Continue when the commit, remote branch, and PR exist.
9. Merge the PR at the first allowed point, unattended through required checks. Rebase onto the default branch when the PR conflicts. Continue when GitHub shows the PR merged and its issue closed.
10. From the main checkout, run `git worktree remove` without `--force` and delete the local branch. Leave a refused worktree to `worktree-janitor`. Continue when `git worktree list` omits it, or Git refused.
11. When a worker finishes, refresh dependent leaves. Close an Epic when all descendants close; reopen it when an open descendant appears. Continue when dependent labels and Epic states match their descendants.
12. Return to step 1 to fill free worker slots, until the completion criterion holds.

## Completion Criterion

The run finishes when a state refresh finds no `ready-for-agent` leaf in the selection and every leaf handled by the run has passed step 10.
