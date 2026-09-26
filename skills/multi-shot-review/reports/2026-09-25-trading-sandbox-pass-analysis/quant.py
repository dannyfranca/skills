#!/usr/bin/env python3
"""Quantitative analysis of multi-shot review sessions (trading-sandbox worktrees)."""
import glob
import json
import os
import re
import statistics as st
from collections import Counter, defaultdict
from datetime import datetime

from cutoff import in_cutoff

GLOB = "/home/danny/.worktrees/*trading-sandbox*/.review/*/_state.json"
FAILED = {"failed", "timeout"}
VALID = {"findings", "no_findings", "ignored", "uncertain", "quiet"}
WAVE_GAP_S = 60
STOP = set("""the a an and or of to in is are was be for on with that this it its as by not no from at
into but if then than so does do did has have had which when while only also already still all any
each same both can could would should will may must here there these those about after before
because via per over under between out up down more less new one two""".split())


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def mins(a, b):
    return (ts(b) - ts(a)).total_seconds() / 60


def pct(n, d):
    return f"{100 * n / d:5.1f}%" if d else "  n/a"


def q(xs, p):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def fmt(x, w=7, d=1):
    return f"{x:{w}.{d}f}" if isinstance(x, (int, float)) and x == x else f"{'n/a':>{w}}"


def table(headers, rows):
    cols = [max(len(str(h)), *(len(str(r[i])) for r in rows)) for i, h in enumerate(headers)] if rows else [len(h) for h in headers]
    line = " | ".join(str(h).ljust(c) for h, c in zip(headers, cols))
    out = [line, "-+-".join("-" * c for c in cols)]
    for r in rows:
        out.append(" | ".join(str(v).ljust(c) for v, c in zip(r, cols)))
    return "\n".join(out)


def lens(name):
    n = name.lower()
    if any(k in n for k in ("idiomatic", "rust", "design")):
        return "rust"
    if any(k in n for k in ("test", "coverage")):
        return "tests"
    if any(k in n for k in ("spec", "fidelity", "prd")):
        return "specs"
    return "other"


def is_test_path(p):
    p = p or ""
    return "/tests/" in p or "_test" in p or p.endswith("tests.rs") or p.startswith("tests/")


def tokens(title):
    return {t for t in re.findall(r"[a-z0-9_]+", (title or "").lower()) if t not in STOP and len(t) > 1}


def pass_bucket(p, cap=7):
    return f"{cap}+" if p >= cap else str(p)


# ----------------------------------------------------------------- load
sessions = []
for f in sorted(glob.glob(GLOB)):
    s = json.load(open(f))
    if not in_cutoff(s):
        continue
    s["_path"] = f
    sessions.append(s)

runs = []       # dicts with session, slice, lens, pass, status, start, end, minutes, findings(list)
findings = []   # flattened finding dicts with run refs
slices = []
status_counter = Counter()
for s in sessions:
    sid = s["session"]["review_dir"]
    for name, sl in s["slices"].items():
        srec = {"session": sid, "name": name, "lens": lens(name), "removed": sl.get("removed"), "runs": []}
        slices.append(srec)
        for r in sl["runs"]:
            status_counter[r["status"]] += 1
            if r["status"] == "running" or not r.get("ended_at"):
                continue
            rec = {
                "session": sid, "slice": name, "lens": srec["lens"], "pass": r["pass"], "status": r["status"],
                "start": r["started_at"], "end": r["ended_at"], "minutes": mins(r["started_at"], r["ended_at"]),
                "finding_count": r.get("finding_count") or 0, "failed": r["status"] in FAILED, "findings": [],
                "model": r.get("model"),
            }
            fl = []
            if r.get("findings_archive") and os.path.exists(r["findings_archive"]):
                fl = json.load(open(r["findings_archive"]))["findings"]
            elif r.get("findings"):
                fl = r["findings"]
            for fi in fl:
                res = fi.get("resolution") or {}
                frec = {
                    "session": sid, "slice": name, "lens": srec["lens"], "pass": r["pass"], "run": rec,
                    "severity": fi.get("severity", "?"), "status": fi.get("status", "open"),
                    "kind": res.get("kind"), "reason": res.get("text"), "title": fi.get("title", ""),
                    "path": (fi.get("location") or {}).get("path"),
                    "start_line": (fi.get("location") or {}).get("start_line"),
                }
                frec["ignored"] = frec["status"] == "ignored" and frec["kind"] != "duplicate"
                frec["duplicate"] = frec["kind"] == "duplicate"
                frec["kept"] = not frec["ignored"] and not frec["duplicate"]
                rec["findings"].append(frec)
                findings.append(frec)
            runs.append(rec)
            srec["runs"].append(rec)

