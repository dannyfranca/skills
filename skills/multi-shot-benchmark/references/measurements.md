# Saved measurements

Run `python3 "$SKILL_DIR/scripts/benchmark.py" measure --run <run-directory> [--rates <rates.json>]` after drivers stop.
This command makes no model calls. It takes the run and execution locks, verifies the frozen tool and validates saved
review state with the trusted installed validator, reads finding archives, and writes `measurements.json`.
Active driver PIDs, classifiers, review reservations, and judges prevent measurement. Invalid or missing required evidence stops the command.

First pass means pass 1 of every initial slice definition, including retries. Full workflow
includes all saved definitions and attempts. Each finding keeps its source, severity, disposition,
driver resolution, repeated link, and canonical chain. A retained finding has a fix claim in
validated completion evidence. Failed-attempt claims retain attempt provenance and links, but
remain unconfirmed. An unresolved finding has no terminal rejection, duplicate, or completed fix claim.
Repeated findings retain separate observations but share a canonical chain. Invalid chain links
are reported as errors. Interpret these values as observed workflow decisions; recall requires independent ground truth.

Review waves are contiguous reservation groups in review history. Retry waves count too.
Slice passes, logical reviewer shots, actual review attempts, and driver attempts are separate.
Completion reasons, unsuccessful runs, unfinished slices, check hashes, and scheduler records
remain inspectable. All attempt results retain failed checks. Final validated checks stay separate.
Saved error blocks and judge stderr retain failures after a successful retry. Abandoned
classifiers and judges remain unfinished evidence with unknown accounting when logs are absent. Check evidence can
be verified, changed, missing, or a driver claim.

Usage records stay separate for driver, classifier, reviewer, and judge roles. Structured Codex
events separate cached input from total input. Claude cache reads and writes remain separate.
Claude modelUsage retains every model, including auxiliary models. It takes precedence over
top-level usage; the two are never added. Missing fields stay unknown. A plain `tokens used` summary has no billing fields. Judge logs lack
reliable per-attempt command profiles and lifecycle timestamps. These values stay unknown.
Harness duration_ms and duration_api_ms remain available when present. Saved verdicts retain
their known profiles. Role times overlap. Use workflow elapsed time for the total wall span.

Optional rates use this JSON shape:

```json
{
  "currency": "USD",
  "units": "per_million_tokens",
  "source": "URL or dated rate document",
  "date": "2026-10-04",
  "assumptions": "State discounts, cache rules, and excluded charges",
  "models": {
    "codex/example-model": {
      "input": 1,
      "cached_input": 0.5,
      "cache_write": 1,
      "output": 2
    }
  }
}
```

These numbers are examples. Use supplied, dated rates. The estimate is
unknown when any observed role record lacks usable tokens or a matching rate. A known subtotal
and its coverage remain available. Even a complete estimate of recorded events is not an invoice
or proof that all provider charges were captured.

For assessment fields, evidence requirements, and current/stale status, follow
[Assess final code](reporting.md#assess-final-code). Missing quality evidence remains unknown.

Case and arm aggregates retain repetitions, repository identity, frozen task context, and
coverage denominators. Paired deltas use only groups completed by every arm. Unfinished pairs
remain listed. Compare the same cases and repetitions; discuss task difficulty and shared load.
Each role has token and time coverage. Aggregate effort values have known subtotals and
complete totals; an unknown value is not zero. Paired usage and time deltas require complete
values on both sides. Workflow elapsed time is the wall span across driver attempts. It includes
waits between retries and does not sum overlapping role times. Missing intermediate attempt
end times leave attempt duration unknown but do not erase a known workflow wall span.
