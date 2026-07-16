# Database Review

Apply the declared database lenses to the narrow database area.

- **Correctness:** query semantics, constraints, nullability, migrations, rollback assumptions, and data invariants.
- **Concurrency:** transaction boundaries, isolation, locking, races, retries, idempotency, and atomicity.
- **Indexing:** access patterns, selectivity, ordering, indexes, query plans, and write amplification.
- **Execution coverage:** verify changed queries execute against a realistic database. When Testcontainers or an equivalent real-database harness already exists, require the changed behavior to use it. When absent, do not demand introducing that infrastructure solely for this change; still assess available behavioral evidence.
