# Frozen paired runs

Use one repository per run. Supply `repo`, `cases`, `arms`, and a fixed `driver` profile.
Each case has `head`, optional `base`, and `task_file`. A lone commit uses its parent.
Merge commits need a base. Names are optional for cases and required for arms.
Each arm has `name`, `config`, and an optional explicit `variant`. Config files accept the full
current review surface. Repetitions default to two. No benchmark token or time cap is added.

Start with five real changes, two arms, and two repetitions: 20 complete workflows.
Use a larger, varied sample before you make a substantive model comparison.

`manifest.freeze` resolves refs and copies task text, effective configs, source configs, and the current review tool.
It fixes every assignment before any driver starts. `scheduling.run` prepares all worktrees,
then overlaps arms for each case and repetition. Arm submission order rotates across cases
and repetitions. Driver attempts and scheduler launch times record the actual order and overlap.
Submission order does not guarantee start order. One host shares CPU, disk, caches, network,
and model capacity. These measurements do not provide isolated performance results.

Resume with `scheduling.run`. Completed assignments are skipped. Failures remain in their own
execution and do not replace successful siblings. Each retry remains an attempt of its original
assignment. A scheduler lock protects each run. One execution lock protects setup, resume, each child, and cleanup. Failed setup assets remain
in `setup-failures` before the same assignment is prepared again. On interruption, the scheduler
stops only its owned drivers, joins them, and saves terminal launch records.

Different runs can overlap when they use the same benchmark root. A host registry rejects
overlap across different roots. Children hide all registered storage and worktree roots, including completed runs.
Storage and worktree roots must be disjoint across all registered runs.
Run outputs cannot be nested inside another run. Launch records name the actual driver
attempt and identify recovered completion that starts no new driver. Keep one storage root for the host. Register all worktree roots before any run starts.
New roots cannot be registered while a scheduler or direct driver is active. This does not hide unknown copies or remote sources.

Inspect `scheduling.progress` for saved status. Cleanup requires the run lock and each execution
lock. It locks classification and review state, checks active drivers and review reservations, saves tracked and untracked source evidence, and removes only
owned worktrees. Private history, tasks, configs, logs, sessions, and results remain available.
Cleaned unfinished executions cannot resume. Use cleanup after assessment or deliberate abandonment.
