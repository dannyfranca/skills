# Run behavior

## Execute

Run `run` through the skill's CLI. Setup freezes assignments before any driver starts.
The scheduler prepares all worktrees, then overlaps arms for each case and repetition.
Arm submission order rotates across cases and repetitions. Saved launch records show the
actual order and overlap; submission order alone cannot establish start order.

Join the command and retain its final result. A completed assignment has normal review
completion and validated final artifacts. A stopped judge's final findings still require
fixes or terminal decisions. If the historical environment cannot run, record the failure
and its effect on comparison coverage.

## Resume

Use `progress` to inspect saved status. Resume an exited, incomplete run with `resume`.
Completed assignments are skipped. Each retry remains an attempt of its original assignment;
successful siblings and failed evidence remain available. Failed setup assets stay in
`setup-failures` before the same assignment is prepared again.

On interruption, the scheduler stops its owned drivers, joins them, and saves terminal launch
records. A recovered completion can finish an assignment without a new driver attempt.
Continue when the command exits and `progress` accounts for every assignment as completed
or explicitly unavailable. Keep active commands joined until they exit.

## Storage and concurrency

Use one benchmark root for concurrent runs on a host. Keep storage and worktree roots disjoint
and run outputs separate from each other. Register all roots before starting drivers; the host
registry rejects new roots while a scheduler or direct driver is active. Children hide all
registered roots, including completed runs. See [isolation](isolation.md) for access boundaries.

One host shares CPU, disk, caches, network, and model capacity. Interpret elapsed time with
that shared load in mind. Driver attempts and scheduler records provide evidence of overlap.

## Cleanup

If any unfinished assignment needs a retry, record its retry plan and preserve the run's
worktrees. The CLI cleans the whole run; defer cleanup until every assignment is completed
and assessed, or deliberately abandoned.

Run `cleanup` when that condition holds. Run, execution, and review locks protect active work.
Cleanup checks driver and review activity, saves tracked and untracked source evidence, and
removes inactive owned worktrees. Private history, tasks, configs, logs, sessions, assessments,
and reports remain available. Cleaned unfinished assignments cannot resume.

Cleanup is complete when every assignment has saved cleanup evidence and a removed worktree,
or every retained worktree has a recorded retry plan or active owner. Retry cleanup after active
owners exit, once the decision to resume or abandon the unfinished work is recorded.
