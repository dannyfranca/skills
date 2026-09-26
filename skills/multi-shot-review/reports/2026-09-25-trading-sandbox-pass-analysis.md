# Pass-count analysis of multi-shot review sessions on trading-sandbox

Date: 2026-09-25. Author: Danny Franca with Claude. Status: analysis complete; changes applied in
the same pull request. Supporting scripts: [`2026-09-25-trading-sandbox-pass-analysis/`](2026-09-25-trading-sandbox-pass-analysis/).

## 1. Purpose

Barrier-mode reviews on trading-sandbox took too long. This report records the data, the analysis,
the decisions, and the changes made to the skill. Future analyses can compare against these
figures.

## 2. Scope and data

Source: every `_state.json` under `/home/danny/.worktrees/*trading-sandbox*/.review/*/`, read on
2026-09-25. The source files are private and stay out of this repository. They are also live: new
sessions add files, and open sessions continue to change. Every script reads only sessions created
before `2026-09-25T22:40:00Z` (override with `MSR_CREATED_BEFORE`). A later rerun can still drift
by a small amount because older sessions can change after the report date. Repository: trading-sandbox (Rust). Review policy: the repository `REVIEW.md` with
required lenses (Idiomatic Rust, Runtime correctness, Specs fidelity, Test Coverage, and others).

| Item | Value |
|---|---|
| Sessions | 130 (128 with at least one finished run) |
| Slices (including 3 removed) | 569 |
| Finished runs | 1260 |
| Valid runs (`findings`, `no_findings`, `ignored`) | 1237 |
| Failed or timed-out runs | 23 (16 failed, 7 timeout) |
| Findings | 934 (824 kept, 100 rejected, 10 duplicate) |
| Session wall-clock, total | 4792.6 min |
| Session wall-clock, median / p90 / max | 21.1 / 83.8 / 327.3 min |
| Mean valid run duration | 2.7 min |

Definitions used in every table:

- Kept finding: not rejected and not a duplicate.
- Rejected finding: status `ignored` with resolution kind `rejected`.
- Lens groups, checked in this order on the lowercase slice name: `rust` (contains "idiomatic",
  "rust", or "design"), `tests` ("test" or "coverage"), `specs` ("spec", "fidelity", or "prd"),
  `other` (all remaining names).
- Wave: runs of the same session and pass whose start times chain within 60 s.
- Parent gap: time between the end of one wave and the start of the next; the parent fixes and
  triages during this time.

Caveats:

- `superseded` means a later pass ran. It does not prove that the finding was fixed.
- Fixes between passes are not committed. Per-pass diffs do not exist. "Fix-induced" is inferred
  from finding lineage and finding text, not from diffs.
- The qualitative tables were classified by one reader. Borderline cases are marked.

## 3. Quantitative data

### 3.1 Highest pass reached per slice

| Group | Slices | p1 | p2 | p3 | p4 | p5 | p6 | p7+ | Mean |
|---|---|---|---|---|---|---|---|---|---|
| all | 569 | 305 (53.6%) | 134 (23.6%) | 46 (8.1%) | 34 (6.0%) | 24 (4.2%) | 9 (1.6%) | 17 (3.0%) | 2.04 |
| rust | 129 | 84 (65.1%) | 29 (22.5%) | 3 (2.3%) | 5 (3.9%) | 4 (3.1%) | 1 (0.8%) | 3 (2.3%) | 1.70 |
| tests | 128 | 27 (21.1%) | 32 (25.0%) | 23 (18.0%) | 21 (16.4%) | 9 (7.0%) | 6 (4.7%) | 10 (7.8%) | 3.21 |
| specs | 128 | 83 (64.8%) | 34 (26.6%) | 3 (2.3%) | 1 (0.8%) | 6 (4.7%) | 1 (0.8%) | 0 | 1.56 |
| other | 184 | 111 (60.3%) | 39 (21.2%) | 17 (9.2%) | 7 (3.8%) | 5 (2.7%) | 1 (0.5%) | 4 (2.2%) | 1.78 |

Top `other` names: code-quality (27), runtime-correctness (22), correctness (9), standards (6),
readability-simplicity (6), standards-quality (5), capture-correctness (4).

### 3.2 Runs by pass number (valid runs only)

