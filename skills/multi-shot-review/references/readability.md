# Readability Review

Review whether a future maintainer or agent can understand the changed code locally and accurately.

- Names should communicate intent and domain meaning.
- Extract a function when a coherent chunk gains a useful name and reduces cognitive load.
- Comments should explain reasoning, constraints, external facts, or non-obvious tradeoffs—not narrate code that should explain itself.
- Flag godfiles when the change creates, worsens, relies on, or should reasonably resolve mixed responsibilities.
- Prefer short, single-responsibility files, but keep one-use helpers collocated when splitting would scatter the idea.
- Avoid deeply nested control flow, hidden mutation, distant setup, and needless indirection.

Do not report unrelated pre-existing readability debt.
