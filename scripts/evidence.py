#!/usr/bin/env python3
from __future__ import annotations
import argparse, datetime as dt, hashlib, json
from pathlib import Path
from harnesslib import run_dir, safe_task_id

EVIDENCE_TYPES={'DETERMINISTIC','INFERRED','INSUFFICIENT'}
STATUSES={'PASS','FAIL','INFO','BLOCKED'}
CATEGORIES={'acceptance','checks','review','verification','security_review','human_approval','research','routing','context','risk','rollback','attestation'}
EXPECTED_ACTORS={'review':{'reviewer'},'verification':{'verifier'},'security_review':{'security-reviewer'},'human_approval':{'human'},'attestation':{'ci-attestor','human-attestor'}}

def ledger_path(task): return run_dir(task)/'evidence.jsonl'
def init(task): safe_task_id(task); d=run_dir(task); d.mkdir(parents=True,exist_ok=True); ledger_path(task).touch(exist_ok=True); return d
def _hash_record(rec): return hashlib.sha256(json.dumps(rec,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def read(task,validate=True):
    p=ledger_path(safe_task_id(task)); rows=[]
    if not p.exists(): return rows
    for n,line in enumerate(p.read_text(encoding='utf-8').splitlines(),1):
        if not line.strip(): continue
        try: rows.append(json.loads(line))
        except Exception as e: raise ValueError(f'invalid evidence JSON line {n}: {e}')
    if validate:
        ok,reason=validate_chain_rows(rows)
        if not ok: raise ValueError(reason)
    return rows

def validate_chain_rows(rows):
    prev='GENESIS'
    for i,r in enumerate(rows,1):
        if r.get('prev_hash')!=prev: return False,f'broken prev_hash at entry {i}'
        stored=r.get('record_hash'); base=dict(r); base.pop('record_hash',None)
        calc=_hash_record(base)
        if stored!=calc: return False,f'record hash mismatch at entry {i}'
        prev=stored
    return True,'ok'
def head_hash(task):
    rows=read(task); return rows[-1]['record_hash'] if rows else 'GENESIS'

def append(task,category,etype,claim,status,actor,command=None,exit_code=None,artifact=None,notes=None):
    init(task)
    if category not in CATEGORIES: raise SystemExit(f'invalid evidence category: {category}')
    if etype not in EVIDENCE_TYPES: raise SystemExit('invalid evidence type')
    if status not in STATUSES: raise SystemExit('invalid status')
    expected=EXPECTED_ACTORS.get(category)
    if expected and actor not in expected: raise SystemExit(f'{category} evidence actor must be one of {sorted(expected)}')
    rows=read(task); prev=rows[-1]['record_hash'] if rows else 'GENESIS'
    rec={'timestamp':dt.datetime.now(dt.timezone.utc).isoformat(),'task_id':safe_task_id(task),'category':category,'evidence_type':etype,'claim':claim,'status':status,'actor':actor,'prev_hash':prev}
    if command is not None: rec['command']=command
    if exit_code is not None: rec['exit_code']=exit_code
    if artifact is not None: rec['artifact']=artifact
    if notes is not None: rec['notes']=notes
    rec['record_hash']=_hash_record(rec)
    with ledger_path(task).open('a',encoding='utf-8') as f: f.write(json.dumps(rec,ensure_ascii=False,sort_keys=True)+'\n')
    return rec

def validate(task):
    try:
        rows=read(task,validate=False); ok,reason=validate_chain_rows(rows); return {'task_id':task,'valid':ok,'entries':len(rows),'reason':reason,'head_hash':rows[-1]['record_hash'] if rows else 'GENESIS'}
    except Exception as e: return {'task_id':task,'valid':False,'entries':0,'reason':str(e)}
def summary(task):
    rows=read(task); latest={}
    for r in rows: latest[r['category']]=r
    return {'task_id':task,'entries':len(rows),'valid_chain':True,'head_hash':rows[-1]['record_hash'] if rows else 'GENESIS','latest_by_category':latest}

def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('init'); p.add_argument('task')
    p=sub.add_parser('add'); p.add_argument('task'); p.add_argument('--category',required=True,choices=sorted(CATEGORIES)); p.add_argument('--type',dest='etype',required=True,choices=sorted(EVIDENCE_TYPES)); p.add_argument('--claim',required=True); p.add_argument('--status',required=True,choices=sorted(STATUSES)); p.add_argument('--actor',required=True); p.add_argument('--command'); p.add_argument('--exit-code',type=int); p.add_argument('--artifact'); p.add_argument('--notes')
    p=sub.add_parser('summary'); p.add_argument('task'); p=sub.add_parser('validate'); p.add_argument('task')
    a=ap.parse_args()
    if a.cmd=='init': print(init(a.task))
    elif a.cmd=='add': print(json.dumps(append(a.task,a.category,a.etype,a.claim,a.status,a.actor,a.command,a.exit_code,a.artifact,a.notes),indent=2))
    elif a.cmd=='validate':
        r=validate(a.task); print(json.dumps(r,indent=2)); raise SystemExit(0 if r['valid'] else 2)
    else: print(json.dumps(summary(a.task),indent=2))
if __name__=='__main__': main()
