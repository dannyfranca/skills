# Multi-Shot Review

Human-facing configuration and review-instruction reference for the
[`multi-shot-review`](SKILL.md) skill.

## Configuration

Configuration files are named:

```text
.agents/multi-shot-review.toml
```

The resolver starts at `$HOME` and loads each configuration down the directory chain to the
repository root. The nearest value wins. Execution profiles are atomic: a nearer `classifier` or
`slice_default` table replaces the whole parent profile.

All settings are optional:

```toml
review_file = "REVIEW"

[classifier]
harness = "codex"
model = "model-name"
reasoning = "high"

[slice_default]
harness = "claude-code"
model = "sonnet"
reasoning = "high"
```

- `review_file`: review-instruction basename. Defaults to `REVIEW`. It must not contain a path or
  the `.md` suffix.
- `classifier`: harness profile used by the slice classifier.
- `slice_default`: harness profile used when a slice does not override it.
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

The resolved content is classifier-only. The classifier receives scoped guidance without source
paths or loader details. Review slices do not receive it automatically. When relevant, the
classifier translates only the concrete requirement into a focused slice prompt.

## Harness audit data

Execution selections are durable:

- Slice definitions store `harness`, `model`, `reasoning`, and their source fields.
- Every run snapshots all six fields, so later configuration or slice-definition changes do not
  alter prior run identity.
- Successful review Markdown artifacts include matching YAML frontmatter.
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
canonical finding must still be open. A valid follow-up supersedes any remaining open findings
from the prior run. Failed follow-ups leave them active.

When a run becomes terminal, its finding records move to `history/<run-id>.json`; `_state.json`
keeps one archive reference. Generated Markdown remains beside the run and includes ignored or
superseded resolutions for human audit. Sessions use state schema version 3; older in-progress
sessions are intentionally unsupported.
