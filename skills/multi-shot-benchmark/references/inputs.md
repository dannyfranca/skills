# Inputs and case selection

A minimal run specification has this shape:

```json
{
  "repo": "/path/to/repository",
  "driver": {"harness": "codex", "model": "your-driver-model", "reasoning": "high"},
  "cases": [{"head": "commit-id", "task_file": "task.md"}],
  "arms": [
    {"name": "a", "config": "review-a.toml"},
    {"name": "b", "config": "review-b.toml", "variant": "selected-variant"}
  ]
}
```

Paths resolve from the specification file. Case names are optional. Add `base` for an explicit
ancestor range or a merge commit. Repetitions default to two. Arm configs use the complete
current multi-shot config surface, including shots, pass behavior, harness, model, reasoning,
classifier, judge, and variants. A variant must be explicit when its file defines A/B alternatives.
Use a fixed driver profile for every assignment. Review arm changes are the experimental treatment.

Keep run assets outside the source repository. Explicit arm configs bypass live A/B selection;
live configs, balance, and outputs remain unchanged. For root registration and concurrent runs,
read [storage and concurrency](runs.md#storage-and-concurrency). Defaults follow the worktrees
convention. Current installed review capabilities run against historical code and historical
repository instructions. Record environment gaps instead of presenting the replay as a perfect
historical environment.

## Recover a task

Run `recover-task --repo <repo> --head <commit> --output <benchmark-root>/context/candidates.json`.
This reads associated GitHub PR descriptions and linked issue descriptions. It reads no review
comments. Recovery is best effort and uses the GitHub origin. Cross-repository linked issues retain
their own URLs. Other hosting drivers are not supported.

Inspect the candidates. Select the PR that describes the chosen diff. Prepare a text file with the
original change goal, acceptance criteria, and constraints. Keep only the requirements known for the original change. Then run `select-task --recovered <candidates.json> --pr <number>
--text <selected-text.md> --task <task.md>`. Setup reads the generated provenance sidecar.
You can instead supply your own task file and optional provenance in the case entry.

GitHub returns current descriptions, which may contain later edits. Record that uncertainty. If
descriptions are missing, ambiguous, or insufficient, ask for requirements. Use supplied
requirements before setup; a patch alone is insufficient. Raw candidates and known-defect notes
stay in parent storage under the benchmark root; the selected task alone is copied into each
child. The child still reviews the whole original selected diff.

## Select a sample

Start with five real changes, two arms, and two repetitions: 20 complete workflows. This pilot
checks reconstruction, isolation, accounting, and report usefulness. It can suggest a
hypothesis. It cannot establish superiority.

For a substantive comparison, consider 20–30 varied real changes before adding more repetitions.
Cover relevant domains, small and large diffs, simple and difficult tasks, and different defect
types. Include changes with independently confirmed defects and clean changes when available.
Keep that knowledge in parent notes. Give children only the original requirements.

Select cases with a rule independent of the tested model’s prior findings. State the selection
rule, exclusions, missing historical requirements, and any known exposure of the tested models
to these changes. Compare paired case/repetition groups. Preserve incomplete workflows and
rejected findings. Use real historical diffs throughout the comparison.