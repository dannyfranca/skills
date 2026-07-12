---
name: worktrees
description: Git worktree location and setup conventions. Use when creating a worktree for a project.
---

Create worktrees at `<repo-root>/.worktrees/<task-slug>`.

Use a short kebab-case task slug. Ensure `.worktrees/` is gitignored.

Use the project's hydration script for fresh worktrees. If none exists, perform sensible setup for that project.