valid = [r for r in runs if not r["failed"]]
failed = [r for r in runs if r["failed"]]
out = []
P = out.append

P(f"Data: {len(sessions)} sessions from {GLOB}")
P(f"Run status raw counts: {dict(status_counter)}  (running/unfinished excluded from everything)")
P(f"Finding status values seen: {dict(Counter(f['status'] for f in findings))}; resolution.kind: {dict(Counter(f['kind'] for f in findings))}")
P("Definitions: ignored = status ignored & kind rejected; duplicate = kind duplicate; kept = neither. Reason field = resolution.text\n")

# ----------------------------------------------------------------- 1 volume
by_sess_runs = defaultdict(list)
for r in runs:
    by_sess_runs[r["session"]].append(r)
wall = {}
for s in sessions:
    sid = s["session"]["review_dir"]
    ends = [r["end"] for r in by_sess_runs.get(sid, [])] + [c.get("ended_at") for c in s["classifications"] if c.get("ended_at")]
    if ends:
        wall[sid] = mins(s["session"]["created_at"], max(ends))
wl = list(wall.values())
P("## 1. Volume")
P(table(["metric", "value"], [
    ["sessions", len(sessions)],
    ["sessions with >=1 finished run", len(by_sess_runs)],
    ["slices (incl. removed)", len(slices)],
    ["slices removed", sum(1 for s in slices if s["removed"])],
    ["runs finished", len(runs)],
    ["runs valid (findings/no_findings/ignored)", len(valid)],
    ["runs failed/timeout", f"{len(failed)} ({dict(Counter(r['status'] for r in failed))})"],
    ["total run-minutes valid", fmt(sum(r["minutes"] for r in valid))],
    ["total run-minutes failed", fmt(sum(r["minutes"] for r in failed))],
    ["mean valid run minutes", fmt(st.mean(r["minutes"] for r in valid))],
    ["total session wall-clock minutes", fmt(sum(wl))],
    ["session wall-clock median / p90 / max (min)", f"{fmt(q(wl,.5))} / {fmt(q(wl,.9))} / {fmt(max(wl))}"],
    ["findings total / kept / ignored / duplicate", f"{len(findings)} / {sum(f['kept'] for f in findings)} / {sum(f['ignored'] for f in findings)} / {sum(f['duplicate'] for f in findings)}"],
]))

# ----------------------------------------------------------------- 2 passes per slice
P("\n## 2. Highest pass reached per slice (all finished runs)")
buckets = ["1", "2", "3", "4", "5", "6", "7+"]
def dist_rows(groups):
    rows = []
    for g, sls in groups:
        c = Counter(pass_bucket(max(r["pass"] for r in s["runs"])) for s in sls if s["runs"])
        n = sum(c.values())
        rows.append([g, n] + [f"{c[b]} ({pct(c[b], n).strip()})" for b in buckets] + [fmt(st.mean(max(r["pass"] for r in s["runs"]) for s in sls if s["runs"]), 5, 2)])
    return rows
groups = [("all", slices)] + [(l, [s for s in slices if s["lens"] == l]) for l in ("rust", "tests", "specs", "other")]
P(table(["group", "slices"] + [f"p{b}" for b in buckets] + ["mean"], dist_rows(groups)))
P(f"slices with zero finished runs: {sum(1 for s in slices if not s['runs'])}")
P("'other' lens top names: " + ", ".join(f"{n}({c})" for n, c in Counter(s['name'] for s in slices if s['lens']=='other').most_common(8)))

