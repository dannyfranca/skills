# Multi-Shot Review

Human-facing configuration and review-instruction reference for the
[`multi-shot-review`](SKILL.md) skill.

## Configuration

Configuration files are named:

```text
.agents/multi-shot-review.toml
```

The resolver starts at `$HOME` and loads each configuration down the directory chain to the
repository root. The nearest value wins. Execution profiles are atomic: a nearer `classifier`,
`slice_default`, or `judge` table replaces the whole parent profile.

`init_state.py` resolves the config one time, when it creates the session. The session state keeps
a snapshot of the effective config. All scripts of that session use the snapshot. A config change
applies from the next session.

All settings are optional. Suggested defaults (replace each `<model>` with a model ID that the
harness supports, or delete the `model` line to use the harness default):

```toml
review_file = "REVIEW"
max_passes = 3
shots = 1
shot_passes = 1

[classifier]
harness = "codex"
model = "<model>"
reasoning = "high"

[slice_default]
harness = "claude-code"
model = "sonnet"
reasoning = "high"

# With no [judge] table in any config of the chain, the judge uses the classifier profile.
[judge]
harness = "codex"
model = "<model>"
reasoning = "medium"
```

- `review_file`: review-instruction basename. Defaults to `REVIEW`. It must not contain a path or
  the `.md` suffix.
- `max_passes`: review passes a slice may run before the judge decides. Defaults to `3`. Must be a
  positive integer.
- `shots`: reviewer shots per pass for a slice that `add_slice.py` creates without `--shots`.
  Defaults to `1`. Must be a positive integer.
- `shot_passes`: number of passes at the start of a slice definition that run more than one shot.
  Applies to a slice that `add_slice.py` creates without `--shot-passes`. Defaults to `1`. Must be a
  positive integer or `"always"`.
- `classifier`: harness profile used by the slice classifier.
- `slice_default`: harness profile used when a slice does not override it.
- `judge`: harness profile used by the pass-budget judge. When no config in the chain has a
  `[judge]` table, the judge uses the `classifier` profile.
- `harness`: required profile field. Supported IDs are `codex` and `claude-code`.
- `model` and `reasoning`: optional, non-empty, harness-specific strings.

Unknown settings, incomplete profiles, invalid values, and the former flat model/reasoning keys are
rejected. With no profile, the harness defaults to `codex`; model and reasoning remain the harness
defaults.

`classify_slices.py` and `add_slice.py` accept `--harness`, `--model`, and `--reasoning` overrides.
Changing the configured harness without also overriding model/reasoning clears those choices, so a
Codex model cannot leak into Claude Code or vice versa. An explicitly selected unavailable harness
fails; it never falls back silently.

`REVIEW.md` guidance may tell the classifier to select a harness for applicable slices. For adding
another built-in, see [Extending review harnesses](docs/extending-harnesses.md).

### Variants

Variants rotate config setups between sessions, so you can compare their outputs later. There is no
automatic rollout.

```toml
shots = 1

[variants]
default = 1
a = 2

[variant.a]
shots = 2

[variant.a.classifier]
harness = "claude-code"
```

- `[variant.<tag>]`: a variant body. It accepts the same settings as the main config. Its settings
  replace the effective main settings. Profile tables stay atomic.
- `default`: the reserved tag for the main config without a body. `[variant.default]` is rejected.
- Tags must match `[a-z0-9][a-z0-9_-]*`.
- `[variants]`: optional integer weights. A weight is relative to the sum of all weights. A body
  without a weight gets `1`. `default` gets `1` unless you set it. Thus, bodies without weights give
  an equal split with `default`.
- A weight of `0` removes that tag from the draw.

These experiments are rejected: a weight without a body, a negative, float, or boolean weight, a sum
of `0`, an unknown or nested setting in a body, and an invalid tag.

Main settings merge down the chain as usual. The nearest config that has `[variants]` or
`[variant.*]` owns the full experiment. Experiments from parent configs are ignored.

`init_state.py` draws one tag per session. `--variant <tag>` forces a tag, also a tag with weight
`0`. Use it only on explicit user request. An unknown tag fails.

The session stores the tag as `session.variant`. The tag is `default` when there are no variants.
Review Markdown frontmatter and the run summary JSON also include `variant`.

## Passes, shots, and the judge

### Classification

Classification runs one time for each session. `classify_slices.py` fails when a slice is active.
For a later slice change, use `add_slice.py` or `remove_slice.py` with a user directive. When a user
directive removes all slices, you can run the classifier again in the same session.

### Passes

Each slice runs one pass in each wave. The slice continues until a wave gives no kept findings. A
kept finding is a finding that the parent did not ignore or deduplicate.

Each pass gets the finding history of the current slice definition. Each history item shows its
outcome:

- Superseded by a later pass. The reviewer makes sure that the finding is fixed.
- Rejected. The history shows the reason word for word.
- Duplicate.

The prompt tells the reviewer to report all high-value findings in the current run.

### Shots

`add_slice.py --shots <n>` sets the number of shots. A shot is an independent reviewer run of the
same prompt in the same wave. Without `--shots`, the slice uses the configured `shots` value. The
default is `1`.

The runner marks some findings as automatic duplicates. The conditions are:

- A sibling shot of the same wave reported a finding at the same path.
- The line ranges overlap.
- The title token overlap is 0.6 or more.

The resolution of an automatic duplicate has `"auto": true`. The runner keeps the copy with the
highest severity.

`add_slice.py --shot-passes <n|always>` sets the number of passes that run more than one shot.
Without `--shot-passes`, the slice uses the configured `shot_passes` value. The default is `1`.
Passes count from the start of the slice definition. After the window, each pass runs one shot.
With `always`, each pass runs the shots that the phase-down rule below gives. A judge `continue`
verdict does not open a new window. A slice removal and a new `add_slice.py` of the same name
opens a new window.