| Pass | Runs | Failed | Mean findings | % runs with ≥1 | Findings | P0/P1/P2/P3 | % rejected | % dup | Mean min | Median min |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 623 | 13 | 0.69 | 46.1% | 431 | 0/136/289/6 | 7.2% | 1.9% | 2.7 | 2.5 |
| 2 | 274 | 0 | 0.78 | 54.0% | 213 | 0/42/167/4 | 14.1% | 0.9% | 2.7 | 2.6 |
| 3 | 136 | 4 | 0.87 | 67.6% | 118 | 0/23/95/0 | 9.3% | 0.0% | 2.8 | 2.7 |
| 4 | 91 | 3 | 0.80 | 59.3% | 73 | 0/18/55/0 | 9.6% | 0.0% | 2.7 | 2.6 |
| 5 | 52 | 1 | 0.85 | 57.7% | 44 | 0/13/31/0 | 22.7% | 0.0% | 3.0 | 2.6 |
| 6 | 26 | 1 | 1.04 | 76.9% | 27 | 0/6/21/0 | 18.5% | 0.0% | 3.1 | 2.8 |
| 7 | 17 | 0 | 0.88 | 70.6% | 15 | 0/5/10/0 | 20.0% | 0.0% | 3.1 | 3.0 |
| 8+ | 18 | 1 | 0.72 | 50.0% | 13 | 0/0/13/0 | 23.1% | 0.0% | 2.8 | 2.7 |

### 3.3 Cap simulation: kept finding records at pass > N

| Set | Cap N | Lost | P1 | P2 | P3 | Sessions with lost P0/P1 | Sessions with any loss |
|---|---|---|---|---|---|---|---|
| all | 1 | 432 | 73 | 355 | 4 | 25 (19.5%) | 81 (63.3%) |
| all | 2 | 251 | 46 | 205 | 0 | 16 (12.5%) | 58 (45.3%) |
| all | 3 | 144 | 28 | 116 | 0 | 12 (9.4%) | 36 (28.1%) |
| all | 4 | 78 | 14 | 64 | 0 | 8 (6.2%) | 19 (14.8%) |
| all | 5 | 44 | 8 | 36 | 0 | 6 (4.7%) | 13 (10.2%) |
| all | 6 | 22 | 5 | 17 | 0 | 4 (3.1%) | 9 (7.0%) |
| all | 7 | 10 | 0 | 10 | 0 | 0 | 4 (3.1%) |
| non-test | 1 | 217 | 64 | 149 | 4 | 20 (15.6%) | 44 (34.4%) |
| non-test | 2 | 132 | 40 | 92 | 0 | 13 (10.2%) | 27 (21.1%) |
| non-test | 3 | 82 | 24 | 58 | 0 | 11 (8.6%) | 19 (14.8%) |
| non-test | 4 | 47 | 11 | 36 | 0 | 7 (5.5%) | 10 (7.8%) |
| non-test | 5 | 28 | 5 | 23 | 0 | 5 (3.9%) | 9 (7.0%) |
| non-test | 6 | 13 | 3 | 10 | 0 | 3 (2.3%) | 5 (3.9%) |

Kept findings: 824 total, 423 on non-test paths, 401 on test paths. Session denominator: 128.
Each row counts finding records. A finding that a later pass raises again counts again. Section
4.5 classifies the late P1 findings one at a time.

### 3.4 Pass of the last kept finding per slice

| Last kept pass | none | 1 | 2 | 3 | 4 | 5 | 6 | 7+ |
|---|---|---|---|---|---|---|---|---|
| Slices | 307 (54.0%) | 130 (22.8%) | 47 (8.3%) | 37 (6.5%) | 24 (4.2%) | 7 (1.2%) | 7 (1.2%) | 10 (1.8%) |

### 3.5 Wave structure

| Metric | Value |
|---|---|
| Waves | 564 (291 with more than one run) |
| Wave size distribution | 1: 273, 2: 105, 3: 34, 4: 90, 5: 57, 6: 5 |
| Mean wave wall (max run) | 4.0 min |
| Slowest-slice penalty, mean over multi-run waves | 0.8 min (median 0.6) |
| Slowest-slice penalty, total | 225.0 min (10.0% of wave wall) |
| Inter-wave gaps | 436 (median 1.7, mean 4.9, p90 8.9, total 2154.0 min) |

| Wave pass | Waves | Mean size | Mean wall min | Mean penalty min |
|---|---|---|---|---|
| 1 | 161 | 3.95 | 3.6 | 0.8 |
| 2 | 130 | 2.11 | 3.1 | 0.4 |
| 3 | 98 | 1.43 | 4.7 | 0.2 |
| 4 | 75 | 1.25 | 4.8 | 0.1 |
| 5 | 45 | 1.18 | 4.3 | 0.1 |
| 6+ | 55 | 1.15 | 4.8 | 0.1 |

### 3.6 Time decomposition per session (interval union, no double counting)

| Component | Total min | % of wall | Median per session | Mean per session |
|---|---|---|---|---|
| Classifier (not overlapping runs) | 632.3 | 13.2% | 3.6 | 4.9 |
| Runs busy | 2027.9 | 42.3% | 9.5 | 15.8 |
| Idle (parent fix and triage) | 2132.4 | 44.5% | 6.4 | 16.7 |
| Session wall-clock | 4792.6 | | 21.1 | 37.4 |

Idle share per session: median 29%, p90 54%. Sessions more than 50% idle: 17 of 128.

### 3.7 Classifier