# ----------------------------------------------------------------- 3 by pass
P("\n## 3. By pass number (valid runs only)")
rows = []
for pb in ["1", "2", "3", "4", "5", "6", "7", "8+"]:
    rs = [r for r in valid if pass_bucket(r["pass"], 8) == pb]
    fs = [f for r in rs for f in r["findings"]]
    sev = Counter(f["severity"] for f in fs)
    fr = [r for r in failed if pass_bucket(r["pass"], 8) == pb]
    rows.append([pb, len(rs), len(fr), fmt(st.mean(r["finding_count"] for r in rs), 5, 2) if rs else "n/a",
                 pct(sum(1 for r in rs if r["finding_count"] > 0), len(rs)), len(fs),
                 "/".join(str(sev[s]) for s in ("P0", "P1", "P2", "P3")),
                 pct(sum(f["ignored"] for f in fs), len(fs)), pct(sum(f["duplicate"] for f in fs), len(fs)),
                 fmt(st.mean(r["minutes"] for r in rs), 5) if rs else "n/a", fmt(st.median(r["minutes"] for r in rs), 5) if rs else "n/a"])
P(table(["pass", "runs", "failed", "mean#find", "%runs>=1", "findings", "P0/P1/P2/P3", "%ignored", "%dup", "mean min", "med min"], rows))

# ----------------------------------------------------------------- 4 cap simulation
P("\n## 4. Cap simulation: kept (non-ignored, non-duplicate) finding records at pass > N (a re-raise counts again)")
n_sess = len(by_sess_runs)
def cap_rows(fs, label):
    rows = []
    for N in range(1, 9):
        late = [f for f in fs if f["pass"] > N]
        sev = Counter(f["severity"] for f in late)
        hs = {f["session"] for f in late if f["severity"] in ("P0", "P1")}
        anys = {f["session"] for f in late}
        rows.append([label, N, len(late), sev["P0"], sev["P1"], sev["P2"], sev["P3"], f"{len(hs)} ({pct(len(hs), n_sess).strip()})", f"{len(anys)} ({pct(len(anys), n_sess).strip()})"])
    return rows
kept = [f for f in findings if f["kept"]]
kept_nontest = [f for f in kept if not is_test_path(f["path"])]
P(table(["set", "cap N", "lost", "P0", "P1", "P2", "P3", "sessions w/ lost P0/P1", "sessions w/ any lost"],
        cap_rows(kept, "all") + cap_rows(kept_nontest, "non-test")))
P(f"kept findings: {len(kept)} total, {len(kept_nontest)} non-test path, {len(kept)-len(kept_nontest)} test path. sessions denominator={n_sess}")

# ----------------------------------------------------------------- 5 last kept finding pass per slice
P("\n## 5. Per slice: pass of last kept (non-ignored, non-dup) finding")
c = Counter()
for s in slices:
    if not s["runs"]:
        continue
    ps = [f["pass"] for r in s["runs"] for f in r["findings"] if f["kept"]]
    c[pass_bucket(max(ps)) if ps else "none"] += 1
n = sum(c.values())
P(table(["last kept pass"] + ["none"] + buckets, [["slices"] + [f"{c[b]} ({pct(c[b], n).strip()})" for b in ["none"] + buckets]]))
c2 = Counter()
for s in slices:
    if not s["runs"]:
        continue
    ps = [f["pass"] for r in s["runs"] for f in r["findings"]]
    c2[pass_bucket(max(ps)) if ps else "none"] += 1
P(table(["last ANY finding pass"] + ["none"] + buckets, [["slices"] + [f"{c2[b]} ({pct(c2[b], n).strip()})" for b in ["none"] + buckets]]))

# ----------------------------------------------------------------- 6 waves
P("\n## 6. Wave structure (runs of same session+pass whose starts chain within 60s)")
waves_by_sess = defaultdict(list)
for sid, rs in by_sess_runs.items():
    bypass = defaultdict(list)
    for r in rs:
        bypass[r["pass"]].append(r)
    for p, prs in bypass.items():
        prs.sort(key=lambda r: r["start"])
        cur = [prs[0]]
        for r in prs[1:]:
            if (ts(r["start"]) - ts(cur[-1]["start"])).total_seconds() <= WAVE_GAP_S:
                cur.append(r)
            else:
                waves_by_sess[sid].append(cur)
                cur = [r]
        waves_by_sess[sid].append(cur)
    waves_by_sess[sid].sort(key=lambda w: w[0]["start"])
waves = [w for ws in waves_by_sess.values() for w in ws]
penal = []
for w in waves:
    d = [r["minutes"] for r in w]
    penal.append((max(d) - st.median(d), max(d), st.median(d), len(w)))
