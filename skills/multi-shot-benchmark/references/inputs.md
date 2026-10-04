# Inputs and case selection

Use one interface: `scripts/benchmark.py`. Its `--help` lists commands and flags.
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

Run assets belong under a separate benchmark root. Use a shared root for concurrent runs on one
host. Worktree roots must be separate. Defaults follow the worktrees convention. Register all roots
before any run starts. Current installed review capabilities run against historical code and
historical repository instructions. Record environment gaps instead of presenting the replay as
a perfect historical environment.

## Recover a task

Run `recover-task --repo <repo> --head <commit> --output <benchmark-root>/context/candidates.json`.
This reads associated GitHub PR descriptions and linked issue descriptions. It reads no review
comments. Recovery is best effort and uses the GitHub origin. Cross-repository linked issues retain
their own URLs. Other hosting drivers are not supported.

Inspect the candidates. Select the PR that describes the chosen diff. Prepare a text file with the
original change goal, acceptance criteria, and constraints. Remove later review findings, outcomes,
and follow-up fixes. Then run `select-task --recovered <candidates.json> --pr <number>
--text <selected-text.md> --task <task.md>`. Setup reads the generated provenance sidecar.
You can instead supply your own task file and optional provenance in the case entry.

GitHub returns current descriptions, which may contain later edits. Record that uncertainty.
If descriptions are missing, ambiguous, or insufficient, ask for requirements. Do not invent them
from the patch. Raw candidates and known-defect notes stay in parent storage; the selected task
alone is copied into each child. The child still reviews the whole original selected diff.

## Select a sample

Start with five real changes, two arms, and two repetitions: 20 complete workflows. This pilot
checks reconstruction, isolation, accounting, and report usefulness. It can suggest a hypothesis.
It cannot establish superiority.

For a substantive comparison, consider 20–30 varied real changes before adding more repetitions.
Cover relevant domains, small and large diffs, simple and difficult tasks, and different defect types.
Include changes with independently confirmed defects and clean changes when available. Keep that
knowledge in parent notes. Do not put expected findings in child requirements.

Avoid selecting only changes that one model already found. State the selection rule, exclusions,
missing historical requirements, and any known exposure of the tested models to these changes.
Compare paired case/repetition groups. Preserve incomplete workflows and rejected findings.
Do not replace real diffs with fabricated faults to make a convenient score.
