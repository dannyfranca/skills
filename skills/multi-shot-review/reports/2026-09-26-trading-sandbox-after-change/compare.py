#!/usr/bin/env python3
"""Compare multi-shot review sessions that ran on the pass-cap/judge/shots code against the
same number of sessions that ran just before it, and against the full 2026-09-25 baseline."""
import glob, json, os, re, statistics as st
from collections import Counter, defaultdict
from datetime import datetime

GLOB = "/home/danny/.worktrees/*trading-sandbox*/.review/*/_state.json"
FAILED = {"failed", "timeout"}
WAVE_GAP_S = 60
STOP = set("""the a an and or of to in is are was be for on with that this it its as by not no from at
into but if then than so does do did has have had which when while only also already still all any
each same both can could would should will may must here there these those about after before
because via per over under between out up down more less new one two""".split())

def ts(s): return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
def mins(a, b): return (ts(b) - ts(a)).total_seconds() / 60
def pct(n, d): return f"{100*n/d:.1f}%" if d else "n/a"
def q(xs, p):
    if not xs: return float("nan")
    xs = sorted(xs); k = (len(xs)-1)*p; lo, hi = int(k), min(int(k)+1, len(xs)-1)
    return xs[lo] + (xs[hi]-xs[lo])*(k-lo)
def f1(x): return f"{x:.1f}" if x == x else "n/a"
def f2(x): return f"{x:.2f}" if x == x else "n/a"
def lens(name):
    n = name.lower()
    if any(k in n for k in ("idiomatic", "rust", "design")): return "rust"
    if any(k in n for k in ("test", "coverage")): return "tests"
    if any(k in n for k in ("spec", "fidelity", "prd")): return "specs"
    return "other"
def tokens(t): return {x for x in re.findall(r"[a-z0-9_]+", (t or "").lower()) if x not in STOP and len(x) > 1}
def table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "---|"*len(headers)]
    out += ["| " + " | ".join(str(v) for v in r) + " |" for r in rows]
    return "\n".join(out)

def load(path):
    s = json.load(open(path)); sid = s["session"]["review_dir"]
    new = any("shots" in sl for sl in s["slices"].values())
    S = {"sid": sid, "created": s["session"]["created_at"], "new": new, "completed": s.get("completed"),
         "classifications": s["classifications"], "slices": [], "history": s.get("history", []),
         "wt": os.path.basename(s["session"]["root"])}
    for name, sl in s["slices"].items():
        srec = {"name": name, "lens": lens(name), "removed": sl.get("removed"), "runs": [],
                "shots": sl.get("shots"), "judgements": sl.get("judgements") or [], "complete": sl.get("complete"),
                "pass_window": sl.get("pass_window")}
        for r in sl["runs"]:
            if r["status"] == "running" or not r.get("ended_at"): continue
            rec = {"pass": r["pass"], "shot": r.get("shot"), "status": r["status"], "start": r["started_at"], "end": r["ended_at"],
                   "minutes": mins(r["started_at"], r["ended_at"]), "failed": r["status"] in FAILED, "findings": [],
                   "count": r.get("finding_count") or 0}
            fl = []
            if r.get("findings_archive") and os.path.exists(r["findings_archive"]):
                fl = json.load(open(r["findings_archive"]))["findings"]
            elif r.get("findings"): fl = r["findings"]
            for fi in fl:
                res = fi.get("resolution") or {}
                fr = {"pass": r["pass"], "shot": r.get("shot"), "sev": fi.get("severity", "?"), "status": fi.get("status", "open"),
                      "kind": res.get("kind"), "auto": bool(res.get("auto")), "title": fi.get("title", ""),
                      "path": (fi.get("location") or {}).get("path"), "slice": name, "lens": srec["lens"], "sid": sid}
                fr["rejected"] = fr["status"] == "ignored" and fr["kind"] != "duplicate"
                fr["dup"] = fr["kind"] == "duplicate"
                fr["kept"] = not fr["rejected"] and not fr["dup"]
                rec["findings"].append(fr)
            srec["runs"].append(rec)
        S["slices"].append(srec)
    return S

