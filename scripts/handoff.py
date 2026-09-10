#!/usr/bin/env python
from __future__ import annotations
import argparse, json
from pathlib import Path
from harnesslib import ROOT, load_json, run_dir, safe_task_id, write_json_atomic

try:
    import jsonschema
except Exception:
    jsonschema=None

POLICY=ROOT/'harness/handoff-policy.json'
def validate(role,data):
    policy=json.loads(POLICY.read_text(encoding='utf-8'))
    if role not in policy['contracts']: raise ValueError(f'unknown handoff role: {role}')
    raw=json.dumps(data,ensure_ascii=False,separators=(',',':')).encode()
    if len(raw)>int(policy['max_bytes']): raise ValueError(f'handoff exceeds {policy["max_bytes"]} bytes')
    schema=json.loads((ROOT/policy['contracts'][role]).read_text(encoding='utf-8'))
    if jsonschema:
        jsonschema.validate(data,schema)
    else:
        for key in schema.get('required',[]):
            if key not in data: raise ValueError(f'missing required field: {key}')
        expected=((schema.get('properties') or {}).get('producer') or {}).get('const')
        if expected and data.get('producer')!=expected: raise ValueError(f'producer must be {expected}')
    safe_task_id(str(data.get('task_id','')))
    return True

def save(role,data,output=None):
    validate(role,data); task=data['task_id']; dest=Path(output) if output else run_dir(task)/'handoffs'/f'{role}.json'
    if not dest.is_absolute(): dest=ROOT/dest
    write_json_atomic(dest,data); return dest

def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('validate'); p.add_argument('role'); p.add_argument('file')
    p=sub.add_parser('save'); p.add_argument('role'); p.add_argument('file'); p.add_argument('--output')
    a=ap.parse_args(); data=json.loads(Path(a.file).read_text(encoding='utf-8'))
    try:
        if a.cmd=='validate': validate(a.role,data); print(json.dumps({'ok':True,'role':a.role,'task_id':data['task_id']},indent=2))
        else:
            dest=save(a.role,data,a.output); print(json.dumps({'ok':True,'output':str(dest)},indent=2))
    except Exception as e:
        print(json.dumps({'ok':False,'reason':str(e)},ensure_ascii=False,indent=2)); raise SystemExit(2)
if __name__=='__main__': main()