Inside the window, the number of shots decreases. Each shot that gives no kept findings removes one
shot from later waves. The minimum is one shot. The number never increases. When all shots of a
wave are clean, the slice is complete. When a shot fails or times out, the runner runs only that
shot again.

### Judge

When a slice has findings after `max_passes` passes, the judge runs before the next wave. The judge
is a clean, read-only session. It gets the full finding history of the slice and this rule:

- `continue`: the last pass has a kept P0 or P1 finding. The slice gets a new window of
  `max_passes` passes.
- `continue`: the same file has a kept P1 finding in two consecutive passes of the current window.
  This applies also when these passes are earlier than the last pass.
- `stop`: all other conditions.

A superseded finding is a kept finding.

The runner applies the same rule to the pass history. When the verdict does not agree with the
rule, the runner records a judge failure. This applies in the two directions.

The slice stores each verdict. The history also records it. The run summary returns it in the
`judge` array.

A `stop` verdict completes the slice. The open findings of the last wave return in `out` with
`"final": true`. The parent fixes or ignores these findings. The parent does not run another wave
for them.

The judge does not filter findings. When the judge fails, the slice does not change. The runner
reports `"st": "judge_failed"` in `err`. The next run tries the judge again.

### Judge guards

Each judged pass accepts one verdict. When two runners judge the same pass, the first verdict
applies. The runner ignores the second verdict and records `stale_judgement_ignored` in history.

Before a judge starts, the runner reserves the judged pass on the slice. Another runner does not
start a second judge for a reserved pass. When the process that holds a reservation stops, the
next runner takes the reservation again. Judge files get a random suffix. Thus, parallel judges
never write to the same file.

### Windows

Each window starts at the last `continue` pass. The runner stores `max_passes` when a window opens.
A config change applies from the next session. A reactivated slice definition gets a new window.
Verdicts for an earlier definition do not change it.

### Output files

Judge outputs are in `<review-dir>/judge/`. The review Markdown of a slice with more than one shot
has a `-shot<n>` suffix.

## Review-instruction resolution

With the default `review_file = "REVIEW"`, the loader recognizes:

```text
REVIEW.md
REVIEW.override.md
```

A custom basename such as `review_file = "SECURITY_REVIEW"` changes these to:

```text
SECURITY_REVIEW.md
SECURITY_REVIEW.override.md
```

Resolution behavior:

1. Load one global instruction from `$HOME/.agents`, preferring the override file.
2. Find repository directories applicable to the changed files.
3. Walk those directories from repository root toward each changed file.
4. At each directory, load at most one file, preferring the override file.
5. Accumulate the selected instructions in root-to-leaf order.

At project scopes, an override file masks the base file even when the override is empty. At the
global scope, an empty override falls through to a non-empty base file. Shared ancestor
instructions are loaded once.

The classifier receives resolved guidance with its path scopes. Repository guidance overrides
global guidance on conflicts; closer scopes override broader scopes; explicit user directions
take precedence. The classifier carries applicable review policy directly into each complete
slice prompt, preserving wording, scope, and precedence. The runner adds only the output-format
contract, without additional review criteria.

Without review policy, the classifier creates a broad review of the whole change when it fits
comfortably in one session, or splits it into coherent content slices to control context. There
are no mandatory lenses or slice counts. Reviewers may read any supporting context, but findings
and review opinions must concern their assigned changes only.

Classifier-created slices use `--prompt-file`, including whole-change reviews. The legacy native
target flags remain available for explicit whole-target slices; they carry no scoped review policy.

## Harness audit data

Execution selections are durable:

- Slice definitions store `harness`, `model`, `reasoning`, and their source fields.
- Every run snapshots all six fields, so later configuration or slice-definition changes do not
  alter prior run identity.
- Successful review Markdown artifacts include matching YAML frontmatter, with the session
  `variant` first.
- Classifier attempts store only harness/model/reasoning, timestamps, status, and exit code.

Harness sources are `slice-override`, `configured-default`, or `built-in-default`. Model and
reasoning sources are:

- `slice-override`
- `configured-default`
- `harness-default`

Harness-default runs store `model: null` and/or `reasoning: null`.

## Finding records

Reviewers return only the strict JSON document defined by
[`references/review-result.schema.json`](references/review-result.schema.json):

```json
{
  "schema_version": 1,
  "findings": [
    {
      "severity": "P1",
      "title": "Short title",
      "content": "Why this is actionable.",
      "location": {"path": "src/example.py", "start_line": 12, "end_line": 15}
    }
  ]
}
```

An empty `findings` array means no findings. The runner validates the document again, supplies an
immutable session-scoped ID shaped as `f_` plus eight NanoID characters, stores active finding
state in `_state.json`, and generates the human-facing Markdown artifact. Raw reviewer text is not
the durable record.

Record rejected findings individually with `scripts/ignore_finding.py --id ... --reason ...` (or
`--reason-file`). Record overlap with `scripts/dedupe_finding.py --id ... --canonical-id ...`; the
canonical finding must still be open. Automatic same-wave duplicates use the same resolution kind
with `"auto": true`. A valid follow-up pass supersedes any remaining open findings from earlier
passes; shots of the same wave never supersede each other. Failed follow-ups leave them active.

When a run becomes terminal, its finding records move to `history/<run-id>.json`; `_state.json`
keeps one archive reference. Generated Markdown remains beside the run and includes ignored or
superseded resolutions for human audit. Sessions use state schema version 4; older in-progress
sessions are intentionally unsupported.
