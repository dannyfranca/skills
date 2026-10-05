# Decision reports

## Assess final code

The parent inspects final code. Use `assess --run <directory> --execution <assignment-id>
--assessment <assessment.json>` for each completed assignment. The assessment has this shape:

```json
{
  "summary": "Describe the inspected behavior and confidence",
  "evidence": ["final.patch and a relevant check log"],
  "residual_defects": [],
  "regressions": [],
  "disputed_findings": []
}
```

Each defect or regression needs `severity`, `description`, and nonempty `evidence`.
Each dispute needs `id`, `reason`, and nonempty `evidence`. Assessments bind to final code evidence.
Changes to that evidence make an assessment stale. The parent agent owns this assessment.

## Write the decision

Run `measure` again after assessments. It returns the measurement `basis`.
Use the same rates for measurement and reporting. For rate fields and accounting semantics,
read [measurements](measurements.md). Prepare a parent decision:

```json
{
  "basis": "basis returned by measure",
  "recommendation": "Inconclusive; collect a larger varied sample",
  "reasoning": "Explain observed quality and effort tradeoffs with evidence",
  "priorities": "State the project's quality, latency, and cost priorities",
  "uncertainty": "Discuss missing usage, disputed findings, historical gaps, classification, sample size, and shared load",
  "evidence": ["assignment IDs, artifact paths, or finding IDs"],
  "case_notes": {"case-001": "Describe task difficulty, selection, and observed results"}
}
```

Give every case a contextual note. Explain whether additional retained findings led to better
final code. Consider repeated findings, driver fixes, regressions, and unresolved coverage. A
costly arm can be suitable for critical changes and unsuitable for routine work. State the
reason for that choice. Compare rounds and measured tokens separately. Preserve unknown cost as
unknown.

## Save the report

Use `report --run <directory> --decision <decision.json> [--rates <rates.json>]`.
It measures current saved evidence again and rejects a stale decision basis. A final report
requires current assessments for completed executions. An inconclusive report can include failed
or interrupted executions if the parent explains their coverage. Omitting `--decision` saves a draft.

Each report directory contains `report.md` and `report.json`. The JSON stores the complete
measurement snapshot and parent decision. The root `report.json` points to the latest report.
Previous reports remain available. Reports include paired groups, per-case repetitions, first-pass
and full-process findings, severity and disposition, resolution chains, checks, saved errors,
unfinished coverage, final-code assessments, effort, role token/time coverage, cost provenance,
and workflow elapsed time. Evidence paths and hashes support later inspection.

Read classification definitions and reviewer failures in saved sessions when comparing coverage.
Finding counts are observations of driver decisions. They are not ground-truth recall. A
retained finding can still be disputed. Silence does not prove that final code has no defects.
Explain the sample and uncertainty before recommending adoption, mixed use, more data, or no
change.