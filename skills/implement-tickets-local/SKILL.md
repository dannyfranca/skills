---
name: implement-tickets-local
description: Implement local ticket files sequentially with extensive reviews
---

# Implement Local Tickets

Read [`../to-tickets-local/references/tracker.md`](../to-tickets-local/references/tracker.md) completely.

Resolve the requested ticket-set directory or explicit ticket files. When selection is ambiguous, ask the user. Create `_implementation-checklist.md` beside the selected tickets with one unchecked entry per ticket.

## Loop

1. Select an unchecked ticket whose blockers are complete.
2. Implement it using `/implement`.
3. Run `/multi-shot-review` until quiet.
4. Mark it complete in the checklist and commit the implementation.
5. Repeat.

If every remaining ticket is blocked outside the selection, show the required expansion and get user confirmation before adding tickets.

## Completion Criterion

Finish when every selected checklist entry is checked and every selected ticket's acceptance criteria are satisfied.
