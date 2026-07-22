# Multi-Shot Review

Human-facing configuration and review-instruction reference for the
[`multi-shot-review`](SKILL.md) skill.

## Configuration

Configuration files are named:

```text
.agents/multi-shot-review.toml
```

The resolver starts at `$HOME` and loads each configuration down the directory chain to the
repository root. Settings merge independently; the nearest configured value wins.

All settings are optional:

```toml
review_file = "REVIEW"
classifier_model = "model-name"
classifier_reasoning = "high"
slice_default_model = "model-name"
slice_default_reasoning = "high"
```

- `review_file`: review-instruction basename. Defaults to `REVIEW`. It must not contain a path or
  the `.md` suffix.
- `classifier_model`: model used by the slice classifier.
- `classifier_reasoning`: reasoning effort used by the slice classifier.
- `slice_default_model`: model used when a slice does not choose one explicitly.
- `slice_default_reasoning`: reasoning effort used when a slice does not choose one explicitly.

Unknown settings and invalid values are rejected.

When no model or reasoning is configured, the corresponding command omits `-m` or
`model_reasoning_effort` and lets the Codex harness select its default.
`classify_slices.py --model/--reasoning` override the configured classifier defaults.
`add_slice.py --model/--reasoning` override the configured slice defaults for that slice.

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

## Model and reasoning audit data

Model and reasoning selections are durable:

- Slice definitions store `model`, `model_source`, `reasoning`, and `reasoning_source`.
- Every run snapshots all four fields, so later configuration or slice-definition changes do not
  alter prior run identity.
- Successful review Markdown artifacts include matching YAML frontmatter.

Each source field is one of:

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
superseded resolutions for human audit. Sessions use state schema version 2; older in-progress
sessions are intentionally unsupported.
