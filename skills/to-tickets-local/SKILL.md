---
name: to-tickets-local
description: Publish tracer-bullet tickets as unversioned local files
---

# To Tickets — Local

Read and follow `/to-tickets` completely. Read [`references/tracker.md`](references/tracker.md) completely and treat it as the configured tracker, replacing `/to-tickets`' local publication path.

Size each ticket at roughly 700–1000 changed lines, tests excluded. The range is a rough guide that sets breakdown granularity; it refines `/to-tickets`' single-context-window rule.

Publishing is complete when every approved ticket exists in the ticket-set directory and every blocking edge names an existing ticket there.