multi = [p for p in penal if p[3] > 1]
gaps = []
neg = 0
gap_by_sess = defaultdict(list)
for sid, ws in waves_by_sess.items():
    for a, b in zip(ws, ws[1:]):
        g = (ts(b[0]["start"]) - max(ts(r["end"]) for r in a)).total_seconds() / 60
        if g < 0:
            neg += 1
        gaps.append(max(g, 0))
        gap_by_sess[sid].append(max(g, 0))
P(table(["metric", "value"], [
    ["waves", len(waves)],
    ["waves with >1 run", len(multi)],
    ["wave size distribution", dict(sorted(Counter(p[3] for p in penal).items()))],
    ["mean wave max minutes", fmt(st.mean(p[1] for p in penal))],
    ["mean wave median minutes", fmt(st.mean(p[2] for p in penal))],
    ["slowest-slice penalty mean (all waves) min", fmt(st.mean(p[0] for p in penal))],
    ["slowest-slice penalty mean (multi-run waves) min", fmt(st.mean(p[0] for p in multi))],
    ["slowest-slice penalty median (multi-run) min", fmt(st.median(p[0] for p in multi))],
    ["slowest-slice penalty total min", fmt(sum(p[0] for p in penal))],
    ["penalty as % of wave wall (sum max)", pct(sum(p[0] for p in penal), sum(p[1] for p in penal))],
    ["penalty total excl. failed/timeout runs min", fmt(sum(max(d) - st.median(d) for w in waves if (d := [r["minutes"] for r in w if not r["failed"]])))],
    ["waves containing a failed/timeout run", sum(1 for w in waves if any(r["failed"] for r in w))],
    ["inter-wave gaps (n)", len(gaps)],
    ["gaps negative/overlapping (clamped to 0)", neg],
    ["gap median / mean / p90 / total min", f"{fmt(q(gaps,.5))} / {fmt(st.mean(gaps))} / {fmt(q(gaps,.9))} / {fmt(sum(gaps))}"],
]))
rows = []
for pb in ["1", "2", "3", "4", "5", "6+"]:
    ws = [(w, p) for w, p in zip(waves, penal) if pass_bucket(w[0]["pass"], 6) == pb]
    if ws:
        rows.append([pb, len(ws), fmt(st.mean(len(w) for w, _ in ws), 5, 2), fmt(st.mean(p[1] for _, p in ws)), fmt(st.mean(p[0] for _, p in ws))])
P(table(["wave pass", "waves", "mean size", "mean max min", "mean penalty min"], rows))

# ----------------------------------------------------------------- 7 time decomposition
P("\n## 7. Time decomposition per session (classifier / run wall = sum of wave max / parent gap)")
rows = []
per = []
for s in sessions:
    sid = s["session"]["review_dir"]
    if sid not in by_sess_runs:
        continue
    cl = sum(mins(c["started_at"], c["ended_at"]) for c in s["classifications"] if c.get("ended_at"))
    rw = sum(max(r["minutes"] for r in w) for w in waves_by_sess[sid])
    gp = sum(gap_by_sess[sid])
    per.append((cl, rw, gp, wall[sid]))
tot = [sum(x[i] for x in per) for i in range(4)]
med = [st.median(x[i] for x in per) for i in range(4)]
mean = [st.mean(x[i] for x in per) for i in range(4)]
acc = tot[0] + tot[1] + tot[2]
P(table(["component", "total min", "% of accounted", "% of wall", "median/session", "mean/session"], [
    ["classifier", fmt(tot[0]), pct(tot[0], acc), pct(tot[0], tot[3]), fmt(med[0]), fmt(mean[0])],
    ["run wall (wave max)", fmt(tot[1]), pct(tot[1], acc), pct(tot[1], tot[3]), fmt(med[1]), fmt(mean[1])],
    ["parent gap", fmt(tot[2]), pct(tot[2], acc), pct(tot[2], tot[3]), fmt(med[2]), fmt(mean[2])],
    ["session wall-clock", fmt(tot[3]), "", "", fmt(med[3]), fmt(mean[3])],
    ["unaccounted (wall - sum)", fmt(tot[3] - acc), "", pct(tot[3] - acc, tot[3]), "", ""],
]))

def union_len(iv):
    iv = sorted(iv)
    tot = 0
    cur = None
    for a, b in iv:
        if cur is None or a > cur[1]:
            if cur:
                tot += (cur[1] - cur[0]).total_seconds() / 60
            cur = [a, b]
        else:
            cur[1] = max(cur[1], b)
    if cur:
        tot += (cur[1] - cur[0]).total_seconds() / 60
    return tot