sessions = sorted((load(f) for f in glob.glob(GLOB)), key=lambda s: s["created"])
new = [s for s in sessions if s["new"]]
old = [s for s in sessions if not s["new"] and any(sl["runs"] for sl in s["slices"])]
first_new = min(s["created"] for s in new)
old = [s for s in old if s["created"] < first_new]
N = len(new)
pops = [("after (new code)", new), (f"before, last {N}", old[-N:]), ("before, all", old)]

def waves_of(sess):
    runs = [r for sl in sess["slices"] for r in sl["runs"]]
    bypass = defaultdict(list)
    for r in runs: bypass[r["pass"]].append(r)
    waves = []
    for p, prs in bypass.items():
        prs.sort(key=lambda r: r["start"]); cur = [prs[0]]
        for r in prs[1:]:
            if (ts(r["start"]) - ts(cur[-1]["start"])).total_seconds() <= WAVE_GAP_S: cur.append(r)
            else: waves.append(cur); cur = [r]
        waves.append(cur)
    return sorted(waves, key=lambda w: w[0]["start"])

def union_len(iv):
    iv = sorted(iv); tot = 0; cur = None
    for a, b in iv:
        if cur is None or a > cur[1]:
            if cur: tot += (cur[1]-cur[0]).total_seconds()/60
            cur = [a, b]
        else: cur[1] = max(cur[1], b)
    if cur: tot += (cur[1]-cur[0]).total_seconds()/60
    return tot

