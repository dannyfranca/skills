---
name: multi-shot-benchmark
description: Benchmark review models or configurations on real historical diffs; compare full workflow quality, effort, and cost.
---

# Multi-Shot Benchmark

The parent agent prepares the experiment, operates deterministic tools, assesses final code,
and writes the recommendation. Use one repository per run.

Set `SKILL_DIR` to the absolute directory that contains this file.
Use `python3 "$SKILL_DIR/scripts/benchmark.py" <command>`; use `--help` for flags.

## 1. Prepare

Read [inputs and sample selection](references/inputs.md) to select real diffs, original task
requirements, review arms, and a shared driver. When task requirements need recovery, follow
that file's **Recover a task** branch. Read [isolation](references/isolation.md) before choosing
storage roots or hidden sources. Use the [worktrees skill](../worktrees/SKILL.md) for worktree
conventions.

Run `setup`. Continue when the manifest contains immutable refs, selected task text, frozen
configs and tool assets, and every case/arm/repetition assignment.

## 2. Execute

Read [run behavior](references/runs.md), then run `run`. Drivers follow the
[current review skill](../multi-shot-review/SKILL.md), including classification, finding
validation, fixes, checks, and normal judge decisions. Keep its normal completion policy;
add no benchmark token, round, or time caps.

Join the command before changing inputs or measuring results. If it exits with incomplete
assignments, use `progress` and follow the **Resume** branch in run behavior.
Continue when every assignment is completed, or the parent records why each incomplete
assignment remains unavailable for this comparison.

## 3. Assess

Read [measurement definitions](references/measurements.md) and the **Assess final code**
branch in [decision reports](references/reporting.md). Run `measure` after all drivers stop.
Inspect every completed final patch, fix patch, check result, and relevant behavior. Record
an assessment with `assess` for each completed assignment.
Continue when every completed assignment has a current assessment with evidence and
explicit defect, regression, and dispute lists.

## 4. Report

Follow the **Write the decision** branch in [decision reports](references/reporting.md).
Measure again after assessment, write the contextual recommendation, and run `report`.
Continue when the saved report is final, uses the current measurement basis, and accounts
for every case and incomplete assignment. Share its Markdown and JSON paths.

## 5. Clean up

Follow the **Cleanup** branch in [run behavior](references/runs.md).
Finish when its completion criterion accounts for every assignment and retained worktree.
