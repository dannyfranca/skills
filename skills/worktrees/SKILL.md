---
name: worktrees
description: Git worktree location and setup conventions. Use when creating a worktree for a project.
---

Ensure the source branch is up to date with the remote branch, or create from remote if needed.

Create worktrees at `~/.worktrees/<YYYYMMDD-HHMM>-<repo-name>-<task-slug>`. Generate the directory name with `scripts/worktree-name.sh <task-slug>`.

Use a short kebab-case task slug.

Use the project's hydration script for fresh worktrees. If none exists, perform sensible setup for that project.
