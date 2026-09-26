import json, subprocess, sys
from collections import defaultdict
from p1_extract import load_findings

sessions, findings = load_findings()
targets = [
 ("8e2478f8","idiomatic-rust",4),("8d5c2621","ecb-reconstruction-behavior",5),("6b6cec36","standards",4),
 ("e8148759","code-quality",4),("8095fe84","direct-tick-runtime-correctness",4),("8095fe84","direct-tick-runtime-correctness",5),
 ("8095fe84","direct-tick-runtime-correctness",6),("ba461d48","idiomatic-rust",5),("ba461d48","specs-fidelity",4),
 ("c5deba72","runtime-correctness",5),("c5deba72","runtime-correctness",6),
]
by_sess = defaultdict(list)
for f in findings: by_sess[f["short"]].append(f)
done=set()
for short, sl, p in targets:
    fs = [f for f in by_sess[short] if f["slice"]==sl and f["pass"]==p and f["severity"]=="P1"]
    for f in fs:
        wt=f["worktree"]
        # previous pass run in same slice
        prev=[g for g in by_sess[short] if g["slice"]==sl and g["pass"]==p-1]
        prev_end = max((g["run_ended"] for g in prev), default=None)
        # all runs in session for this slice: timeline
        runs=sorted({(g["pass"],g["run_started"],g["run_ended"]) for g in by_sess[short] if g["slice"]==sl})
        print(f"\n##### {short} {sl} p{p}: {f['title']}\npath={f['path']}:{f['line']}")
        print("slice run timeline:", [(a,b[11:16],c[11:16]) for a,b,c in runs])
        same_path=[(g["pass"],g["slice"],g["severity"],g["status"],g["title"]) for g in by_sess[short] if g["path"] and f["path"] and g["path"].split('/')[-1]==f["path"].split('/')[-1] and (g["pass"]<p or g["slice"]!=sl) and g is not f]
        print("other findings same file:"); [print("   ",x) for x in same_path]
        # git log between session start and this run start
        sess_start=min(g["run_started"] for g in by_sess[short])
        if (short,sl,p) in done: continue
        done.add((short,sl,p))
        cmd=["git","-C",wt,"log","--since",sess_start,"--until",f["run_started"],"--format=%h %ci %s","--stat","--stat-width=120"]
        out=subprocess.run(cmd,capture_output=True,text=True).stdout
        rel=f["path"].replace(wt+"/","")
        print(f"git log {sess_start[11:16]}..{f['run_started'][11:16]} (this run start):")
        lines=out.splitlines()
        for l in lines:
            if l and (l[0].isalnum() and len(l.split())>3 and l.split()[1][:4]=="2026") : print("  C ", l)
            elif rel.split('/')[-1] in l or " files changed" in l: print("     ", l.strip())