P("\nInterval-union variant (no double counting): busy = union of run intervals; classifier = union of classifier intervals not overlapping runs; idle = wall - busy - classifier")
per2 = []
for s_ in sessions:
    sid = s_["session"]["review_dir"]
    if sid not in by_sess_runs:
        continue
    riv = [(ts(r["start"]), ts(r["end"])) for r in by_sess_runs[sid]]
    civ = [(ts(c["started_at"]), ts(c["ended_at"])) for c in s_["classifications"] if c.get("ended_at")]
    busy = union_len(riv)
    both = union_len(riv + civ)
    per2.append((both - busy, busy, wall[sid] - both, wall[sid]))
tot2 = [sum(x[i] for x in per2) for i in range(4)]
med2 = [st.median(x[i] for x in per2) for i in range(4)]
P(table(["component", "total min", "% of wall", "median/session", "mean/session"], [
    ["classifier (not overlapping runs)", fmt(tot2[0]), pct(tot2[0], tot2[3]), fmt(med2[0]), fmt(tot2[0]/len(per2))],
    ["runs busy (union)", fmt(tot2[1]), pct(tot2[1], tot2[3]), fmt(med2[1]), fmt(tot2[1]/len(per2))],
    ["idle = parent fix/triage", fmt(tot2[2]), pct(tot2[2], tot2[3]), fmt(med2[2]), fmt(tot2[2]/len(per2))],
    ["session wall-clock", fmt(tot2[3]), "", fmt(med2[3]), fmt(tot2[3]/len(per2))],
]))
idle_share = sorted(x[2] / x[3] for x in per2 if x[3] > 0)
P(f"idle share per session: median {100*q(idle_share,.5):.0f}%, p90 {100*q(idle_share,.9):.0f}%; sessions >50% idle: {sum(1 for x in idle_share if x > .5)}/{len(idle_share)}")

# ----------------------------------------------------------------- 8 classifier
P("\n## 8. Classifier")
cc = Counter(len(s["classifications"]) for s in sessions)
cd = [mins(c["started_at"], c["ended_at"]) for s in sessions for c in s["classifications"] if c.get("ended_at")]
cstat = Counter(c["status"] for s in sessions for c in s["classifications"])
second = [s for s in sessions if len(s["classifications"]) >= 2]
after = Counter()
after_by_sess = []
net_removed, net_added = [], []
for s in second:
    first_end = ts(s["classifications"][0]["ended_at"])
    ev = Counter(e["event"] for e in s["history"] if e["event"].startswith("slice_") and ts(e["at"]) > first_end)
    after.update(ev)
    after_by_sess.append(sum(ev.values()))
    evs = [e for e in s["history"] if e["event"].startswith("slice_") and ts(e["at"]) > first_end]
    final = {}
    for e in evs:
        final[e["slice"]] = e["event"]
    net_removed.append(sum(1 for v in final.values() if v == "slice_removed"))
    net_added.append(sum(1 for v in final.values() if v == "slice_added"))
P(table(["metric", "value"], [
    ["classifications per session", dict(sorted(cc.items()))],
    ["classification status", dict(cstat)],
    ["duration median / mean / p90 / max min", f"{fmt(q(cd,.5))} / {fmt(st.mean(cd))} / {fmt(q(cd,.9))} / {fmt(max(cd))}"],
    ["classifier models", dict(Counter(c.get("model") for s in sessions for c in s["classifications"]))],
    ["sessions with >=2 classifications", f"{len(second)} ({pct(len(second), len(sessions)).strip()})"],
    ["slice events after 1st classification (those sessions)", dict(after)],
    ["sessions (>=2 class.) with zero slice changes after 1st", sum(1 for x in after_by_sess if x == 0)],
    ["median slice events after 1st per such session", fmt(st.median(after_by_sess), 5) if after_by_sess else "n/a"],
    ["net slices still removed at end (sum / sessions with any)", f"{sum(net_removed)} / {sum(1 for x in net_removed if x)}"],
    ["net slices newly added at end (sum / sessions with any)", f"{sum(net_added)} / {sum(1 for x in net_added if x)}"],
    ["reclassifications (2nd+) that changed slice set", sum(1 for a, b in zip(net_removed, net_added) if a or b)],
]))

