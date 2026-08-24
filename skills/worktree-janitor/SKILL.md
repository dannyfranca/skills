---
name: worktree-janitor
description: Periodic cleanup and recovery for ephemeral Git worktrees.
disable-model-invocation: true
---

# Worktree Janitor

Use `scripts/worktree_janitor.py`. Its defaults are the cleanup policy; pass overrides only when the user explicitly changes that policy.

## Sweep

Run a dry sweep first:

```bash
python3 "$HOME/.agents/skills/worktree-janitor/scripts/worktree_janitor.py" sweep
```

Complete when its JSON report has been read and every failure or safety hold is accounted for. Dry-run mode makes no worktree, Git-ref, or state mutation.

Run an applied sweep only when the request or automation explicitly selects `--apply`:

```bash
python3 "$HOME/.agents/skills/worktree-janitor/scripts/worktree_janitor.py" sweep --apply
```

Complete when the report has `status: "ok"`; otherwise report the failed operations and preserved paths. Exit `0` includes expected safety holds, exit `1` means audit/tool failure, and exit `2` means partial mutation failure.

## Recovery

List recoverable snapshots:

```bash
python3 "$HOME/.agents/skills/worktree-janitor/scripts/worktree_janitor.py" archives
```

Restore one into a new explicit path inside `~/.worktrees`:

```bash
python3 "$HOME/.agents/skills/worktree-janitor/scripts/worktree_janitor.py" restore <archive-id> --path "$HOME/.worktrees/<name>"
```

Complete when Git reports the detached worktree and the JSON result names its path and archive ref.