def stats(pop):
    slices = [sl for s in pop for sl in s["slices"] if sl["runs"]]
    runs = [r for sl in slices for r in sl["runs"]]
    valid = [r for r in runs if not r["failed"]]
    F = [f for r in valid for f in r["findings"]]
    d = {}
    d["sessions"] = len(pop); d["completed"] = sum(1 for s in pop if s["completed"])
    d["slices"] = len(slices); d["runs"] = len(runs); d["failed runs"] = len(runs)-len(valid)
    d["slices/session"] = f2(len(slices)/len(pop))
    d["runs/session"] = f2(len(runs)/len(pop))
    d["classifications/session"] = f2(sum(len(s["classifications"]) for s in pop)/len(pop))
    d["sessions with 2+ classifications"] = sum(1 for s in pop if len(s["classifications"]) >= 2)
    hp = [max(r["pass"] for r in sl["runs"]) for sl in slices]
    d["mean highest pass/slice"] = f2(st.mean(hp))
    d["slices reaching pass>=4"] = f"{sum(1 for p in hp if p>=4)} ({pct(sum(1 for p in hp if p>=4), len(hp))})"
    d["max pass seen"] = max(hp)
    tp = [max(r["pass"] for r in sl["runs"]) for sl in slices if sl["lens"]=="tests"]
    d["tests lens mean highest pass"] = f2(st.mean(tp)) if tp else "n/a"
    d["findings / kept / rejected / dup(auto)"] = f"{len(F)} / {sum(f['kept'] for f in F)} / {sum(f['rejected'] for f in F)} / {sum(f['dup'] for f in F)}({sum(f['dup'] and f['auto'] for f in F)})"
    d["kept findings/session"] = f2(sum(f['kept'] for f in F)/len(pop))
    d["kept P1/session"] = f2(sum(f['kept'] and f['sev']=='P1' for f in F)/len(pop))
    d["rejected % (all)"] = pct(sum(f['rejected'] for f in F), len(F))
    p1 = [r for r in valid if r["pass"]==1]
    d["pass-1 runs: mean findings/run"] = f2(st.mean(r["count"] for r in p1))
    d["pass-1 runs: % with >=1"] = pct(sum(1 for r in p1 if r["count"]>0), len(p1))
    d["kept findings at pass 1 (% of kept)"] = pct(sum(1 for f in F if f["kept"] and f["pass"]==1), sum(f['kept'] for f in F))
    d["kept findings at pass>=4"] = sum(1 for f in F if f["kept"] and f["pass"]>=4)
    d["kept P1 at pass>=3"] = sum(1 for f in F if f["kept"] and f["pass"]>=3 and f["sev"]=="P1")
    # re-raise: consecutive-pass title overlap >=0.6 within slice
    rr = tot = 0
    for sl in slices:
        byp = defaultdict(list)
        for r in sl["runs"]:
            for f in r["findings"]: byp[r["pass"]].append(f)
        for p, fs in byp.items():
            if p < 2: continue
            prev = byp.get(p-1, [])
            for f in fs:
                tot += 1; t = tokens(f["title"])
                if t and any(len(t & tokens(g["title"]))/max(1,len(t)) >= 0.6 and g["path"]==f["path"] for g in prev): rr += 1
    d["re-raise rate (pass>=2, consec. overlap>=0.6)"] = f"{rr}/{tot} ({pct(rr,tot)})"
    d["mean valid run minutes"] = f1(st.mean(r["minutes"] for r in valid))
    # time
    comp = [s for s in pop if s["completed"]]
    walls, cls, busy, idle = [], [], [], []
    for s in comp:
        rs = [r for sl in s["slices"] for r in sl["runs"]]
        ends = [r["end"] for r in rs] + [c["ended_at"] for c in s["classifications"] if c.get("ended_at")]
        if not ends: continue
        w = mins(s["created"], max(ends)); walls.append(w)
        civ = [(ts(c["started_at"]), ts(c["ended_at"])) for c in s["classifications"] if c.get("ended_at")]
        riv = [(ts(r["start"]), ts(r["end"])) for r in rs]
        c = union_len(civ); b = union_len(riv); cls.append(c); busy.append(b); idle.append(max(w - union_len(civ+riv), 0))
    d["completed sessions with wall"] = len(walls)
    d["wall min: median / p90 / mean / total"] = f"{f1(q(walls,.5))} / {f1(q(walls,.9))} / {f1(st.mean(walls))} / {f1(sum(walls))}"
    d["classifier min/session (mean)"] = f1(st.mean(cls))
    d["runs busy min/session (mean)"] = f1(st.mean(busy))
    d["idle min/session (mean)"] = f1(st.mean(idle))
    d["idle share of wall"] = pct(sum(idle), sum(walls))
    d["idle share median/session"] = pct(st.median(i/w for i, w in zip(idle, walls)), 1)
    ws = [w for s in pop for w in waves_of(s)]
    d["waves/session"] = f2(len(ws)/len(pop))
    d["waves at pass>=4 (% of waves)"] = f"{sum(1 for w in ws if w[0]['pass']>=4)} ({pct(sum(1 for w in ws if w[0]['pass']>=4), len(ws))})"
    d["mean wave size"] = f2(st.mean(len(w) for w in ws))
    d["mean wave wall min"] = f1(st.mean(max(r["minutes"] for r in w) for w in ws))
    # judge / shots
    J = [j for sl in slices for j in sl["judgements"]]
    d["judge verdicts (continue/stop)"] = f"{sum(1 for j in J if j.get('verdict')=='continue')}/{sum(1 for j in J if j.get('verdict')=='stop')}" if J else "n/a"
    sh = Counter(sl["shots"] for sl in slices if sl["shots"])
    d["configured shots per slice"] = dict(sh) if sh else "n/a"
    return d

keys = list(stats(new).keys())
cols = [(name, stats(p)) for name, p in pops]
print("# After vs before\n")
print(f"first new-code session created_at: {first_new}; new sessions: {N}; old sessions total: {len(old)}\n")
print(table(["metric"] + [c[0] for c in cols], [[k] + [c[1][k] for c in cols] for k in keys]))

