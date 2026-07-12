# Slice Selection

Register slices until every meaningful risk in the change belongs to at least one slice. A runtime code change is incomplete without a dedicated `test-coverage` slice.

## Related work

Record intentionally deferred work so reviewers can distinguish it from missing scope:

```bash
python3 "$SKILL_DIR/scripts/add_related_task.py" \
  --review-dir "$REVIEW_DIR" \
  --name <task-name> \
  --text "<what will be addressed later>"
```

Use `--file <path>` or `--dir <path>` for larger related tasks.

## Required test coverage slice

Every change to runtime code requires a dedicated prompted `test-coverage` slice. Runtime code is shipped application, library, service, CLI, or automation code. This slice is optional for changes limited to tests, documentation, or metadata.

```bash
python3 "$SKILL_DIR/scripts/add_slice.py" \
  --review-dir "$REVIEW_DIR" \
  --name test-coverage \
  --prompt-file - <<'EOF'
Review the current uncommitted changes.
Slice: test coverage for changed runtime behavior.
Scope: every added, removed, or changed runtime behavior in this review.
Verify tests prove the behavior and catch regressions across success, failure, boundary, and integration paths. Report each meaningful behavior without adequate test evidence.
EOF
```

## Broad slices

Use native slices for small changes:

- Up to 5 meaningful files: 2 broad slices.
- 6–10 meaningful files: 3 broad slices.
- 11–20 meaningful files: 4 broad slices.

Mechanical churn does not increase the count.

```bash
python3 "$SKILL_DIR/scripts/add_slice.py" \
  --review-dir "$REVIEW_DIR" \
  --name broad-1 \
  --uncommitted
```

Use `--base <branch>` or `--commit <sha>` when either target describes the review boundary more accurately. Native slices use Codex's built-in review instructions; task context and slice-specific instructions belong in prompted slices.

## Focused slices

Use prompted slices for larger or riskier changes. Divide them by feature, contract, subsystem, or risk; add cross-cutting slices for structure, migrations, tests, performance, security, or UI flows when relevant.

Put the review target and slice-specific scope in the prompt. The runner prepends the session task context automatically.

```bash
python3 "$SKILL_DIR/scripts/add_slice.py" \
  --review-dir "$REVIEW_DIR" \
  --name api-contracts \
  --prompt-file - <<'EOF'
Review the current uncommitted changes.
Slice: API and data contracts.
Scope: <features, directories, files, or contracts>.
Focus on compatibility, validation, migrations, and call sites.
EOF
```

Use the structure reference for a maintainability slice:

```bash
python3 "$SKILL_DIR/scripts/add_slice.py" \
  --review-dir "$REVIEW_DIR" \
  --name structure \
  --prompt-file - <<'EOF'
Review the current uncommitted changes.
Slice: project structure and maintainability.
Read /path/to/this-skill/references/software-structure.md and apply it.
EOF
```

Review slices default to `gpt-5.6-sol` with high reasoning. Override the model only for a slice with a specific need.