# ----------------------------------------------------------------- 9 late-pass ignore rate
P("\n## 9. Late-pass ignore rate (findings at pass >= 3)")
late = [f for f in findings if f["pass"] >= 3]
early = [f for f in findings if f["pass"] < 3]
P(table(["set", "findings", "ignored", "%ignored", "dup", "%dup"], [
    ["pass>=3", len(late), sum(f["ignored"] for f in late), pct(sum(f["ignored"] for f in late), len(late)), sum(f["duplicate"] for f in late), pct(sum(f["duplicate"] for f in late), len(late))],
    ["pass<3", len(early), sum(f["ignored"] for f in early), pct(sum(f["ignored"] for f in early), len(early)), sum(f["duplicate"] for f in early), pct(sum(f["duplicate"] for f in early), len(early))],
]))
reasons = [f["reason"] for f in findings if f["ignored"] and f["reason"]]
P(f"reason field: resolution.text (present on {len(reasons)} ignored findings); duplicate uses resolution.finding_id")
uni, bi = Counter(), Counter()
for r in reasons:
    toks = [t for t in re.findall(r"[a-z][a-z0-9_]+", r.lower()) if t not in STOP]
    uni.update(set(toks))
    bi.update(set(zip(toks, toks[1:])))
P("top unigrams (docs containing): " + ", ".join(f"{w}({c})" for w, c in uni.most_common(15)))
P("top bigrams (docs containing): " + ", ".join(f"{a} {b}({c})" for (a, b), c in bi.most_common(12)))
lead = Counter(" ".join(re.findall(r"[a-z]+", r.lower())[:2]) for r in reasons)
P("leading two words: " + ", ".join(f"'{w}'({c})" for w, c in lead.most_common(10)))

# ----------------------------------------------------------------- 10 re-raise
P("\n## 10. Re-raise detection (title token overlap; overlap = |A&B|/min(|A|,|B|), jaccard = |A&B|/|A|B|)")
def rer(metric, thr=0.6, consecutive_only=False):
    later = matched = matched_ign = matched_kept = 0
    for s in slices:
        byp = defaultdict(list)
        for r in s["runs"]:
            for f in r["findings"]:
                byp[f["pass"]].append(f)
        for p in sorted(byp):
            if p < 2:
                continue
            prev = [f for pp, fs in byp.items() if (pp == p - 1 if consecutive_only else pp < p) for f in fs]
            for f in byp[p]:
                later += 1
                A = tokens(f["title"])
                best = None
                for g in prev:
                    B = tokens(g["title"])
                    if not A or not B:
                        continue
                    sc = len(A & B) / (min(len(A), len(B)) if metric == "overlap" else len(A | B))
                    if sc >= thr and (best is None or sc > best[0]):
                        best = (sc, g)
                if best:
                    matched += 1
                    if best[1]["ignored"]:
                        matched_ign += 1
                    else:
                        matched_kept += 1
    return [metric + (" consecutive" if consecutive_only else " any earlier"), thr, later, matched, pct(matched, later), matched_ign, matched_kept]
P(table(["metric", "thr", "pass>=2 findings", "re-raised", "%", "earlier was ignored", "earlier was kept"],
        [rer("overlap"), rer("overlap", consecutive_only=True), rer("jaccard"), rer("jaccard", 0.4)]))
# same-slice re-raise by later finding's own fate
def rer_fate():
    c = Counter()
    for s in slices:
        byp = defaultdict(list)
        for r in s["runs"]:
            for f in r["findings"]:
                byp[f["pass"]].append(f)
        for p in sorted(byp):
            if p < 2:
                continue
            prev = [f for pp, fs in byp.items() if pp < p for f in fs]
            for f in byp[p]:
                A = tokens(f["title"])
                hit = any(A and tokens(g["title"]) and len(A & tokens(g["title"])) / min(len(A), len(tokens(g["title"]))) >= 0.6 for g in prev)
                c[("re-raised" if hit else "novel", "ignored" if f["ignored"] else ("dup" if f["duplicate"] else "kept"))] += 1
    return c
c = rer_fate()
P(table(["later finding", "kept", "ignored", "dup", "%ignored"], [
    [k, c[(k, "kept")], c[(k, "ignored")], c[(k, "dup")], pct(c[(k, "ignored")], c[(k, "kept")] + c[(k, "ignored")] + c[(k, "dup")])] for k in ("re-raised", "novel")]))

