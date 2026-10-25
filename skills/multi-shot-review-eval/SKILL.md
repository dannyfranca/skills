---
name: multi-shot-review-eval
description: Evaluate multi-shot-review A/B tests from live review sessions, with recommendations from independent analyst models.
disable-model-invocation: true
---

# Multi-Shot Review Eval

You run a deterministic stats pack, send it to two independent analysts, and synthesize **proposals** with a reasoned confidence. The user decides. Expected effects are small but valuable: read effect sizes and uncertainty, not pass/fail.

Set `SKILL_DIR` to the absolute directory that contains this file. Run `python3 "$SKILL_DIR/scripts/eval_stats.py"`; use `--help` for flags. All files go to the eval directory `<review_root>/_evals/<eval-id>/`; [the output contract](references/output-contract.md) defines each file.

Configs and skill files stay as they are. This skill names no models: model choices and prices change faster than the skill. The analysts come from `[eval] analysts` in the user config or from a choice at run time. Model prices are in a machine cache outside the skill, and `prices.py set` is the only writer; `prices.py --help` gives its path.

## 1. Stats

Run `eval_stats.py` with the user's arguments. With no arguments it uses the active tags of the nearest config.

- Exit code 2: give the error to the user and stop.
- Exit code 3: the run wrote no files. Do [the price research](references/price-research.md) for each model in `prices_needed.missing_models` and `prices_needed.stale_models`, then run `eval_stats.py` again with the same arguments. When the research finds no price for a model, add `--allow-unpriced`. Tell the user which missing model has no USD and which stale model uses its old rate, with its `fetched_at`.
- `second_round_offer.<arm>.offer` is true for an arm: tell the user the CI overlap and the kept-share gap, and offer a second sample round of 20 kept findings per arm. On yes, run `eval_stats.py --second-round <eval-id>`.

Continue when `eval_stats.py` exits 0, `sample.json` exists, and the user has answered each second-round offer.

## 2. Analysts

Read [the analyst brief](references/analyst-brief.md). Choose the analysts:

- `configured_analysts` in the `eval_stats.py` output is a list: use those analysts. Each entry gives the harness, the model, and the effort. `stats.json` keeps this list, and the dashboard rejects a synthesis with other analysts.
- `configured_analysts` is `null`: choose two analysts now, from two different providers. For each provider, choose its newest flagship model that this machine can run. Find the models in the `orchestrator_capabilities` catalog, the CLI model list, or the provider release notes. Use current information for each run, because earlier evals can name older models. The dashboard rejects a run-time pick with one harness.
- Run each configured analyst at the effort of its entry. Run each run-time pick at `high`. The dashboard rejects an analyst file at another effort.

To fix the analysts, the user adds this table to a `multi-shot-review.toml` in the config chain. The nearest file with `[eval] analysts` wins. Give two or more entries; `effort` is optional and defaults to `high`.

```toml
[[eval.analysts]]
harness = "claude-code"
model = "<model>"
effort = "medium"

[[eval.analysts]]
harness = "codex"
model = "<model>"
```

The analyst name is `<harness>/<model>`, for example `claude-code/<model>` or `codex/<model>`. Tell the user which analysts you chose and why.

Start all analysts in parallel, each with the same brief. For each analyst, try these routes in order until one gives an analyst file that `agreement.py` accepts:

1. The native subagent tool, when it runs that provider and model.
2. T3 `delegate_task` with that provider instance, model, and effort. Get the IDs from `orchestrator_capabilities`.
3. The headless CLI, from the eval directory, with the brief on stdin:
   - `claude -p --model <model> --effort <effort> --allowedTools "Read Grep Glob Bash(git -C * show *) Bash(git -C * log *)" > <answer-file>`
   - `codex exec -m <model> -c model_reasoning_effort=<effort> -s read-only --skip-git-repo-check -o <answer-file> -`

Save each answer as `analysts/<analyst>.json`. Run `python3 "$SKILL_DIR/scripts/agreement.py" <eval_dir>`. When it reports a contract error, send the error to that analyst and ask for a corrected answer.

When all routes fail for one analyst, continue with the other analysts and record the failure in `synthesis.json` `analysts_missing`. When all analysts fail, give the failures to the user and stop.

Continue when `agreement.py` exits 0 and each chosen analyst has a file or an `analysts_missing` reason.

## 3. Synthesis

Read `stats.json`, every analyst file, and the `agreement.py` result. Write `synthesis.json` with the chosen analysts, one recommendation per non-baseline arm and a `new_variant` recommendation for each variant idea that the data supports.

- Each recommendation is a **proposal**: `promote`, `drop`, `keep_testing`, or `new_variant`. The test as a whole gets no verdict and no label.
- Set `confidence` by reasoning over: the 95% CI against the margin band, the MDE against the margin, `sessions_needed`, the worsening flags, the cost differences, the imbalanced strata, the pooled result, the analyst agreement and kappa, and the sample accuracy per arm.
- The `reasoning` states those numbers. When the analysts disagree, state each position and the data that separates them.

Then run `python3 "$SKILL_DIR/scripts/render_dashboard.py" <eval_dir>`.

Continue when the renderer exits 0 and each non-baseline arm has a recommendation whose reasoning cites the primary-metric CI and its margin.

## 4. Report

Write `report.md` from [the report template](references/report-template.md). When T3 `html_render` is available, check `dashboard.html` with `html_preview`, then publish it with `html_render`. Give the user the chat summary from the template.

Continue when `report.md` exists and the user has the chat summary.

## 5. Decision

The run is complete after step 4 when the user gives no decision. When the user decides about an eval, run `eval_stats.py --record-decision <eval-id> adopt|reject|continue`. `adopt` means the user applies the change; later evals add it to the cumulative adopted gains.

Continue when the command prints `adopted_gains`.