| Metric | Value |
|---|---|
| Classifications per session | 0: 1, 1: 89, 2: 23, 3: 12, 5: 4, 6: 1 |
| Classification status | succeeded 193, failed 3, running 1 |
| Duration median / mean / p90 / max | 3.1 / 3.2 / 4.5 / 65.1 min |
| Sessions with 2 or more classifications | 40 (30.8%) |
| Slice events after the first classification (those sessions) | removed 208, reactivated 205, added 9 |
| Reclassifications that changed the slice set | 3 |
| Net slices removed at session end | 3 (in 1 session) |
| Net slices added at session end | 9 (in 2 sessions) |

### 3.8 Late-pass rejection rate

| Set | Findings | Rejected | % rejected | Duplicate |
|---|---|---|---|---|
| pass ≥ 3 | 290 | 39 | 13.4% | 0 |
| pass < 3 | 644 | 61 | 9.5% | 10 |

Most frequent rejection bigrams: "old pins" (9), "parser contract" (7), "low value" (7), "data
lifecycle" (6), "managed immutable" (6), "explicit mutable" (6).

### 3.9 Re-raise detection (title token overlap)

| Metric | Threshold | Pass ≥ 2 findings | Re-raised | % | Earlier rejected | Earlier kept |
|---|---|---|---|---|---|---|
| overlap, any earlier pass | 0.6 | 503 | 31 | 6.2% | 2 | 29 |
| overlap, consecutive pass | 0.6 | 503 | 20 | 4.0% | 0 | 20 |
| jaccard, any earlier pass | 0.6 | 503 | 8 | 1.6% | 1 | 7 |
| jaccard, any earlier pass | 0.4 | 503 | 24 | 4.8% | 2 | 22 |

Re-raised findings were rejected 22.6% of the time; novel findings 13.1%.

### 3.10 Cross-lens duplication (same session and pass, other slice, same path, start line ±5)

| Metric | Value |
|---|---|
| Findings in a session-pass with 2 or more reporting slices | 654 of 934 |
| Overlapping with another slice | 86 (9.2% of all; 13.1% of multi-slice groups) |
| By severity (overlap / total) | P1 49/243, P2 37/681, P3 0/10 |
| By lens (overlap / total) | rust 23/124, tests 12/511, specs 19/97, other 32/202 |
| Overlapping findings later rejected | 7 |

## 4. Qualitative data

### 4.1 Sample design

Sample of 50 non-rejected findings from 50 distinct sessions: 30 at pass ≥ 3, 20 at pass 2. Plus
25 rejected findings at pass ≥ 2. For each, the reader examined the finding text, earlier titles on
the same slice, other-slice titles in the same wave, and git commits between runs.

Classes: A = pre-existing defect missed earlier. B = introduced or exposed by a fix. C = re-raise
of an earlier same-slice finding. D = duplicate of another lens. E = nitpick, style, or
speculative edge case. F = design or architecture concern.

### 4.2 Non-rejected findings at pass ≥ 3 (n = 30)

