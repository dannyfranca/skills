import json,glob,os,collections
from cutoff import in_cutoff
rows=[]
res_kinds=collections.Counter(); res_keys=collections.Counter()
for st in glob.glob('/home/danny/.worktrees/*trading-sandbox*/.review/*/_state.json'):
    sess=os.path.dirname(st); wt=sess.split('/.review/')[0]
    try: s=json.load(open(st))
    except Exception as e: continue
    if not in_cutoff(s): continue
    for sname,sl in s.get('slices',{}).items():
        runs=sorted(sl.get('runs',[]),key=lambda r:r.get('pass',0))
        for r in runs:
            fa=r.get('findings_archive')
            if not fa or not os.path.exists(fa): continue
            try: h=json.load(open(fa))
            except: continue
            for f in h.get('findings',[]):
                res=f.get('resolution') or {}
                res_kinds[(f.get('status'),res.get('kind'))]+=1
                for k in res: res_keys[k]+=1
                rows.append(dict(sess=sess,sid=os.path.basename(sess),wt=os.path.basename(wt),slice=sname,pass_=r['pass'],run=r['id'],fid=f.get('id'),sev=f.get('severity'),status=f.get('status'),reskind=res.get('kind'),reason=res.get('reason') or res.get('note') or '',title=f.get('title'),path=(f.get('location') or {}).get('path'),start=r.get('started_at'),end=r.get('ended_at'),content=f.get('content')))
json.dump(rows,open('/tmp/msr_rows.json','w'))
print(len(rows),'findings;',len({r['sess'] for r in rows}),'sessions')
print(res_kinds)
print(res_keys)
c=collections.Counter((r['pass_']>=3, r['status']) for r in rows); print(c)
print(collections.Counter(r['pass_'] for r in rows))
