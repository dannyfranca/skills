# Report template

Write `report.md` in the eval directory with this structure. Use short lines. Use numbers from `stats.json` and the renderer result; round to 2 significant digits.

```markdown
# <eval-id>

<mode> · baseline <arm> · <arm>: n=<n> · window <start> → <end>

## Recommendations (reasoning, not a verdict)

- <arm>: <proposal>, <confidence> confidence. <one-line reasoning with the numbers>

## Primary metric: <metric>

| arm | diff | rel | 95% CI | margin | bucket | MDE | sessions needed |
| --- | --- | --- | --- | --- | --- | --- | --- |

Pooled (<eval ids>): <diff, CI, bucket> — or "no earlier evals of this contrast".

## Flags

- Worsening (P ≥ 80%): <arm metric p_worse>, or none.
- Imbalanced strata: <dimension value>, or none.
- Prices: <stale / missing models / assumptions>.
- Incomplete sessions: <arm n_incomplete>.

## Analysts

- <analyst>: <view per arm, one line>.
- Missing: <analyst and reason>, or none.
- Sample agreement: <agreement> (κ=<kappa>, n=<n>).
- Sample accuracy: <arm>: kept valid <share>, rejected invalid <share>.

## Next

- Second sample round: <offered for arm X / not offered>.
- Record the decision: `eval_stats.py --record-decision <eval-id> adopt|reject|continue`.

Files: stats.json · sample.json · analysts/ · synthesis.json · dashboard.html
```

## Chat summary

Give the user this summary after the report, at most 8 lines:

```text
<eval-id>: <arm> vs <baseline>, n=<n>/<n>
Primary <metric>: <diff> (<rel>), CI <lo>..<hi>, <bucket>
Flags: <worsening, imbalance, prices>, or none
Analysts: <agree/disagree>, κ=<kappa>
Proposal: <arm> <proposal> (<confidence>): <one-line reason>
Report: <path to report.md> · Dashboard: <path to dashboard.html>
```
