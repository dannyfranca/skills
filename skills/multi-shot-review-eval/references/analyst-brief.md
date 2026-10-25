# Analyst brief

Send this brief to each analyst. Replace `<eval_dir>`, `<review_root>`, `<skill_dir>`, and `<analyst>` first. Send the same text to all analysts.

---

You are an independent analyst of an A/B test of `multi-shot-review` configurations. Another analyst does the same work in parallel. Work alone: use only the files below, the output contract, and the code. Your judgment must be yours.

Expected effects are small but valuable. Look for the effect size, the uncertainty, and the cost of each arm. Give proposals with a reasoned confidence. Give no final verdict for the test.

## Inputs

- `<eval_dir>/stats.json`: the deterministic stats pack. The first arm is the baseline. Each contrast has the difference, the 95% CI, the minimum detectable effect (`mde`), the equivalence bucket against the margin, and the sessions per arm needed to resolve it. `worsening` lists secondary metrics that are worse with P ≥ 80%. `strata` shows the primary metric by repository, slice harness/model, and diff size; `imbalanced` marks a stratum with uneven arm shares.
- `<eval_dir>/sample.json`, and `<eval_dir>/sample-round2.json` when it exists: the finding sample. `judgeability_notes` tells you which code to judge each entry against.
- `<review_root>`: read access to every review session. Each session directory has `_state.json`; `session.root` is the repository and `session.target` is the reviewed change.

## Work

1. Judge every sample entry against the code. Read the finding, open the code at its `location`, and decide if the defect is real.
   - `exact`: read the files at the reviewed commit with `git -C <root> show <commit>:<path>`.
   - `approximate`: read the current files at `<root>`. Say in the `reason` when the code moved.
   - `unjudgeable`: give the verdict `unjudgeable`.
   - The work is complete when every entry has a verdict and a one-sentence reason that names the code you read.
2. Read the stats pack. For each non-baseline arm, find the primary metric result, the worsening flags, the cost metrics, the imbalanced strata, and the pooled result when `pooled.eval_ids` is not empty. Compare your sample verdicts per arm with the `kept` and `rejected` classes: a kept finding that you judge `invalid` is noise that the primary metric counts as quality.
3. Give one view per non-baseline arm: `promote`, `drop`, or `keep_testing`. Add `new_variant` views when the data points to a change that no arm tests. Set `confidence` from the CI width against the margin, the sample n, the agreement of the sample with the classes, and the strata balance. The `reasoning` states those numbers.

## Output

Keep all files and Git state as they are; your work is read-only. Return one JSON object that follows the **Analyst file** section of `<skill_dir>/references/output-contract.md`, with `analyst` set to `<analyst>`. Return the JSON object only, with no text before or after it.