# ---- by pass table for after vs before-last-N
for name, pop in pops[:2]:
    slices = [sl for s in pop for sl in s["slices"] if sl["runs"]]
    valid = [r for sl in slices for r in sl["runs"] if not r["failed"]]
    rows = []
    for p in range(1, 9):
        rs = [r for r in valid if r["pass"]==p]
        if not rs: continue
        fs = [f for r in rs for f in r["findings"]]
        sev = Counter(f["sev"] for f in fs if f["kept"])
        rows.append([p, len(rs), f2(st.mean(r["count"] for r in rs)), pct(sum(1 for r in rs if r["count"]>0), len(rs)), len(fs),
                     sum(f["kept"] for f in fs), f"{sev['P1']}/{sev['P2']}/{sev['P3']}", pct(sum(f["rejected"] for f in fs), len(fs)), sum(f["dup"] for f in fs)])
    print(f"\n## By pass: {name}\n")
    print(table(["pass", "runs", "mean#find", "%runs>=1", "findings", "kept", "kept P1/P2/P3", "%rejected", "dup"], rows))

# ---- per-session detail for the new population
print("\n## New sessions detail\n")
rows = []
for s in new:
    slices = [sl for sl in s["slices"] if sl["runs"]]
    runs = [r for sl in slices for r in sl["runs"]]
    F = [f for r in runs for f in r["findings"]]
    J = [j.get("verdict") for sl in slices for j in sl["judgements"]]
    ends = [r["end"] for r in runs] + [c["ended_at"] for c in s["classifications"] if c.get("ended_at")]
    w = f1(mins(s["created"], max(ends))) if ends else "n/a"
    hp = max((r["pass"] for r in runs), default=0)
    rows.append([s["wt"].replace("trading-sandbox-", ""), "yes" if s["completed"] else "no", len(slices), len(runs), hp,
                 sum(f["kept"] for f in F), sum(f["rejected"] for f in F), sum(f["dup"] and f["auto"] for f in F), sum(f["dup"] and not f["auto"] for f in F),
                 "/".join(J) or "-", w])
print(table(["worktree", "done", "slices", "runs", "max pass", "kept", "rej", "auto-dup", "man-dup", "judge", "wall min"], rows))

# ---- shots phase-down and auto-dup detail
print("\n## Shots per pass (new sessions): runs per (slice,pass)\n")
c = Counter()
for s in new:
    for sl in s["slices"]:
        byp = Counter(r["pass"] for r in sl["runs"])
        for p, n in byp.items(): c[(p, n)] += 1
print(table(["pass", "1 run", "2 runs", "3+ runs"], [[p, c[(p,1)], c[(p,2)], sum(v for (pp,n),v in c.items() if pp==p and n>=3)] for p in sorted({p for p,_ in c})]))

print("\n## Auto-duplicate pairs (new sessions)\n")
for s in new:
    for sl in s["slices"]:
        for r in sl["runs"]:
            for f in r["findings"]:
                if f["dup"] and f["auto"]:
                    sib = [g for rr in sl["runs"] if rr["pass"]==r["pass"] and rr is not r for g in rr["findings"] if g["path"]==f["path"]]
                    print(f"- {s['wt'].replace('trading-sandbox-','')} / {sl['name']} p{r['pass']} s{r['shot']} [{f['sev']}] {f['title']!r}")
                    for g in sib: print(f"    kept sibling s{g['shot']} [{g['sev']}] {g['title']!r} ({g['status']})")

print("\n## Judge verdicts (new sessions)\n")
for s in new:
    for sl in s["slices"]:
        for j in sl["judgements"]:
            print(f"- {s['wt'].replace('trading-sandbox-','')} / {sl['name']}: {json.dumps({k:v for k,v in j.items() if k!='prompt'})[:400]}")