| # | Session | Slice | Pass | Sev | Class | Sev ok | Test file | Gist |
|---|---|---|---|---|---|---|---|---|
| 1 | ba461d48 | test-coverage | 3 | P2 | C | inflated | no | Public refresh command boundary untested; third identical ask |
| 2 | c5deba72 | runtime-correctness | 6 | P1 | F | yes | no | Sixth consecutive P1 on statcan saved predecessor seam |
| 3 | 50b1fced | test-coverage | 4 | P2 | A | yes | yes | coverage_intervals conflict path has no test |
| 4 | 68c1b83f | test-coverage | 4 | P2 | A | yes | no | execute_recorded after failed attempt lacks recovery test |
| 5 | 98f4cb66 | boj-test-coverage | 3 | P2 | A | yes | yes | No test rejects unauthenticated original in v3 evidence group |
| 6 | e5d04616 | specs-fidelity | 3 | P2 | B | yes | no (doc) | Doc still advertises resume command removed by earlier fix |
| 7 | e8148759 | test-coverage | 5 | P2 | C | inflated | no | Selected-content rejection tests; same ask in p3 and p4 |
| 8 | 669916e0 | pboc-test-coverage | 4 | P2 | A | yes | yes | Unsupported-meaning article rejection has no acquisition test |
| 9 | 3a7947a1 | idiomatic-rust-and-quality | 7 | P1 | F | yes | no | Seventh pass on early_easing v1-basis authentication seam |
| 10 | 2cba5fcd | test-coverage | 4 | P2 | A | yes | yes | Corrupt manifest case only deletes; corruption untested |
| 11 | 3d808c4d | test-coverage | 4 | P2 | E | inflated | yes | Extra-vector case guards a branch nobody broadened |
| 12 | 274fd4cf | test-coverage | 3 | P2 | A | yes | yes | Populated-month mask drift rejection untested |
| 13 | 51c1961b | test-coverage | 3 | P2 | A | yes | yes | Receipt-write interruption after publication untested |
| 14 | 8095fe84 | direct-tick-runtime-correctness | 5 | P1 | B | yes | no | Tick hashing added by earlier fix ignores coverage deadline |
| 15 | c7f4fa68 | test-coverage | 7 | P2 | A | inflated | yes | JGB-only decision lacks negative short-rate assertion |
| 16 | b827582f | specs-fidelity | 3 | P1 | C | inflated | no (doc) | README evidence for tick meaning; same as p1 finding |
| 17 | 8d5c2621 | test-coverage | 3 | P2 | A | yes | yes | InvalidPublishedSeal recoverable path untested |
| 18 | 40933bb7 | test-coverage | 4 | P2 | A | yes | yes | Happy path never asserts capture provenance annotations |
| 19 | 2a89ed1a | test-coverage | 3 | P2 | B | yes | yes | Tests bypass validate_time_spec added for the p2 finding |
| 20 | e944d2c6 | test-coverage | 4 | P2 | A | yes | yes | v131 test omits evidence and safety annotations |
| 21 | 48e5e724 | test_coverage | 4 | P2 | A | yes | yes | Offline quarantine retry after association failure untested |
| 22 | ef5fff6e | test-coverage | 9 | P2 | C | inflated | no | One more authentication predicate; p6 asked for all |
| 23 | 8c8b3aab | test-coverage | 3 | P2 | A | yes | yes | Projected record period for January effectivity not asserted |
| 24 | 572fb68c | asset-program-test-coverage | 5 | P2 | A | yes | yes | Inherited 2001 event digest untested on v4 manifest path |
| 25 | 54ad4de8 | october-acquisition-correctness | 3 | P2 | E | inflated | no | ZIP nondeterminism only if a disabled feature is enabled |
| 26 | 95194c87 | test-coverage | 4 | P2 | C | inflated | yes | Fourth variant of "test rejection of mismatched facts" |
| 27 | 4d8227df | test-coverage | 3 | P2 | A | yes | yes | Correction-pair test skips v130 quality annotations |
| 28 | b9669b44 | boj-evidence-correctness | 3 | P2 | A | yes | yes | Replay test opens directory-diff file, not the pinned shard |
| 29 | d566737a | test-coverage | 3 | P2 | A | yes | yes | Missing recorded-attempt binding case never tested |
| 30 | 83ad59c0 | cutoff-test-coverage | 6 | P2 | A | yes | inline | Debt-row cutoff never mutated independently |

Counts: A 18, B 3, C 5, D 0, E 2, F 2. Severity inflated: 8 of 30. In a test file: 20 of 30.

### 4.3 Non-rejected findings at pass 2 (n = 20)

| # | Session | Slice | Sev | Class | Sev ok | Test file | Gist |
|---|---|---|---|---|---|---|---|
| 31 | 90fbccbe | test-coverage | P2 | A | yes | no | Inherited append audit rows have no corruption regression |
| 32 | 6b6cec36 | standards | P2 | A | yes | no | Malformed SQLite treated as retryable I/O, not quarantine |
| 33 | 5434986b | test-coverage | P2 | A | yes | yes | Warmup limit in replay test never binds |
| 34 | f6bab355 | test-coverage | P2 | A | yes | yes | Explicit reuse after default revision advances untested |
| 35 | 01d16039 | test-coverage | P2 | A | yes | yes | Correction of non-current receipt untested |
| 36 | e8bed8ae | specs-fidelity | P2 | A | yes | no | Refresh reports success through ordinary-run receipt |
| 37 | 4f7c1ff9 | test-coverage | P2 | E | inflated | yes | Assert fresh admission identities; already checked |
| 38 | 11ac5584 | candle-restoration-behavior | P2 | F | yes | no | Full archive re-hashed repeatedly during one reconstruction |
| 39 | ecc0ebd1 | test-coverage | P2 | A | yes | yes | Not every association fact asserted |
| 40 | d0ed5bb6 | test-coverage | P2 | A | yes | yes | Scope assertion oracle derived from produced ledger |
| 41 | 59df7a4e | test-coverage | P2 | A | yes | yes | Integration test never authenticates request-association |
| 42 | 7469f222 | test-coverage | P2 | A | yes | yes | Rolling revision identity not bound to workbook hash |
| 43 | 597d8238 | test-coverage | P2 | A | yes | no | Retained-bundle byte-limit boundary untested |
| 44 | 54d89ac9 | example_correctness | P2 | B | yes | no (doc) | Doc example states status where fix emits resolution |
| 45 | 38958ec1 | test-coverage | P2 | A | yes | yes | Forged receipt with foreign Series meaning untested |
| 46 | 498f80ef | test-coverage | P2 | A | yes | yes | Exact ZIP role-association boundary untested |
| 47 | 1f7e4cc5 | test-coverage | P2 | E | inflated | yes | Assert retry re-exposes same capture IDs; speculative |
| 48 | f18fe04c | refresh-runtime-correctness | P1 | F | yes | no | Unchanged-result recheck race against current predecessor |
| 49 | d58799bd | meti-annual-quality | P3 | E | inflated | no (doc) | Rewrite evidence markdown in Simplified Technical English |
| 50 | d190f0e2 | test-coverage-correctness | P2 | E | inflated | yes | Instrument authentication counters, not only scan metadata |

