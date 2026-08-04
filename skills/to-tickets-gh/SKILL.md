---
name: to-tickets-gh
description: Publish tracer-bullet tickets as GitHub Issues
---

# To Tickets — GitHub

Read and follow `/to-tickets` completely. Read [`references/tracker.md`](references/tracker.md) completely and treat it as the configured tracker, replacing `/to-tickets`' real-tracker publication rules.

Publish blockers before dependents. Publishing is complete when every approved leaf belongs to the Epic, every dependency edge exists, and every leaf that is done by an agent is labeled `ready-for-agent`. Executions agents will check for blockers before tackling a leaf.
