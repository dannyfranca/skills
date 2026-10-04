---
name: multi-shot-benchmark
description: Compare review models or configurations by replaying real historical diffs through isolated full multi-shot review workflows, then assess quality, effort, and cost.
---

# Multi-Shot Benchmark

Use one repository per run. The parent agent prepares and operates the benchmark, assesses final
code, and recommends an action. Deterministic scripts manage assets and measurements. Reviewers
and drivers use normal multi-shot behavior. There is no assessor harness or winner formula.

Set `SKILL_DIR` to the absolute directory that contains this `SKILL.md`.
Use `python3 "$SKILL_DIR/scripts/benchmark.py" <command>`. Read
[inputs and case selection](references/inputs.md) before setup. Read
[run behavior](references/runs.md) before execution. Use the
[worktrees skill](../worktrees/SKILL.md) for worktree conventions and the
[current review skill](../multi-shot-review/SKILL.md) for the review process.

## Prepare

Choose real commit diffs or ancestor base/head ranges. Review the whole selected diff. A lone
commit uses its parent; a merge commit needs an explicit base. Freeze immutable refs, task text,
full arm configs, and one shared driver profile. Every case, arm, and repetition gets a separate
worktree, private history, tool copy, session, runtime state, and output.

Recover GitHub descriptions with `recover-task` when a task file is unavailable. Review the
candidates as the parent. Use `select-task` with text that contains only the original task.
Descriptions may have changed since the commit. Exclude later reviews, outcomes, and fixes.
When requirements remain missing, ask the user for them before setup. A diff alone is insufficient.
Keep raw recovery candidates and any known-defect notes in parent storage under the benchmark
root. Only selected original requirements enter child task files.

Setup completes when `setup --spec <spec.json> --run <run-directory>` has frozen the manifest and
all assignments. Benchmark outputs must be outside the source repository. Explicit arm configs
bypass live A/B selection and never update its configs, balance, or outputs.

## Run and resume

`run --run <directory>` starts the workflows. Join the command before reading final results or
changing inputs. Drivers classify the whole diff, validate findings, fix or reject them, run checks,
and repeat until normal completion. Normal judges can continue or stop. Do not add benchmark
token, round, or time limits. A stopped judge's final findings still need fixes or terminal decisions.

Execution completes when progress shows every assignment completed. A partial exit retains
failed and interrupted evidence. Use `progress` to inspect it and `resume` to retry the same
assignments. Completed siblings are skipped. Never restart a still-running command.
If a historical environment cannot run, record that limitation; do not silently change the case.

Read [isolation](references/isolation.md) when choosing roots or hidden sources. It describes
shared host load and the practical leakage boundary. Network sources and unknown clones remain
possible. The parent must keep later history, sibling outcomes, and benchmark hypotheses out of
child prompts. Stronger isolation requires an appropriate external container or network policy.

## Assess and report

Read [measurements](references/measurements.md) and
[decision reports](references/reporting.md) before assessment. Run `measure` after drivers stop.
Inspect every completed final patch, fix patch, checks, and relevant behavior as the parent. Record
an evidence-backed assessment with `assess`; an empty defect list is an assessment result, while
missing assessment is unknown quality. Resolve or explain disputed findings. Review silence alone
is insufficient evidence of quality.

Write the recommendation yourself. Compare first-pass detection with the complete process,
final defects and regressions, rounds, actual driver effort, role usage, elapsed time, and dated
cost estimates. Discuss task difficulty, repetitions, missing coverage, classification differences,
historical requirement gaps, shared load, and project priorities. Allow an inconclusive result.
A small pilot does not establish model superiority.

Reporting completes when `report` saves a final report with current assessments for completed
executions and the parent's reasoned decision bound to current measurements. A report without a
decision is a draft. Partial runs can support an explicit inconclusive decision. Report the remaining
coverage. Share the durable Markdown report and its JSON evidence snapshot.

Use `cleanup` after assessment or deliberate abandonment. It removes only inactive owned
worktrees and saves source evidence. Logs, tasks, configs, private history, sessions, assessments,
and reports remain. Cleaned unfinished executions cannot resume.