Counts: A 13, B 1, C 0, D 0, E 4, F 2. Inflated: 4 of 20. In a test file: 12 of 20.

Combined (n = 50): A 31 (62%), B 4 (8%), C 5 (10%), D 0, E 6 (12%), F 4 (8%). Inflated 12 (24%).
In a test file 32 (64%).

### 4.4 Rejected findings at pass ≥ 2 (n = 25)

Classes: wrong = false positive. oos = out of scope. done = already addressed. design = design
disagreement. relit = re-litigates an earlier rejection.

| # | Session | Slice | Pass | Sev | Class | Gist of rejection reason |
|---|---|---|---|---|---|---|
| 1 | 11ac5584 | candle-restoration-behavior | 2 | P1 | wrong | Control flow contradicts claim |
| 2 | e8bed8ae | specs-fidelity | 5 | P1 | design | Recapture lineage is a different feature |
| 3 | 7469f222 | safe-rolling-correctness | 2 | P1 | oos | Tracked as issue #1399 |
| 4 | 2cba5fcd | idiomatic-rust | 4 | P1 | wrong | Parser contract already bound; remainder is issue #1506 |
| 5 | 8095fe84 | idiomatic-rust-design | 6 | P1 | relit | Same premise rejected four times in p5 |
| 6 | ef5fff6e | test-coverage | 7 | P2 | design | Issue requires unchanged identity; test asks the opposite |
| 7 | f18fe04c | refresh-runtime-correctness | 2 | P1 | wrong | Omitted months unreachable by construction |
| 8 | 669916e0 | pboc-test-coverage | 4 | P1 | relit | Repeated rejected hypothesis |
| 9 | 3d808c4d | test-coverage | 5 | P2 | design | Independent pin would mirror implementation |
| 10 | 274fd4cf | test-coverage | 6 | P2 | done | Already covered |
| 11 | f041327d | immutable-market-publication | 3 | P1 | oos | Pre-existing; not touched |
| 12 | 2a89ed1a | specs-fidelity | 2 | P1 | design | Cross check not required by spec |
| 13 | 38958ec1 | test-coverage | 3 | P2 | design | Test-only observer adds production hook for no risk |
| 14 | e7938afa | specs-fidelity | 2 | P1 | oos | Contract owned by another issue |
| 15 | 08aab262 | test-coverage | 3 | P2 | done | Existing test already covers it |
| 16 | ba461d48 | code-quality | 3 | P2 | design | Journey test intentional |
| 17 | 3a7947a1 | idiomatic-rust-and-quality | 6 | P1 | wrong | Target compiled; dyn Read needs no trait import |
| 18 | f6bab355 | test-coverage | 2 | P2 | design | Read-only API has no parser seam |
| 19 | 1f7e4cc5 | readability-simplicity | 2 | P1 | wrong | Not reproducible; 42 tests pass |
| 20 | a5b60f50 | test-coverage | 2 | P1 | wrong | Lock test passed twice |
| 21 | d566737a | test-coverage | 4 | P2 | design | Zero counters follow repo convention |
| 22 | d0ed5bb6 | test-coverage | 2 | P1 | wrong | As-of replay would violate truthful availability |
| 23 | cc1cb929 | test-coverage | 3 | P2 | design | Guaranteed by construction |
| 24 | e8148759 | test-coverage | 8 | P2 | design | Needs timing-sensitive data; narrow-test policy |
| 25 | 29ecdab3 | idiomatic-rust | 2 | P2 | design | Descriptor is canonical label, not evidence |

Counts: design 11, wrong 7, oos 3, done 2, relit 2. All 7 "wrong" findings carry P1.

### 4.5 Late P1 findings and cap cost

Selection: severity P1, not rejected, pass ≥ 3. Result: 46 findings. Calibration: 25 random P1
findings at pass 1 (seed 42) out of 115. Classes: PROD = harm on an honest, normal execution
path. GUARD = harm only under forged, corrupted, adversarial, or concurrent input, or a missing
check. QUALITY = docs, scope, naming. Deadline-overrun findings count as PROD but are borderline.

| Class | Late P1 (pass ≥ 3), n = 46 | Pass-1 sample, n = 25 |
|---|---|---|
| PROD | 23 (50%) | 10 (40%) |
| GUARD | 20 (43%) | 11 (44%) |
| QUALITY | 3 (7%) | 3 (12%) |
| FALSE | 0 | 1 (4%) |

Late PROD by pass: 3: 12, 4: 5, 5: 4, 6: 2. Late findings on test files: 6 of 46, all GUARD.

