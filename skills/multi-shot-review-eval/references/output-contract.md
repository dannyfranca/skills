# Output contract

All files are in the eval directory `<review_root>/_evals/<eval-id>/`. `eval_stats.py` writes `stats.json`, `sample.json`, and `sample-round2.json` (second round only). The analysts write `analysts/<analyst>.json`. The main session writes `synthesis.json`. `render_dashboard.py` writes `dashboard.html`. The main session writes `report.md` last.

Use JSON with UTF-8 text. Use the lowercase values exactly as this file shows them.

## Lane metrics

`stats.json` gives the reviewer-lane metrics of each arm in `arms.<arm>.lanes`. The key is the lane label from the review session. Each lane that ran has an entry, also with no findings. The metrics count only completed sessions that have all their findings.

```json
{
  "lanes": {
    "codex:model-x": {
      "kept": 12, "unique": 9, "shared": 3, "rejected": 8, "rejection_rate": 0.4,
      "tokens": 120000, "usd": 0.6, "kept_per_usd": 20.0, "unique_per_usd": 15.0
    }
  }
}
```

- A duplicate group is one finding and the same-wave reports that the reviewer runner grouped into it. A group counts one time for each lane that reported it. The kept copy sets the outcome of the group: when the author rejects it, the group is rejected for each lane.
- `kept`: the kept groups that the lane reported.
- `unique`: the kept groups that only this lane reported.
- `shared`: the kept groups that this lane and one or more other lanes reported. `kept` is `unique` plus `shared`.
- `rejected`: the rejected groups that the lane reported.
- `rejection_rate`: `rejected / (kept + rejected)`. It is `null` when the lane has no kept or rejected group.
- `tokens` and `usd`: the sum from the logs of the review runs of the lane. Classifier, judge, and dedupe logs are not in this sum. The value is `null` when one or more runs of the lane have no log, or a log without this value.
- `kept_per_usd` and `unique_per_usd`: `kept / usd` and `unique / usd`. The value is `null` when `usd` is `null` or 0.
- Open findings, removed findings, and duplicates that the author marked are not in these metrics.
- A session from before reviewer lanes has no lane label. Its runs count as one lane, with the label `<harness>:<model>[:<reasoning>]` from the review profile.

## Setting metrics

`stats.json` gives four more tallies for each arm. They count only completed sessions that have all their findings. An old `stats.json` does not have them. The dashboard and the report must accept a missing tally.

```json
{
  "shots": {"by_shot": {"1": {"runs": 10, "kept": 14, "rejected": 8}, "2": {"runs": 6, "kept": 7, "rejected": 2}}, "extra_only_kept": 4},
  "dedupe": {"runs": 3, "failed": 1, "merged": 5, "tokens": 9000, "usd": 0.05},
  "judge": {"continue": 2, "stop": 3, "continue_with_kept": 1, "kept_after_continue": 2},
  "severity": {"P0": {"kept": 1, "rejected": 0}, "P1": {"kept": 7, "rejected": 3}, "P2": {"kept": 10, "rejected": 6}, "P3": {"kept": 0, "rejected": 1}}
}
```

- `shots.by_shot`: the key is the shot number. `runs` is the review runs of that shot. `kept` and `rejected` count duplicate groups as the lane metrics do: a group counts one time for each shot that reported it.
- `shots.extra_only_kept`: the kept groups that no shot 1 reported. This is the yield of the extra shots.
- `dedupe.runs`: the dedupe runs. This is the larger of the dedupe events and the dedupe logs, because a dedupe run that ends after its slice changes writes a log but no event. `failed` is the runs that failed. `merged` is the findings that a dedupe run marked as duplicates.
- `dedupe.tokens` and `dedupe.usd`: the sum from the dedupe logs. The value is `null` when there are fewer dedupe logs than dedupe runs, or a log does not have this value.
- `judge.continue` and `judge.stop`: the judge verdicts.
- `judge.continue_with_kept`: the `continue` verdicts after which the same slice definition got one or more kept findings in a later pass.
- `judge.kept_after_continue`: the kept findings from passes after the first `continue` verdict of their slice definition.
- `severity`: the kept and the rejected findings for each severity from P0 to P3. Each report counts one time.

## Cost metric

`kept_p0_p2_per_usd` is the kept P0–P2 findings divided by the session USD. A higher value is better. The arm value is the sum of the kept findings divided by the sum of the USD. Only sessions that have all their findings and a USD value for each log are in this metric.

## Analyst file

Path: `analysts/<analyst>.json`. `<analyst>` is the harness and model with `/` changed to `-`, for example `codex-model-x` for the analyst `codex/model-x`. Model names in this file are placeholders.

```json
{
  "analyst": "codex/model-x",
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
  "analysts": ["claude-code/model-y", "codex/model-x"],
  "recommendations": [
    {
      "arm": "b",
      "proposal": "keep_testing",
      "confidence": "medium",
      "reasoning": "Both analysts see a quality gain; the CI crosses the margin. 14 more sessions per arm resolve it."
    }
  ],
  "analysts_missing": [
    {"analyst": "codex/model-x", "reason": "delegate_task and codex exec both failed: quota exceeded."}
  ]
}
```

- `analysts`: the `<harness>/<model>` name of each analyst that the eval chose (SKILL.md, Analysts). Give two or more names, each one time.
- `recommendations`: the same rule as analyst `views`: one entry that is not `new_variant` for each arm that is not the baseline, plus optional `new_variant` entries. Each entry uses the analyst view fields.
- `analysts_missing`: one entry for each chosen analyst that did not give an analyst file. An analyst with a file in `analysts/` is not in this list. Use an empty list when all ran. Each name in `analysts` has exactly one file or one entry here, and only those names have files.

## Renderer result

`render_dashboard.py <eval_dir>` writes `dashboard.html` and prints JSON to stdout:

- `dashboard`: the path of `dashboard.html`.
- `agreement`: one entry for each pair of analysts: `{analysts: [a, b], n, agreement, kappa}`. `n` counts the findings that both judged `valid` or `invalid`. `agreement` is the share of the same verdict. `kappa` is Cohen's kappa. Both are `null` when they are not defined.
- `sample_accuracy`: `{arm: {analyst: {kept_valid, kept_n, rejected_invalid, rejected_n}}}`. `kept_valid` is the share of sampled kept findings judged `valid`; `rejected_invalid` is the share of sampled rejected findings judged `invalid`. `unjudgeable` verdicts are not in the shares or in n. A share is `null` when its n is 0.

`agreement.py <eval_dir>` prints the same `agreement` and `sample_accuracy` without `synthesis.json`.

The renderer stops with exit code 2 and an error message when an analyst file or `synthesis.json` breaks this contract.
