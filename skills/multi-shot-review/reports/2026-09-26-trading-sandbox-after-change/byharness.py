import json,glob,os,re,statistics as st
from collections import Counter,defaultdict
src=open('compare.py').read()
exec(src.split("sessions = sorted(")[0])
exec("def waves_of"+src.split("def waves_of")[1].split("def stats")[0])
raw={f:json.load(open(f)) for f in glob.glob(GLOB)}
def harness(f):
    hs=Counter(r.get("harness") for sl in raw[f]["slices"].values() for r in sl["runs"])
    return hs.most_common(1)[0][0] if hs else None
sessions=[]
for f in raw:
    s=load(f); s["harness"]=harness(f); sessions.append(s)
sessions.sort(key=lambda s:s["created"])
new=[s for s in sessions if s["new"]]
first_new=min(s["created"] for s in new)
old=[s for s in sessions if not s["new"] and s["created"]<first_new and any(sl["runs"] for sl in s["slices"])]
def m(pop,label):
    slices=[sl for s in pop for sl in s["slices"] if sl["runs"]]
    allruns=[r for sl in slices for r in sl["runs"]]
    valid=[r for r in allruns if not r["failed"]]
    F=[f for r in valid for f in r["findings"]]
    K=[f for f in F if f["kept"]]; K12=[f for f in K if f["sev"] in("P0","P1","P2")]
    rej=[f for f in F if f["rejected"]]
    p1=[r for r in valid if r["pass"]==1]
    hp=[max(r["pass"] for r in sl["runs"]) for sl in slices]
    idle=[];walls=[];busy=[];wv=[]
    for s in pop:
        rs=[r for sl in s["slices"] for r in sl["runs"]]
        ends=[r["end"] for r in rs]+[c["ended_at"] for c in s["classifications"] if c.get("ended_at")]
        w=mins(s["created"],max(ends)); walls.append(w)
        iv=[(ts(r["start"]),ts(r["end"])) for r in rs]; b=union_len(iv); busy.append(b)
        civ=[(ts(c["started_at"]),ts(c["ended_at"])) for c in s["classifications"] if c.get("ended_at")]
        idle.append(max(w-union_len(iv+civ),0)); wv.append(len(waves_of(s)))
    print(f"{label:<28} n={len(pop):<3} runs/s={len(allruns)/len(pop):5.1f} fail%={pct(sum(r['failed'] for r in allruns),len(allruns)):>6} slices p>=4={pct(sum(h>=4 for h in hp),len(hp)):>6} waves/s={st.mean(wv):.2f} | p1 find/run={st.mean(len(r['findings']) for r in p1):.2f} kept@p1={pct(sum(f['pass']==1 for f in K),len(K)):>6} kept/s={len(K)/len(pop):5.2f} keptP12/s={len(K12)/len(pop):5.2f} keptP12@p>=3={sum(f['pass']>=3 for f in K12):<3} rej%={pct(len(rej),len(rej)+len(K)):>6} | run min={st.mean(r['minutes'] for r in valid):.1f} busy/s={st.mean(busy):5.1f} idle/s={st.mean(idle):5.1f} idle%={pct(sum(idle),sum(walls)):>6} wall med={q(walls,.5):5.1f}")
print("## split by slice harness")
m([s for s in new if s["harness"]=="codex"],"after codex")
m(old[-19:],"before last19 (all codex? see)")
m([s for s in old[-19:] if s["harness"]=="codex"],"before last19 codex")
m([s for s in old if s["harness"]=="codex"][-9:],"before last9 codex")
m([s for s in old if s["harness"]=="codex"],"before all codex")
m([s for s in new if s["harness"]=="claude-code"],"after claude-code")
m([s for s in old if s["harness"]=="claude-code"],"before claude-code")
print("\nharness mix old:",Counter(s["harness"] for s in old),"new:",Counter(s["harness"] for s in new))