| Cap N | PROD lost (pass > N) | Excluding deadline-only | Sessions affected | % of 128 |
|---|---|---|---|---|
| 2 | 23 | 20 | 10 | 7.8% |
| 3 | 11 | 8 | 7 | 5.5% |
| 4 | 6 | 5 | 4 | 3.1% |
| 5 | 2 | 2 | 2 | 1.6% |
| 6 | 0 | 0 | 0 | 0 |

Origin of the 11 PROD findings at pass ≥ 4: fix-induced 7 (8e2478f8 p4; 8095fe84 p4, p5, p6;
c5deba72 p5, p6; ba461d48 p5 as a half-fix), exposed by a fix 1 (ba461d48 p4), broadened from an
earlier finding 1 (e8148759 p4), pre-existing and unread 1 (6b6cec36 p4), uncertain 1 (8d5c2621
p5).

Every one of the 4 sessions with PROD findings after pass 4 (8095fe84, 8d5c2621, ba461d48,
c5deba72) had a P1 on the same file at pass 1 or 2 and at least one more at pass 3.

The full 46-row table is reproducible with `p1_extract.py` and `p1_report.py`.

## 5. Analysis

### 5.1 Where the time goes

- Parent idle time is the largest component: 44.5% of wall-clock. Reviewer runs are 42.3%. The
  classifier is 13.2%.
- Reviewer runs are short: median 2.6 min at every pass. Pass count, not run duration, drives
  session length.
- The slowest-slice penalty inside a wave is 10% of wave wall. Wave synchronization is not the
  problem.
- Passes after 3 are dominated by single-slice waves (mean size 1.2 to 1.4). Those waves cost the
  same fixed parent overhead per pass for one slice.

### 5.2 Why late passes keep finding things

- Test-coverage slices are the long tail. Their mean highest pass is 3.21 against 1.56 to 1.78
  for other lenses. 55% of test slices go past pass 2.
- Class A dominates late findings: 62% are pre-existing gaps that an earlier pass could have found.
  The reviewer reports one missing negative case per pass, not all gaps at once. The supply of
  "one more untested branch" is near infinite.
- Class B (fix-induced) is 8% of the sample but 7 of the 11 PROD findings at pass ≥ 4. Each one is
  visible from prior findings plus the fix. Chains occur: c5deba72 had 5 generations in one file;
  8095fe84 had 3.
- Class C (re-raises) is 10%, all in long sessions. Token-overlap detection puts pure re-raises at
  4% to 6% of pass ≥ 2 findings. Re-raised findings are rejected almost twice as often as novel
  ones.
- Class D (cross-lens duplicate) is 0% in the sample and 9.2% by location overlap. Lens partition
  works.
- Class E (speculative or style) is 12%, always superseded, never rejected. The author spent a
  pass on each. All 12 severity inflations are E or C.
- Late P1s are real. Late findings are 50% PROD and 0% FALSE; pass-1 findings are 40% PROD. The
  reviewer does not inflate late passes. Sessions that need many passes show a same-file P1 at 2
  or more consecutive passes early on.

### 5.3 Reclassification

- 30.8% of sessions reclassified. 413 slice events followed, almost all remove-then-reactivate
  pairs.
- Only 3 reclassifications changed the slice set. The rest cost a 3-minute classifier run and
  produced nothing.

### 5.4 Rejections

- 44% of late rejections are design disagreements; 28% are false positives, all P1.
- Two rejection patterns repeat across sessions with no memory: a phantom `use std::io::Read`
  compile error (3 sessions) and "expose zero counters through a test hook" (4 sessions).
- Within a session, re-litigation is rare (2 of 25). Per-session rejection reasons do propagate.

## 6. Decisions

| # | Decision | Rationale from the data |
|---|---|---|
| 1 | Remove reclassification. Classification runs once per session; the tool refuses a second run while active slices exist. | 3 of 68 reclassifications changed the slice set. Each cost about 3 min plus slice churn. |
| 2 | Pass cap `max_passes`, default 3, per slice. | Cap 3 keeps 82% of kept findings and loses 11 PROD findings in 7 sessions. Cap 2 doubles the PROD loss. |
| 3 | Judge at the cap instead of a hard stop. Rule: kept P0 or P1 in the last pass, or the same file flagged P1 in two consecutive passes, grants another `max_passes` window. Only P2 or P3 stops the slice. | Every session with PROD after pass 4 matched the same-file P1 rule. Nothing PROD appears at pass 7. Late P2s are 64% test-coverage GUARD or class A gaps. |
| 4 | On `stop`, the last wave's findings return to the parent as `final`. The judge never filters findings. | Late findings are as real as early ones. The parent fixes or rejects them without another wave. |
| 5 | Parallel shots per slice with phase-down. Default 1. Each clean shot subtracts one shot from later passes, floor 1, never increases. Failed shots retry only themselves. | Test-coverage slices find one gap per pass. Two shots in one wave cost one wave of parent overhead instead of two. Phase-down stops paying for shots once the slice is quiet. |
| 6 | Automatic duplicate marking across shots of the same wave: same path, overlapping lines, title token overlap ≥ 0.6. | The same threshold matched 6.2% of later findings to a finding of an earlier pass. That is cross-pass title overlap; the data has no same-wave shots, so it does not measure same-wave precision. 29 of the 31 matched findings were kept, not rejected. Kept is not a confirmed true positive. The analysis tokenizer also drops stop words; the implementation does not. Automatic duplicate marking is an unvalidated hypothesis; measure it on the first multi-shot sessions. |
| 7 | Reviewer prompt: "Report every high-value finding you can support in this run. Do not hold findings for a later pass." | Class A at 62% is a pass-1 exhaustiveness problem. |
| 8 | Reviewer prompt on pass ≥ 2 carries every prior finding with its outcome: addressed, rejected with the verbatim reason, or duplicate. | Catches class B (4 of 4 in the sample), suppresses pure class C re-raises, and prevents in-session re-litigation. |
| 9 | Judge runs in a clean session on the classifier profile unless a `[judge]` profile is set. | Same isolation rules as the classifier; no parent bias. |

