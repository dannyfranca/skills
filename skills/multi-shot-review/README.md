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
slice_default_model = "model-name"
```

- `review_file`: review-instruction basename. Defaults to `REVIEW`. It must not contain a path or
  the `.md` suffix.
- `classifier_model`: model used by the slice classifier.
- `slice_default_model`: model used when a slice does not choose one explicitly.

Unknown settings and invalid values are rejected.

When no classifier or slice model is configured, the corresponding command omits `-m` and lets the
Codex harness select its default. `classify_slices.py --model` overrides the configured classifier
model. `add_slice.py --model` overrides the configured slice default for that slice.

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

## Model audit and resume data

Model selection is durable:

- Slice definitions store `model` and `model_source`.
- Every run snapshots both fields, so later configuration or slice-definition changes do not alter
  prior run identity.
- Successful review Markdown artifacts include matching YAML frontmatter.

`model_source` is one of:

- `slice-override`
- `configured-default`
- `harness-default`
- `legacy-definition` for migrated state created before run-level model snapshots

Harness-default runs store `model: null`.
