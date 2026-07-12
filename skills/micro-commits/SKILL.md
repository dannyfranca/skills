---
name: micro-commits
description: Micro-commit code changes when asked to commit.
---

# Micro Commits

1. Inspect the repository state and pending diff. Partition in-scope changes into the smallest coherent commits, ordered prerequisites first.
2. When asked for guidance, present the ordered micro-commit sequence. Finish when every in-scope change belongs to one commit.
3. When asked to commit, execute the sequence. For each micro-commit:
   - Stage only its files or hunks; leave spec Markdown and unrelated changes unstaged.
   - Verify the staged diff is independently coherent and contains one concern unless separation would break it.
   - Commit with a concise conventional subject.

Finish when every planned micro-commit exists and only intentionally uncommitted changes remain.
