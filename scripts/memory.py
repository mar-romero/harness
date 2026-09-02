#!/usr/bin/env python3
from __future__ import annotations
import argparse, datetime as dt, json, re
from pathlib import Path
from harnesslib import ROOT, run_dir, safe_task_id, write_json_atomic
from gate import finish_decision
from evidence import summary

MEM=ROOT/'.harness/memory/approved'
def toks(s): return set(re.findall(r'[a-z0-9_-]{3,}',s.lower()))
def promote(task,title,summary_text,lessons,tags,approved_by):
    task=safe_task_id(task); route=json.loads((run_dir(task)/'route.json').read_text(encoding='utf-8')); gate=finish_decision(task,route['risk'])
    if not gate['allow']: raise ValueError('task cannot become memory before finish gate passes: '+json.dumps(gate))
    if not approved_by.strip(): raise ValueError('explicit approver required')
    ev=summary(task); doc={'schema_version':1,'task_id':task,'created_at':dt.datetime.now(dt.timezone.utc).isoformat(),'title':title,'summary':summary_text,'lessons':lessons,'tags':tags,'approved_by':approved_by,'risk':route['risk'],'evidence_head_hash':ev['head_hash'],'source':'verified-task-only'}
    dest=MEM/f'{task}.json'; write_json_atomic(dest,doc); return dest
def search(query,limit=5):
    q=toks(query); scored=[]
    for p in MEM.glob('*.json') if MEM.exists() else []:
        try: d=json.loads(p.read_text(encoding='utf-8'))
        except Exception: continue
        text=' '.join([d.get('title',''),d.get('summary',''),' '.join(d.get('lessons',[])),' '.join(d.get('tags',[]))]); score=len(q&toks(text))
        if score: scored.append((score,d))
    scored.sort(key=lambda x:(-x[0],x[1].get('task_id',''))); return [dict(d,relevance_score=s) for s,d in scored[:limit]]
def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('promote'); p.add_argument('task'); p.add_argument('--title',required=True); p.add_argument('--summary',required=True); p.add_argument('--lesson',action='append',default=[]); p.add_argument('--tag',action='append',default=[]); p.add_argument('--approved-by',required=True)
    p=sub.add_parser('search'); p.add_argument('query'); p.add_argument('--limit',type=int,default=5)
    a=ap.parse_args()
    try:
        if a.cmd=='promote': print(json.dumps({'ok':True,'output':str(promote(a.task,a.title,a.summary,a.lesson,a.tag,a.approved_by))},indent=2))
        else: print(json.dumps({'items':search(a.query,a.limit)},indent=2,ensure_ascii=False))
    except Exception as e: print(json.dumps({'ok':False,'reason':str(e)},indent=2,ensure_ascii=False)); raise SystemExit(2)
if __name__=='__main__': main()
