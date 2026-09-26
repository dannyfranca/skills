# Multi-shot review: after the pass cap, judge, and shots change

Date: 2026-09-26. Data: `~/.worktrees/*trading-sandbox*/.review/*/_state.json`.
Scripts: `2026-09-26-trading-sandbox-after-change/{compare,p1p2,byharness}.py`.

## Populations

- **After**: 19 sessions with the new state format (slice `shots`, `judgements`, `pass_window`). Created 2026-09-26 00:32Z to 08:34Z.
- **Before last19**: the 19 old-format sessions created before the first new session.
- **Before all**: 131 old-format sessions with runs.

Two confounders. Read the codex-only rows for a fair comparison.

1. **Slice harness swap.** 11 of 19 new sessions ran slices on claude-code opus. 129 of 131 old sessions ran codex gpt-5.6-terra. The opus slices emit P3 findings and about 2x more findings per run. No REVIEW.md file mentions P3. The P3 surge comes from the model, not from the diff.
2. **Before last19 was a slow night.** 13% failed runs, 42 busy minutes per session versus 16 for the full history. Wall-clock gains against it are flattering.

## Codex-only comparison (same model, same lenses)

| Metric | After codex (n=8) | Before last17 codex | Before all codex (n=129) |
|---|---|---|---|
| Slices that reach pass >= 4 | 0.0% | 16.9% | 15.0% |
| Waves per session | 2.75 | 4.82 | 4.45 |
| Runs per session | 14.2 | 10.2 | 9.9 |
| Failed runs | 0.9% | 12.1% | 2.7% |
| Pass-1 findings per run | 0.90 | 0.79 | 0.69 |
| Kept findings raised at pass 1 | 75.9% | 50.5% | 47.4% |
| Kept per session | 7.25 | 5.59 | 6.41 |
| Kept P1+P2 at pass >= 3 (total) | 1 | 23 | 254 |
| Rejected share | 37.0% | 22.1% | 11.0% |
| Run duration, mean min | 2.5 | 2.7 | 2.7 |
| Runs busy min per session | 16.1 | 45.2 | 16.3 |
| Idle min per session | 16.7 | 14.8 | 16.8 |
| Idle share of wall | 47% | 23% | 44% |
| Wall median, min | 28.4 | 29.4 | 20.6 |

## All 19 new sessions versus all 19 old (mixed harness)

| Metric | After | Before last19 | Before all |
|---|---|---|---|
| Slices that reach pass >= 4 | 1.2% | 21.8% | 15.8% |
| Max pass | 5 | 13 | 13 |
| Waves per session | 3.00 | 5.42 | 4.54 |
| Kept per session | 14.95 | 8.84 | 6.87 |
| Kept P1+P2 per session | 7.84 | 5.63 | 6.33 |
| Kept P1+P2 raised at pass 1 | 79.2% | 46.7% | 46.8% |
| Idle share of wall | 64.4% | 32.0% | 45.7% |
| Wall median, min | 33.2 | 82.0 | 21.7 |

## Mechanism checks

- **Judge**: 16 verdicts, 15 stop, 1 continue. The continue verdict (pboc-m2-refresh, shared-refresh-extraction) followed a kept P1 at pass 3. Pass 4 found one P3, pass 5 was clean. All reasons match the rule. No judge failure events.
- **Shots phase-down**: pass 1 all slices 2 to 3 runs. Pass 2: 23 single, 35 double, 5 triple. Pass 3: 20 single, 17 double, 0 triple.
- **Auto-duplicate**: 19 marks, all genuine on inspection. 18 manual duplicate marks remain, mostly same-slice same-pass siblings the auto rule missed. Recall gap, no precision problem.
- **Re-raise rate** (same path, title overlap >= 0.6, consecutive passes): 6.4% (8/125) versus 0% and 1.6%. Several are re-raises of superseded but unfixed items.
- **Post-stop last wave**: 17 P3 and 3 P2 findings left open in state. Final findings are mostly not triaged.

## Defects found in the run data

- **Classifier failures**: claude-code classifier exits with code 2 on the first attempt in 11 of 19 sessions (up to 5 retries in pboc-rate-recover). No classifier stderr log exists under `_logs`, so the cause is not recorded. Only after-retry sessions succeed. Started 2026-09-25 22:44Z, before the diff, when the classifier moved to claude-code.
- **`--json-schema` rejected**: claude-code slice runs failed with `no schema with key or ref "https://json-schema.org/draft/2020-12/schema"` until 2026-09-26 01:04Z. The diff strips `$schema` in `harnesses/claude_code.py`. Fixed.

## Verdict

Better on the goal it targeted. Same model, same lenses:

- Long tails are gone: pass >= 4 slices 17% to 0%, waves per session 4.8 to 2.75.
- Findings arrive earlier: kept findings at pass 1 50% to 76%. Late P1+P2 (pass >= 3) 23 to 1.
- Kept P1+P2 per session up 21% (5.6 to 6.75) with 39% more runs.

Costs:

- Rejected share up: 22% to 37% (P1 19, P2 15). Two shots at pass 1 add noise the parent must triage.
- Idle share up: 23% to 47%. The parent now triages a larger pass-1 wave. Wall median is flat versus the slow night and 38% worse than the full history (28 vs 21 min).
- Re-raise rate 6.4%. History in prompt does not stop re-raises of superseded items.

Opus slice sessions show the same shape (pass >= 4 60% to 2%, kept at pass 1 21% to 66%), but n=2 before and P3 inflation make that comparison weak.
