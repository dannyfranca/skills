---
name: implement-issues-github
description: Implement selected GitHub issues sequentially with extensive reviews
---

# Implement GitHub Issues

Read [`../to-tickets-gh/references/tracker.md`](../to-tickets-gh/references/tracker.md) completely, then resolve the selector:

- leaf issue(s): exactly those issues
- Epic(s): their open descendant leaves
- `drain-ready`: repeatedly query the repository-wide Ready frontier until empty
- any combination of explicit leaves and Epics

If an explicit leaf is blocked outside the selection, show the required expansion and get user confirmation before adding issues. Execute sequentially.

## Loop

1. Refresh ticket states: label every unblocked, unassigned Backlog leaf `ready-for-agent`; remove that label from blocked leaves.
2. Select an unassigned `ready-for-agent` leaf in scope.
3. Assign it, add `in-progress`, and remove `ready-for-agent`.
4. Implement it using `/implement`, then run `/multi-shot-review` until quiet.
5. Commit, close the issue, and link the commit or requested PR.
6. Refresh dependent leaves. Close an Epic when all descendants close; reopen it when an open descendant appears.
7. Repeat.

## Completion Criterion

Explicit selection finishes when every selected leaf is closed. `drain-ready` finishes when a state refresh finds no `ready-for-agent` leaf.
