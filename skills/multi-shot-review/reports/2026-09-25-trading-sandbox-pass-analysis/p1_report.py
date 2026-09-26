import json
from collections import Counter, defaultdict
d = json.load(open("/tmp/msr_p1_data.json"))
late, sample = d["late"], d["sample"]
def key(f): return (f["short"], f["slice"], f["pass"], f["id"].rsplit(":",1)[1])
# class, note, gist
C = {
 ("8e2478f8","idiomatic-rust",3,"0"): ("PROD","", "Availability-first ranking returns stale revision over newer ordinal"),
 ("8e2478f8","idiomatic-rust",4,"0"): ("PROD","contradicts p3 fix; fix-induced", "p3 fix regressed evaluation selector to ordinal-first ordering"),
 ("b827582f","specs-fidelity",3,"0"): ("QUALITY","docs/evidence", "README cites unretained archive README for field meanings"),
 ("b827582f","specs-fidelity",4,"0"): ("GUARD","input validation", "admit-csv accepts empty request lacking provider/instrument fields"),
 ("7cefe535","dmo_reprocessing_behavior",3,"0"): ("PROD","", "Reprocess to new dataset fails: publication lock missing"),
 ("8d5c2621","ecb-reconstruction-behavior",5,"0"): ("PROD","fixed in final code; test may have passed", "Damaged manifest not restore-eligible; explicit restore errors out"),
 ("6b6cec36","standards",3,"0"): ("PROD","regression; breaks existing test", "file:// accepted on automated path; local path as provenance"),
 ("6b6cec36","standards",4,"0"): ("PROD","", "Revision hash omits cursor; second shard reuses A's receipt"),
 ("50b1fced","test-coverage",7,"0"): ("GUARD","", "No test for legacy source-tag downgrade of receipts"),
 ("e8148759","code-quality",4,"0"): ("PROD","deadline overrun", "open_until calls unbounded verifier; deadline exceeded"),
 ("e8148759","idiomatic-rust",3,"0"): ("PROD","", "Two selected sources always fail Screening digest compare"),
 ("e8148759","idiomatic-rust",4,"0"): ("GUARD","validation of saved family", "Saved-family parse admits tick/5m feeds on 1h pin"),
 ("e8148759","market-selection-correctness",3,"0"): ("GUARD","validation", "Pin presence checked, not syntax; modified family accepted"),
 ("e8148759","market-selection-correctness",5,"0"): ("GUARD","authentication bypass on forged input", "Pinned intake skips authentication; other shard's digest accepted"),
 ("e8148759","test-coverage",7,"0"): ("GUARD","", "Gate test asserts generic error; authenticate call untested"),
 ("e8148759","test-coverage",7,"1"): ("GUARD","", "No deadline-expiry tests for bounded candle open chain"),
 ("3a7947a1","boj-easing-correctness",4,"0"): ("GUARD","provenance retention", "original_basis not in related set; never re-authenticated"),
 ("3a7947a1","boj-easing-correctness",5,"0"): ("GUARD","fail-closed on corruption", "reopen silently recreates missing generated evidence"),
 ("3a7947a1","boj-easing-specs-fidelity",3,"0"): ("QUALITY","scope", "v2 publishes short-rate series outside issue scope"),
 ("3a7947a1","boj-easing-specs-fidelity",4,"0"): ("GUARD","forged input", "Evidence group validated by IDs only, not content"),
 ("3a7947a1","boj-easing-test-coverage",4,"0"): ("GUARD","", "No test for substituted evidence-group member"),
 ("3a7947a1","idiomatic-rust-and-quality",3,"0"): ("PROD","", "8-row correction replaces 114-row ledger as current"),
 ("3a7947a1","idiomatic-rust-and-quality",4,"0"): ("GUARD","validation", "Basis not verified as v1 contract; helper uses v2"),
 ("3a7947a1","idiomatic-rust-and-quality",7,"0"): ("GUARD","re-raise of p4 correctness", "v1 basis still not authenticated/composed; re-raise"),
 ("8095fe84","direct-tick-runtime-correctness",3,"0"): ("PROD","feature guarantee void; re-raise of p1", "Authority checked, adapter reopens mutable pathname anyway"),
 ("8095fe84","direct-tick-runtime-correctness",4,"0"): ("GUARD","concurrent mutation edge", "StopAfterBatch return skips post-scan authority check"),
 ("8095fe84","direct-tick-runtime-correctness",4,"1"): ("PROD","deadline overrun; fix-induced", "Direct-tick constructor hard-codes expires_at None"),
 ("8095fe84","direct-tick-runtime-correctness",5,"0"): ("PROD","deadline overrun; fix-induced", "Coverage hashes whole SQLite with no deadline check"),
 ("8095fe84","direct-tick-runtime-correctness",6,"0"): ("PROD","fix-induced; fixed in final code", "Symlinked data home fails every direct readiness"),
 ("8095fe84","idiomatic-rust-design",3,"0"): ("PROD","cross-slice dup of runtime p3", "Authority in side vector; adapter never consumes it"),
 ("8095fe84","idiomatic-rust-design",4,"0"): ("GUARD","borderline FALSE; contrived", "mtime/len/inode not content proof; same-inode page write"),
 ("8095fe84","test-coverage",6,"0"): ("GUARD","", "No WAL/SHM sidecar rejection test for direct path"),
 ("ef5fff6e","idiomatic-rust-design",3,"0"): ("PROD","", "Per-request occurrence replaces caller's capture occurrence"),
 ("ef5fff6e","test-coverage",7,"0"): ("GUARD","forged input", "reopen skips association response-fact validation"),
 ("ba461d48","idiomatic-rust",5,"0"): ("PROD","re-raise/variant of rc p3", "Retry of B after C current moves latest back to B"),
 ("ba461d48","refresh-correctness",3,"0"): ("PROD","", "Stale failed intent permanently blocks window refresh"),
 ("ba461d48","refresh-correctness",3,"1"): ("PROD","", "Fallback reuse drops predecessor condition; wrong receipt"),
 ("ba461d48","specs-fidelity",3,"0"): ("PROD","re-raise of p2", "run then refresh creates unrelated lineage (different root)"),
 ("ba461d48","specs-fidelity",4,"0"): ("PROD","", "64-page ceiling; 191-page archive refresh returns InventoryLimit"),
 ("c5deba72","runtime-correctness",3,"0"): ("PROD","interruption scenario", "Interrupted publication + head advance: request never completes"),
 ("c5deba72","runtime-correctness",5,"0"): ("PROD","fix-induced", "Second run returns stale revision omitting July data"),
 ("c5deba72","runtime-correctness",6,"0"): ("PROD","fix-induced", "Intent index ignores predecessor; run fails forever"),
 ("f6bab355","test-coverage",3,"1"): ("GUARD","", "Second completed capture selection untested"),
 ("e5d04616","specs-fidelity",4,"0"): ("QUALITY","doc overclaim; test asserts path fails", "Recovery guide promises unrecoverable pre-publication case"),
 ("2cba5fcd","storage-correctness",3,"0"): ("GUARD","API misuse; borderline FALSE", "Target from root A corrupts root B binding"),
 ("5f442ef6","specs-fidelity",3,"0"): ("GUARD","", "FRED fixture digests self-referential; origin receipts absent"),
}
S = {
 ("ba461d48","refresh-correctness",1,"1"): ("PROD","crash gap", "Revision committed before receipt; retries abort"),
 ("a4d83fec","acquisition-correctness",1,"0"): ("QUALITY","inflated; sub-second", "received_at precedes ZIP pack completion"),
 ("2a89ed1a","specs-fidelity",1,"0"): ("PROD","", "Mode enum causal/lookahead; historical/live rejected"),
 ("cc1cb929","specs-fidelity",1,"0"): ("PROD","", "v1 receipts break completed() scanning"),
 ("1b3afed8","test-coverage",1,"0"): ("GUARD","test compile break", "Unit tests call old 2-arg signature; won't compile"),
 ("e8148759","test-coverage",1,"0"): ("GUARD","", "Parity test compares IDs, never runs Screening"),
 ("e8148759","market-selection-correctness",1,"0"): ("PROD","", "Predicate rejects every selected Family at admission"),
 ("274fd4cf","test-coverage",1,"0"): ("GUARD","", "Test asserts timestamp only, not availability label"),
 ("2cba5fcd","test-coverage",1,"0"): ("GUARD","", "No lost-binding reconstruction test"),
 ("6b6cec36","standards",1,"1"): ("PROD","edge: same second", "Occurrence collides for two shards in same second"),
 ("59df7a4e","code-quality",1,"0"): ("PROD","", "headline_and_core selection always fails at publish"),
 ("2cba5fcd","provider-correctness",1,"0"): ("GUARD","fail-closed", "RevisionNotFound treated as no-predecessor"),
 ("7f34b67a","test-coverage",1,"0"): ("GUARD","", "Rejection test covers 1 of 3 prose claims"),
 ("ac134a64","idiomatic_rust",1,"0"): ("PROD","", "Sealed shard has 1 raw artifact not 11; assertion fails"),
 ("d0ed5bb6","correctness",1,"0"): ("PROD","", "Sept-2021 ceiling expiry dropped from published data"),
 ("8095fe84","specs-fidelity",1,"0"): ("PROD","", "Adapter opens mutable path after authority check"),
 ("b827582f","specs-fidelity",1,"0"): ("QUALITY","docs/evidence", "README evidence for tick meaning unretained"),
 ("5f442ef6","specs-fidelity",1,"0"): ("GUARD","", "Test runs two imports, not refresh/reprocess"),
 ("01d16039","runtime-correctness",1,"0"): ("GUARD","fail-closed", "Recovery returns before basis authentication"),
 ("1cb86165","test-coverage",1,"0"): ("GUARD","", "No partial-history-response regression"),
 ("e8148759","market-selection-correctness",1,"1"): ("PROD","", "Tick/non-default selections sealable but not screenable"),
 ("5434986b","runtime-correctness",1,"0"): ("FALSE","swap-and-restore race", "Verify path vs fd swap race; contrived"),
 ("38958ec1","idiomatic-rust-quality",1,"0"): ("GUARD","corrupted receipt", "Receipt identity not bound to revision lineage"),
 ("f6bab355","test-coverage",1,"1"): ("GUARD","", "No raw-evidence corruption test on reuse"),
 ("8c8b3aab","specs-fidelity",1,"0"): ("QUALITY","citation", "January rate cites broader statement, not tier"),
}
for f in late: f["cls"],f["note"],f["gist"] = C[key(f)]
for f in sample: f["cls"],f["note"],f["gist"] = S[key(f)]
assert len(C)==len(late)==46 and len(S)==len(sample)==25
out=[]; P=out.append
P("# Late P1 findings: what would a pass cap cost?\n")
P("Data: `/home/danny/.worktrees/*trading-sandbox*/.review/*/_state.json`, 132 state files (116 with >=1 finding). Selection: severity P1, status != ignored (all are `superseded`), pass >= 3. Result: **46 findings** (not 60-70; the 65 P1 at pass>=3 includes 19 `ignored`). Calibration: 25 random (seed 42) P1 non-ignored findings at pass 1 out of 115.\n")
P("Classification rule applied: harm on an honest, normal execution path -> PROD. Harm only under forged/corrupted/adversarial/concurrent input, or a missing check -> GUARD. Docs, scope, naming -> QUALITY. Deadline-overrun findings are counted PROD but tagged; they are borderline.\n")
P("Caveat: `superseded` = slice removed or re-run, not proven fixed. Spot checks (8d5c2621 p5, 8095fe84 p6) show the fix in final code. Per-pass git history is absent: review target is `uncommitted`; the branch is committed once at session end.\n")
P("## 1. All late P1 findings\n")
P("| # | sess | slice | pass | test? | class | gist | note |"); P("|--|--|--|--|--|--|--|--|")
for i,f in enumerate(sorted(late,key=lambda f:(f["short"],f["slice"],f["pass"])),1):
    P(f"| {i} | {f['short']} | {f['slice']} | {f['pass']} | {'y' if f['test'] else ''} | {f['cls']} | {f['gist']} | {f['note']} |")
