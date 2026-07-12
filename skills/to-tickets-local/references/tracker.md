# Local Ticket Tracker

- Root: the Git repository root when inside one; otherwise the execution directory.
- Ticket set: `.tickets/<YYYYMMDD-HHmm>-<feature-slug>/`, using local creation time.
- Shape: one Markdown file per ticket, named `<NN>-<slug>.md`, using `/to-tickets`' local template.
- Order: number blockers before dependents, starting at `01`; record blocking edges in each file.
- Durability: keep `.tickets/` unversioned. In a Git repository, add it to `.git/info/exclude` when needed.