### 6.1 Expected effect

Waves at pass 4 or later are 175 of 564 (31%). The cap removes them except where the judge
continues. `quant.py` section 12 applies the full judge rule to each slice at `max_passes = 3`:
91 judgements, 20 `continue` verdicts, in 14 of 129 sessions. Of 208 valid slice runs at pass 4 or
later, 45 (22%) fall inside a continued window. The cap removes the other 163 (78%). Parent idle
time falls in proportion. The simulation reads the live source at a later time, so its session
count differs by one. Shots on test-coverage slices should pull class A
findings into earlier passes; this is not measurable until new sessions run.

### 6.2 Deferred

- Non-convergence heuristic: automatic detection of a slice whose fixes keep producing new P1s on
  one file. The judge rule covers the observed cases.
- Cross-session rejection memory: a short list of rejected finding patterns from `resolution.text`.
  Two patterns recur today.
- Reviewer timeouts and the model switch to `gpt-6-sol`: separate concerns.
- Severity floor for class E: no data yet on whether the exhaustiveness line makes it worse.

## 7. Change log for this pull request

### 7.1 Behavior

- `classify_slices.py` exits with an error when the session already has active slices. Sessions
  whose slices were all removed can classify again. The check runs again under the state lock when
  classification starts, so a slice added in the meantime also stops it.
- Child processes that get no prompt on stdin get a closed stdin. Codex reads stdin to end of file
  even when the prompt is an argument; an inherited open pipe made every shot wait until timeout.
- The Claude Code harness removes the `$schema` key from the inline `--json-schema` value. Claude
  Code rejects a schema that names the 2020-12 meta-schema, so every Claude Code judge and reviewer
  with structured output failed before this change.
- `max_passes` (default 3) in `.agents/multi-shot-review.toml`. A slice that still has findings
  after its budget goes to the judge before the next wave.
- Judge: clean session, `[judge]` profile with fallback to `[classifier]`. Prompt carries the slice
  history and the decision rule. Output validated against
  `references/judge-verdict.schema.json`. Verdicts stored in the slice's `judgements` list, written
  to `<review-dir>/judge/`, and returned in the `judge` array of the run summary. `continue` grants
  a new `max_passes` window. `stop` completes the slice; its last-wave findings return in `out` with
  `"final": true`. Judge failure returns `"st": "judge_failed"` in `err`, exit code 2, and leaves
  the slice untouched for retry.
- Judge rule in the runner: the runner computes the required verdict from the pass history.
  `continue` when the last pass keeps a P0 or P1 finding, or when the same path has a kept P1 in two
  consecutive passes of the current window. Otherwise `stop`. The judge supplies the reason. A
  verdict that contradicts the rule, in either direction, is a judge failure. The judge prompt
  states the window range and says that the consecutive-pass condition applies also to passes
  before the last pass. A superseded finding is kept.
- Judge guards: before a judge starts, the runner stores a `judge_pending` reservation on the
  slice. A concurrent runner does not start a second judge for a reserved pass. A reservation held
  by a stopped process is taken again. A judge timeout is a retryable judge failure. Each judged pass and definition version accepts one verdict; a later verdict for the same pass is
  ignored and recorded as `stale_judgement_ignored`. Judge files carry a random suffix.
- Window: each window starts at the last `continue` pass (or `pass_base`). The runner stores
  `max_passes` on the slice when a window opens. A config change applies from the next window, so it
  does not move the judge point of a wave that already started.
- Reactivation: a reactivated slice definition gets a new `max_passes` window (`pass_base`).
  Verdicts for an earlier definition do not change the budget of the new definition.
