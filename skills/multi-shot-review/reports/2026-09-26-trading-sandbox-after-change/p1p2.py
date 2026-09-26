import json,glob,os,re,statistics as st
from collections import Counter,defaultdict
src=open('compare.py').read()
exec(src.split("sessions = sorted(")[0])
exec("def waves_of"+src.split("def waves_of")[1].split("def stats")[0])
sessions = sorted((load(f) for f in glob.glob(GLOB)), key=lambda s: s["created"])
new=[s for s in sessions if s["new"]]
first_new=min(s["created"] for s in new)
old=[s for s in sessions if not s["new"] and s["created"]<first_new and any(sl["runs"] for sl in s["slices"])]
old19=old[-19:]
def m(pop,label):
    slices=[sl for s in pop for sl in s["slices"] if sl["runs"]]
    valid=[r for sl in slices for r in sl["runs"] if not r["failed"]]
    F=[f for r in valid for f in r["findings"]]
    K=[f for f in F if f["kept"] and f["sev"] in("P0","P1","P2")]
    p1=[r for r in valid if r["pass"]==1]
    p1k=[sum(1 for f in r["findings"] if f["sev"] in("P0","P1","P2")) for r in p1]
    idle=[];walls=[];kept=[]
    for s in pop:
        if not s["completed"]: continue
        rs=[r for sl in s["slices"] for r in sl["runs"]]
        ends=[r["end"] for r in rs]+[c["ended_at"] for c in s["classifications"] if c.get("ended_at")]
        if not ends: continue
        w=mins(s["created"],max(ends)); walls.append(w)
        iv=[(ts(c["started_at"]),ts(c["ended_at"])) for c in s["classifications"] if c.get("ended_at")]+[(ts(r["start"]),ts(r["end"])) for r in rs]
        idle.append(max(w-union_len(iv),0)); kept.append(sum(1 for sl in s["slices"] for r in sl["runs"] for f in r["findings"] if f["kept"] and f["sev"] in ("P0","P1","P2")))
    rej=[f for f in F if f["rejected"] and f["sev"] in ("P0","P1","P2")]
    print(f"{label:<40} n={len(pop):<3} keptP12/sess={len(K)/len(pop):.2f} keptP12@p1={pct(sum(f['pass']==1 for f in K),len(K))} p1 P12/run={st.mean(p1k):.2f} p1 %runs w/P12={pct(sum(1 for x in p1k if x),len(p1k))} keptP12@p>=3={sum(f['pass']>=3 for f in K)} rejP12={len(rej)} rej%={pct(len(rej),len(rej)+len(K))} idle/keptP12={sum(idle)/max(1,sum(kept)):.2f}min idle/sess={st.mean(idle):.1f} wall med={q(walls,.5):.1f}")
print("## P1+P2 only")
m(new,"after")
m(old19,"before last19")
m(old,"before all")
noP3=[s for s in old19 if not any(f["sev"]=="P3" for sl in s["slices"] for r in sl["runs"] for f in r["findings"])]
m(noP3,"before last19 w/o P3-era sessions")
cut="2026-09-26T02:00:00Z"
m([s for s in new if s["created"]<cut],"after, before REVIEW.md edit 02:00Z")
m([s for s in new if s["created"]>=cut],"after, after REVIEW.md edit 02:00Z")
print("\n## sev mix per session (after), created / P1 P2 P3 kept / total runs / wall")
for s in new:
    K=[f for sl in s["slices"] for r in sl["runs"] for f in r["findings"] if f["kept"]]
    c=Counter(f["sev"] for f in K); print(s["created"][5:16], s["wt"][-40:], dict(c), "runs",sum(len(sl["runs"]) for sl in s["slices"]))