P("\n## 2. Counts by class\n")
cl=Counter(f["cls"] for f in late); cs=Counter(f["cls"] for f in sample)
P("| class | late P1 (pass>=3), n=46 | pass-1 sample, n=25 |"); P("|--|--|--|")
for k in ["PROD","GUARD","QUALITY","FALSE","UNCLEAR"]: P(f"| {k} | {cl[k]} ({100*cl[k]/46:.0f}%) | {cs[k]} ({100*cs[k]/25:.0f}%) |")
dl=sum(1 for f in late if f["cls"]=="PROD" and "deadline" in f["note"])
P(f"\nLate PROD includes {dl} deadline-overrun findings (borderline) and 1 cross-slice duplicate pair (8095fe84 p3 runtime vs idiomatic) plus 1 contradicting pair (8e2478f8 p3/p4). Distinct late PROD issues ~= {cl['PROD']-1-dl} to {cl['PROD']-1}.")
P(f"\nLate PROD by pass: {dict(sorted(Counter(f['pass'] for f in late if f['cls']=='PROD').items()))}. Late findings on test files: {sum(f['test'] for f in late)}/46, all GUARD.")
P("\n## 3. Cap-loss table\n")
P("Denominator: 128 sessions (user figure; data has 132 state files, 116 with findings).\n")
P("| cap N | PROD lost (pass>N) | ...excl. deadline-only | sessions affected | % of 128 |"); P("|--|--|--|--|--|")
for N in range(2,7):
    lost=[f for f in late if f["cls"]=="PROD" and f["pass"]>N]
    strict=[f for f in lost if "deadline" not in f["note"]]
    ss={f["short"] for f in lost}
    P(f"| {N} | {len(lost)} | {len(strict)} | {len(ss)} ({', '.join(sorted(ss))}) | {100*len(ss)/128:.1f}% |")
open("/tmp/msr_p1_tables.md","w").write("\n".join(out)+"\n")
print("\n".join(out))
