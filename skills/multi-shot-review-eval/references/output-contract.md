# Output contract

All files are in the eval directory `<review_root>/_evals/<eval-id>/`. `eval_stats.py` writes `stats.json`, `sample.json`, and `sample-round2.json` (second round only). The analysts write `analysts/<analyst>.json`. The main session writes `synthesis.json`. `render_dashboard.py` writes `dashboard.html`. The main session writes `report.md` last.

Use JSON with UTF-8 text. Use the lowercase values exactly as this file shows them.

## Analyst file

Path: `analysts/<analyst>.json`. `<analyst>` is the harness and model with `/` changed to `-`, for example `claude-code-claude-opus-5-5`.

```json
{
  "analyst": "claude-code/claude-opus-5-5",
  "effort": "high",
  "judgments": [
    {
      "session_id": "owner/repo/20261001-0002-00000002",
      "finding_id": "k2-1",
      "verdict": "valid",
      "reason": "The loop reads past the end of the buffer at src/lib.rs:41."
    }
  ],
  "views": [
    {
      "arm": "b",
      "proposal": "keep_testing",
      "confidence": "medium",
      "reasoning": "The kept P1/P2 gain is +0.4 per session, but the CI crosses the margin band."
    }
  ],
  "notes": "Two sessions in arm b have no diff size."
}
```

- `judgments`: one judgment for each entry in `sample.json` and `sample-round2.json`. The key is `session_id` plus `finding_id`.
- `verdict`: `valid` (the finding is a real defect at its stated severity or near it), `invalid` (no defect, or the claim is wrong), or `unjudgeable` (the code to judge is not available). Use `unjudgeable` for every entry that `eval_stats.py` marks `unjudgeable`.
- `views`: one view for each arm that is not the baseline. Add more views with `proposal: "new_variant"` and `arm` set to the arm the variant starts from.
- `proposal`: `promote`, `drop`, `keep_testing`, or `new_variant`.
- `confidence`: `low`, `medium`, or `high`. The `reasoning` states the numbers that set the confidence.
- `notes`: data problems and doubts. Use an empty string when there are none.

## Synthesis file

Path: `synthesis.json`.

```json
{
  "recommendations": [
    {
      "arm": "b",
      "proposal": "keep_testing",
      "confidence": "medium",
      "reasoning": "Both analysts see a quality gain; the CI crosses the margin. 14 more sessions per arm resolve it."
    }
  ],
  "analysts_missing": [
    {"analyst": "codex/gpt-6.1-sol", "reason": "delegate_task and codex exec both failed: quota exceeded."}
  ]
}
```

- `recommendations`: the same rule as analyst `views`: one entry that is not `new_variant` for each arm that is not the baseline, plus optional `new_variant` entries. Each entry uses the analyst view fields.
- `analysts_missing`: one entry for each default analyst that did not give an analyst file. An analyst with a file in `analysts/` is not in this list. Use an empty list when both ran. Each default analyst (SKILL.md step 3) has exactly one file or one entry here, and only default analysts have files.

## Renderer result

`render_dashboard.py <eval_dir>` writes `dashboard.html` and prints JSON to stdout:

- `dashboard`: the path of `dashboard.html`.
- `agreement`: one entry for each pair of analysts: `{analysts: [a, b], n, agreement, kappa}`. `n` counts the findings that both judged `valid` or `invalid`. `agreement` is the share of the same verdict. `kappa` is Cohen's kappa. Both are `null` when they are not defined.
- `sample_accuracy`: `{arm: {analyst: {kept_valid, kept_n, rejected_invalid, rejected_n}}}`. `kept_valid` is the share of sampled kept findings judged `valid`; `rejected_invalid` is the share of sampled rejected findings judged `invalid`. `unjudgeable` verdicts are not in the shares or in n. A share is `null` when its n is 0.

`agreement.py <eval_dir>` prints the same `agreement` and `sample_accuracy` without `synthesis.json`.

The renderer stops with exit code 2 and an error message when an analyst file or `synthesis.json` breaks this contract.
