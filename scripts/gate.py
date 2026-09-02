#!/usr/bin/env python3
from __future__ import annotations
import argparse, fnmatch, json, re, sys
from pathlib import Path
from harnesslib import ROOT, load_json, run_dir
from evidence import read as read_evidence

def command_decision(command, risk='R1'):
    p=load_json('harness/policies/risk-policy.json')
    for pat in p['blocked_always_patterns']:
        if re.search(pat,command): return {'allow':False,'reason':'blocked unsafe pipe-to-shell pattern','human_gate':False}
    for pat in p['human_gate_patterns']:
        if re.search(pat,command): return {'allow':False,'reason':'human approval required for destructive/production command','human_gate':True}
    return {'allow':True,'reason':'no blocking policy matched','human_gate':False}

def path_decision(path):
    p=load_json('harness/policies/risk-policy.json'); rel=Path(path)
    candidate=rel if rel.is_absolute() else ROOT/rel
    try:
        normalized=candidate.resolve().relative_to(ROOT.resolve()).as_posix()
    except Exception:
        return {'allow':False,'reason':'write path resolves outside the project workspace','human_gate':False}
    if rel.name == '.env.example': return {'allow':True,'reason':'documented non-secret environment template','human_gate':False}
    for protected in p.get('protected_paths',[]):
        if normalized == protected:
            return {'allow':False,'reason':'human approval required to modify harness policy/control-plane file','human_gate':True}
    for g in p['secret_path_patterns']:
        if fnmatch.fnmatch(rel.name,g) or fnmatch.fnmatch(normalized,g): return {'allow':False,'reason':'secret-sensitive path','human_gate':False}
    return {'allow':True,'reason':'path allowed','human_gate':False}

def finish_decision(task,risk):
    policy=load_json('harness/policies/risk-policy.json'); req=policy['finish_requirements'][risk]
    try:
        rows=read_evidence(task)
    except Exception as e:
        return {'allow':False,'required':req,'missing':[],'failing':['evidence_chain'],'reason':str(e)}
    latest={}
    for r in rows: latest[r['category']]=r
    missing=[]; failing=[]
    for cat in req:
        r=latest.get(cat)
        if not r or r.get('evidence_type')!='DETERMINISTIC': missing.append(cat); continue
        if r.get('status')!='PASS': failing.append(cat); continue
        if cat=='checks' and r.get('command') is not None and r.get('exit_code') != 0: failing.append(cat)
    return {'allow':not missing and not failing,'required':req,'missing':missing,'failing':failing}

def hook(event, payload):
    # Supports canonical payload and common Claude/Cursor shapes.
    tool=payload.get('tool_name') or payload.get('tool') or ''
    ti=payload.get('tool_input') or payload.get('input') or {}
    if event=='pre-shell':
        cmd=payload.get('command') or ti.get('command') or ''
        if not cmd: return {'allow':False,'reason':'missing shell command in hook payload','human_gate':False}
        return command_decision(cmd,payload.get('risk','R1'))
    if event=='pre-write':
        path=payload.get('file_path') or ti.get('file_path') or ti.get('path') or ''
        if not path: return {'allow':False,'reason':'missing write path in hook payload'}
        return path_decision(path)
    if event=='post-write': return {'allow':True,'reason':'post-write audit point'}
    if event=='pre-finish': return finish_decision(payload['task_id'],payload['risk'])
    return {'allow':False,'reason':'unknown hook event'}

def emit_provider(event, d, provider):
    allow=bool(d['allow']); reason=d.get('reason') or ('gate blocked' if not allow else 'allowed')
    if provider=='claude' and event in {'pre-shell','pre-write'}:
        # Do not auto-allow safe operations: Claude's `allow` bypasses its normal permission flow.
        # Emit a decision only when the harness needs to deny.
        if allow:
            print('{}'); return 0
        out={'hookSpecificOutput':{'hookEventName':'PreToolUse','permissionDecision':'deny','permissionDecisionReason':reason}}
        print(json.dumps(out)); return 0
    if provider=='gemini' and event in {'pre-shell','pre-write'}:
        # Gemini BeforeTool hooks preserve normal policy/confirmation on safe operations.
        # A deny decision blocks the tool without granting any extra permission.
        if allow:
            print('{}'); return 0
        print(json.dumps({'decision':'deny','reason':reason})); return 0
    if provider=='cursor' and event in {'pre-shell','pre-write'}:
        # Cursor expects a permission decision for beforeShellExecution/preToolUse.
        out={'permission':'allow' if allow else 'deny'}
        if not allow:
            out['user_message']=reason; out['agent_message']=reason
        print(json.dumps(out)); return 0 if allow else 2
    if provider=='cursor':
        # Audit-only events such as afterFileEdit have no blocking output contract.
        print('{}'); return 0
    print(json.dumps(d)); return 0 if allow else 2

def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('command'); p.add_argument('command'); p.add_argument('--risk',default='R1')
    p=sub.add_parser('path'); p.add_argument('path')
    p=sub.add_parser('finish'); p.add_argument('task'); p.add_argument('--risk',required=True,choices=['R0','R1','R2','R3'])
    p=sub.add_parser('hook'); p.add_argument('--event',required=True,choices=['pre-shell','pre-write','post-write','pre-finish']); p.add_argument('--provider',choices=['canonical','claude','cursor','gemini'],default='canonical')
    args=ap.parse_args()
    if args.cmd=='command': d=command_decision(args.command,args.risk); print(json.dumps(d,indent=2)); raise SystemExit(0 if d['allow'] else 2)
    if args.cmd=='path': d=path_decision(args.path); print(json.dumps(d,indent=2)); raise SystemExit(0 if d['allow'] else 2)
    if args.cmd=='finish': d=finish_decision(args.task,args.risk); print(json.dumps(d,indent=2)); raise SystemExit(0 if d['allow'] else 2)
    payload=json.load(sys.stdin) if not sys.stdin.isatty() else {}
    d=hook(args.event,payload); raise SystemExit(emit_provider(args.event,d,args.provider))
if __name__=='__main__': main()