# ----------------------------------------------------------------- 11 cross-lens duplication
P("\n## 11. Cross-lens duplication (same session+pass, other slice, same path, start_line within +-5)")
grp = defaultdict(list)
for f in findings:
    grp[(f["session"], f["pass"])].append(f)
tot_f = dupd = 0
by_sev = Counter(); by_sev_d = Counter(); by_lens = Counter(); by_lens_d = Counter()
pairs_slices = Counter()
for key, fs in grp.items():
    if len({f["slice"] for f in fs}) < 2:
        for f in fs:
            tot_f += 1; by_sev[f["severity"]] += 1; by_lens[f["lens"]] += 1
        continue
    for f in fs:
        tot_f += 1; by_sev[f["severity"]] += 1; by_lens[f["lens"]] += 1
        hit = [g for g in fs if g["slice"] != f["slice"] and g["path"] == f["path"] and f["start_line"] is not None and g["start_line"] is not None and abs(g["start_line"] - f["start_line"]) <= 5]
        if hit:
            dupd += 1; by_sev_d[f["severity"]] += 1; by_lens_d[f["lens"]] += 1
            pairs_slices[tuple(sorted({f["lens"], hit[0]["lens"]}))] += 1
multi_slice_f = sum(len(fs) for fs in grp.values() if len({f["slice"] for f in fs}) >= 2)
P(table(["metric", "value"], [
    ["findings (all)", tot_f],
    ["findings in session+pass with >=2 slices reporting", multi_slice_f],
    ["overlapping with another slice", f"{dupd} ({pct(dupd, tot_f).strip()} of all; {pct(dupd, multi_slice_f).strip()} of multi-slice groups)"],
    ["by severity (overlap/total)", {s: f"{by_sev_d[s]}/{by_sev[s]}" for s in ("P1", "P2", "P3")}],
    ["by lens (overlap/total)", {l: f"{by_lens_d[l]}/{by_lens[l]}" for l in ("rust", "tests", "specs", "other")}],
    ["lens pairs (counted per finding)", dict(pairs_slices.most_common())],
    ["overlapping findings later ignored", sum(1 for key, fs in grp.items() for f in fs if f["ignored"] and any(g["slice"] != f["slice"] and g["path"] == f["path"] and f["start_line"] is not None and g["start_line"] is not None and abs(g["start_line"] - f["start_line"]) <= 5 for g in fs))],
]))

# ----------------------------------------------------------------- 12 judge simulation
P("\n## 12. Judge simulation at max_passes = 3 (continue: kept P0/P1 in the last window pass, or kept P1 on one path in two consecutive window passes)")
CAP = 3
judged = continued = runs_after_cap = runs_kept_by_judge = 0
sess_continue = set()
for s in slices:
    byp = defaultdict(list)
    for r in s["runs"]:
        if not r["failed"]:
            byp[r["pass"]].append(r)
    if not byp:
        continue
    last = max(byp)
    runs_after_cap += sum(len(rs) for p, rs in byp.items() if p > CAP)
    window_end = CAP
    while last > window_end:
        judged += 1
        kept = {p: [f for r in byp.get(p, []) for f in r["findings"] if f["kept"]] for p in range(window_end - CAP + 1, window_end + 1)}
        high_last = any(f["severity"] in ("P0", "P1") for f in kept[window_end])
        p1_paths = {p: {f["path"] for f in fs if f["severity"] == "P1"} for p, fs in kept.items()}
        repeat = any(p1_paths[p] & p1_paths[p + 1] for p in range(window_end - CAP + 1, window_end))
        if not (high_last or repeat):
            break
        continued += 1
        sess_continue.add(s["session"])
        runs_kept_by_judge += sum(len(byp.get(p, [])) for p in range(window_end + 1, window_end + CAP + 1))
        window_end += CAP
P(table(["metric", "value"], [
    ["slice judgements", judged],
    ["continue verdicts", continued],
    ["sessions with at least one continue", f"{len(sess_continue)} of {n_sess}"],
    ["valid slice runs at pass > 3", runs_after_cap],
    ["of those, inside a continued window", f"{runs_kept_by_judge} ({pct(runs_kept_by_judge, runs_after_cap).strip()})"],
]))

text = "\n".join(out)
print(text)
open("/tmp/msr_quant.txt", "w").write(text + "\n")
