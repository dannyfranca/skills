#!/usr/bin/env python3
import glob, json, os, random, re
from collections import Counter
from cutoff import in_cutoff
GLOB = "/home/danny/.worktrees/*trading-sandbox*/.review/*/_state.json"
def is_test_path(p):
    p = p or ""
    return "/tests/" in p or "_test" in p or p.endswith("tests.rs") or p.startswith("tests/") or "/test_" in p or "/testing/" in p


def load_findings():
    sessions = []
    for f in sorted(glob.glob(GLOB)):
        s = json.load(open(f))
        if not in_cutoff(s):
            continue
        s["_path"] = f; sessions.append(s)
    findings = []
    for s in sessions:
        sid = s["session"]["review_dir"]
        wt = s["_path"].split("/.review/")[0]
        for name, sl in s["slices"].items():
            for r in sl["runs"]:
                if r["status"] == "running" or not r.get("ended_at"):
                    continue
                fl = []
                if r.get("findings_archive") and os.path.exists(r["findings_archive"]):
                    fl = json.load(open(r["findings_archive"]))["findings"]
                elif r.get("findings"):
                    fl = r["findings"]
                for i, fi in enumerate(fl):
                    res = fi.get("resolution") or {}
                    loc = fi.get("location") or {}
                    findings.append({
                        "id": f"{sid}:{name}:p{r['pass']}:{i}",
                        "session": sid, "short": sid.split("-")[-1], "worktree": wt, "slice": name, "pass": r["pass"],
                        "severity": fi.get("severity"), "status": fi.get("status"), "kind": res.get("kind"),
                        "res_text": res.get("text"), "title": fi.get("title",""), "content": fi.get("content",""),
                        "path": loc.get("path"), "line": loc.get("start_line"), "test": is_test_path(loc.get("path")),
                        "run_started": r["started_at"], "run_ended": r["ended_at"],
                    })
    return sessions, findings


if __name__ == "__main__":
    sessions, findings = load_findings()
    n_sess = len({f["session"] for f in findings})
    p1 = [f for f in findings if f["severity"] == "P1" and f["status"] != "ignored"]
    late = [f for f in p1 if f["pass"] >= 3]
    early = [f for f in p1 if f["pass"] == 1]
    random.seed(42)
    sample = random.sample(early, 25)
    print("sessions with findings:", n_sess, "total sessions", len(sessions))
    print("P1 non-ignored:", len(p1), "late(>=3):", len(late), "pass1:", len(early))
    print("late status:", Counter(f["status"] for f in late), "kind:", Counter(f["kind"] for f in late))
    print("late by pass:", sorted(Counter(f["pass"] for f in late).items()))
    json.dump({"late": late, "sample": sample, "n_sessions": len(sessions)}, open("/tmp/msr_p1_data.json","w"), indent=1)
    def dump(fs, path):
        with open(path,"w") as o:
            for f in fs:
                o.write(f"=== {f['id']}\nsess={f['short']} slice={f['slice']} pass={f['pass']} status={f['status']} kind={f['kind']} test={f['test']}\npath={f['path']}:{f['line']}\nwt={f['worktree']}\nTITLE: {f['title']}\nRES: {f['res_text']}\n{f['content']}\n\n")
    dump(late, "/tmp/msr_p1_late.txt"); dump(sample, "/tmp/msr_p1_sample.txt")
