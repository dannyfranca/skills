import json,glob,os,random,subprocess,collections
from cutoff import in_cutoff
random.seed(7)
rows=[]
for st in glob.glob('/home/danny/.worktrees/*trading-sandbox*/.review/*/_state.json'):
    sess=os.path.dirname(st); wt=sess.split('/.review/')[0]
    try: s=json.load(open(st))
    except: continue
    if not in_cutoff(s): continue
    for sname,sl in s.get('slices',{}).items():
        runs=sorted(sl.get('runs',[]),key=lambda r:r.get('pass',0))
        for i,r in enumerate(runs):
            fa=r.get('findings_archive')
            if not fa or not os.path.exists(fa): continue
            try: h=json.load(open(fa))
            except: continue
            prev=runs[i-1] if i>0 else None
            for f in h.get('findings',[]):
                res=f.get('resolution') or {}
                rows.append(dict(sess=sess,wt=wt,sid=os.path.basename(sess),slice=sname,pass_=r['pass'],run=r['id'],fid=f.get('id'),sev=f.get('severity'),status=f.get('status'),reskind=res.get('kind'),reason=res.get('text') or res.get('finding_id') or '',title=f.get('title'),path=(f.get('location') or {}).get('path'),loc=f.get('location'),start=r.get('started_at'),end=r.get('ended_at'),prev_end=prev.get('ended_at') if prev else None,prev_start=prev.get('started_at') if prev else None,content=f.get('content')))
json.dump(rows,open('/tmp/msr_rows.json','w'))
def spread(pool,n,used):
    pool=pool[:]; random.shuffle(pool); out=[]
    for r in pool:
        if len(out)>=n: break
        if r['sid'] not in used: out.append(r); used.add(r['sid'])
    for r in pool:
        if len(out)>=n: break
        if r not in out: out.append(r)
    return out
used=set()
late=spread([r for r in rows if r['status']!='ignored' and r['pass_']>=3],30,used)
p2=spread([r for r in rows if r['status']!='ignored' and r['pass_']==2],20,used)
ig=spread([r for r in rows if r['status']=='ignored' and r['pass_']>=2],25,set())
print('sessions covered non-ignored:',len({r['sid'] for r in late+p2}))
json.dump(dict(late=late,p2=p2,ig=ig),open('/tmp/msr_sample.json','w'))
def dossier(r,idx):
    L=[f"===== #{idx} sid={r['sid']} slice={r['slice']} pass={r['pass_']} sev={r['sev']} fid={r['fid']} status={r['status']}"]
    L.append(f"WT={os.path.basename(r['wt'])}")
    L.append(f"TITLE: {r['title']}"); L.append(f"LOC: {r['loc']}"); L.append(f"CONTENT: {r['content']}")
    if r['status']=='ignored': L.append(f"IGNORE({r['reskind']}): {r['reason']}")
    same=[x for x in rows if x['sess']==r['sess'] and x['slice']==r['slice'] and x['pass_']<r['pass_']]
    L.append("EARLIER SAME-SLICE:"); 
    for x in sorted(same,key=lambda x:x['pass_']): L.append(f"  p{x['pass_']} [{x['sev']}/{x['status']}{'/'+x['reason'][:60] if x['status']=='ignored' else ''}] {x['title']} @{x['path']}")
    oth=[x for x in rows if x['sess']==r['sess'] and x['slice']!=r['slice'] and x['pass_']<=r['pass_']]
    L.append("OTHER SLICES (<= this pass):")
    for x in sorted(oth,key=lambda x:x['pass_']): L.append(f"  p{x['pass_']} {x['slice']} [{x['sev']}/{x['status']}] {x['title']} @{x['path']}")
    if r['prev_end']:
        try:
            out=subprocess.run(['git','-C',r['wt'],'log','--since',r['prev_end'],'--until',r['start'],'--format=%h %aI %s','--stat'],capture_output=True,text=True,timeout=30)
            L.append(f"GIT LOG {r['prev_end']} .. {r['start']}:\n"+(out.stdout.strip() or '(no commits)')+(('\nERR:'+out.stderr.strip()) if out.stderr.strip() else ''))
        except Exception as e: L.append('GIT ERR '+str(e))
    return '\n'.join(L)
with open('/tmp/msr_dossiers_late.txt','w') as fh:
    for i,r in enumerate(late,1): fh.write(dossier(r,i)+'\n\n')
with open('/tmp/msr_dossiers_p2.txt','w') as fh:
    for i,r in enumerate(p2,31): fh.write(dossier(r,i)+'\n\n')
with open('/tmp/msr_dossiers_ig.txt','w') as fh:
    for i,r in enumerate(ig,1): fh.write(dossier(r,i)+'\n\n')