- Shots: `add_slice.py --shots <n>`, stored on the slice. Without `--shots`, the slice uses the
  `shots` setting (default 1) from `.agents/multi-shot-review.toml`. Wave size is the shot count minus clean
  runs at earlier passes, floor 1. Failed or timed-out shots retry only themselves with the same
  shot index. Output files of a slice with more than one shot always carry `-shot<n>`, also after
  the wave phases down to one shot. Sibling shots never supersede each other.
- Auto-dedupe: a finding matching a sibling shot's finding in the same wave gets a `duplicate`
  resolution with `"auto": true`. The kept copy is the one with the highest severity, independent
  of shot completion order, because the judge rule reads kept severities. A run whose findings are
  all auto duplicates counts as clean and returns in `out` with `"st": "auto_duplicates"`.
- Reviewer prompt: exhaustiveness sentence on every pass; prior-finding memory block with outcomes
  on pass ≥ 2. The memory covers the current slice definition only. A superseded finding is shown
  as "superseded by a later pass"; the reviewer verifies that it was fixed and reports it again if
  it still applies.
- Run summary: `out` records carry `sh` (shot) and `final`; `judge` array; status `done` when only
  judges ran. A `final` record lists only the open findings; ignored findings already carry their
  outcome. `out` records are built after all shots of the wave finish, so a run that a later shot
  demoted to an automatic duplicate shows `"st": "auto_duplicates"`.

### 7.2 Files

| File | Change |
|---|---|
| `scripts/review_state.py` | Shots, judgements, wave sizing, auto-dedupe, judge input and recording, final runs, prompt memory block, judge execution, run summary fields |
| `scripts/review_config.py` | `max_passes`, `shots`, `[judge]` profile with classifier fallback |
| `scripts/review_result.py` | `parse_judge_verdict`, `JUDGE_SCHEMA_PATH` |
| `scripts/classify_slices.py` | Refuse sessions with active slices; `--shots` guidance in the classifier prompt |
| `scripts/harnesses/base.py` | Abstract `judge_invocation` |
| `scripts/harnesses/codex.py` | Judge invocation with `--output-schema` |
| `scripts/harnesses/claude_code.py` | Judge invocation with inline `--json-schema`; the inline schema drops the `$schema` key |
| `references/judge-verdict.schema.json` | New judge verdict schema |
| `references/classifier-rules.md` | One classification per session; `--shots` selection |
| `references/slice-selection.md` | `--shots` sentence; reclassify guidance removed |
| `docs/extending-harnesses.md` | Judge invocation responsibility; judge result contract |
| `SKILL.md` | Single classification; shots; pass budget and judge semantics; `final` findings |
| `README.md` | `max_passes`, `shots`, `[judge]`, section "Passes, shots, and the judge", auto duplicates |
| `tests/test_review_state.py` | 40 new tests for shots, phase-down, severity-aware auto-dedupe, window budget snapshot, final open findings, closed child stdin, classifier race, judge, judge rule in both directions, judge reservation, judge timeout, Claude Code judge on the classifier profile, window anchor, reactivation window, corrupt-state rejection, memory per definition, auto-dedupe boundaries, prompt memory, classifier refusal, config chain through the CLIs; classifier tests reworked |
| `tests/test_review_config.py` | `max_passes`, `shots`, judge profile, and config chain tests |
| `tests/test_review_harness.py` | Judge invocation tests |
| `tests/test_review_result.py` | New: judge verdict parsing and automatic duplicate validation and rendering tests |
| `reports/2026-09-25-trading-sandbox-pass-analysis.md` | This report |
| `reports/2026-09-25-trading-sandbox-pass-analysis/` | Analysis scripts; `cutoff.py` holds the shared session cutoff |

Test suite after the change: 199 tests, all pass.

### 7.3 Follow-up outside this repository

trading-sandbox `REVIEW.md`: the Test Coverage lens asks for 2 shots and for every gap in one
pass. The three-pass design-boundary rule now points at the judge.

## 8. Reproduction

All scripts read the worktree glob at the top of each file. All scripts that read sessions use the
cutoff in `cutoff.py`: sessions created before `2026-09-25T22:40:00Z`. Set `MSR_CREATED_BEFORE` to
change it. Run from the scripts directory:

```bash
python3 quant.py            # sections 3.1 to 3.10; writes /tmp/msr_quant.txt
python3 inventory.py        # finding inventory; writes /tmp/msr_rows.json
python3 sample_findings.py  # qualitative sample dossiers (seed 7); writes /tmp/msr_dossiers_*.txt
python3 p1_extract.py       # late P1 set and pass-1 sample (seed 42); writes /tmp/msr_p1_data.json
python3 p1_context.py       # lineage context for the 11 PROD findings at pass >= 4
python3 p1_report.py        # section 4.5 tables; writes /tmp/msr_p1_tables.md
```

The qualitative classes in sections 4.2 to 4.5 were assigned by hand and are recorded in
`p1_report.py` (P1 set) and in this file (50-finding sample). Session IDs are the first 8
characters of the review directory name.
