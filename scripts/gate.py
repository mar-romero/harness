#!/usr/bin/env python3
from __future__ import annotations
import argparse, fnmatch, json, re, sys
from pathlib import Path
from harnesslib import ROOT, load_json, run_dir, safe_task_id
from evidence import read as read_evidence
from handoff import validate as validate_handoff
from impact_analysis import finish_decision as impact_finish_decision
from tdd_evidence import finish_decision as tdd_finish_decision

INFERRED_ALLOWED_CATEGORIES={'exploration','planning','test_design','implementation','review','test_audit','verification','security_review'}
ROLE_EVIDENCE={'explorer':'exploration','planner':'planning','test-designer':'test_design','implementer':'implementation','test-auditor':'test_audit'}
HANDOFF_ROLES={'explorer','planner','test-designer','implementer','reviewer','test-auditor','verifier','security-reviewer'}

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

def _progress_decision(task, route):
    p=run_dir(task)/'progress.json'
    if not p.exists():
        return False, ['progress'], [], 'progress.json missing'
    try:
        progress=json.loads(p.read_text(encoding='utf-8'))
    except Exception as e:
        return False, [], ['progress'], str(e)
    if progress.get('risk') != route.get('risk'):
        return False, [], ['risk_state_mismatch'], f'route risk {route.get("risk")} != progress risk {progress.get("risk")}'
    if progress.get('current_step') != 'CLOSE':
        return False, [], ['progress_not_at_close'], f'current step is {progress.get("current_step")}'
    if progress.get('state') in {'WAITING','BLOCKED','STALLED'}:
        return False, [], ['progress_blocked'], f'progress state is {progress.get("state")}'
    required_predecessors=[x for x in progress.get('steps',[]) if x!='CLOSE']
    missing=[f'progress:{x}' for x in required_predecessors if x not in progress.get('completed',[])]
    return not missing, missing, [], None

def _handoff_decision(task, route):
    missing=[]; failing=[]
    for role in route.get('agents',[]):
        if role not in HANDOFF_ROLES:
            continue
        p=run_dir(task)/'handoffs'/f'{role}.json'
        if not p.exists():
            missing.append(f'handoff:{role}')
            continue
        try:
            data=json.loads(p.read_text(encoding='utf-8'))
            validate_handoff(role,data)
        except Exception:
            failing.append(f'handoff:{role}')
    return missing,failing

def _authoritative_checks_decision(task, latest):
    row=latest.get('checks')
    if not row:
        return False,'acceptance:checks_missing'
    if row.get('evidence_type')!='DETERMINISTIC' or row.get('status')!='PASS':
        return False,'acceptance:checks_not_deterministic_pass'
    if row.get('actor')!='check-runner' or row.get('exit_code')!=0:
        return False,'acceptance:checks_not_authoritative'
    expected=f'.harness/runs/{task}/checks-report.json'
    if row.get('artifact')!=expected:
        return False,'acceptance:checks_report_mismatch'
    report_path=ROOT/expected
    if not report_path.is_file():
        return False,'acceptance:checks_report_missing'
    try:
        report=json.loads(report_path.read_text(encoding='utf-8'))
    except Exception:
        return False,'acceptance:checks_report_invalid'
    if report.get('task_id')!=task or report.get('status')!='PASS':
        return False,'acceptance:checks_report_not_pass'
    return True,None

def _acceptance_decision(task, route, latest):
    # Acceptance is derived from authoritative workflow provenance. A standalone
    # `acceptance` ledger row is intentionally ignored so the finish gate cannot
    # be satisfied by evidence laundering after a failed CLOSE attempt.
    checks_ok,reason=_authoritative_checks_decision(task,latest)
    if not checks_ok:
        return False,reason

    requires_verification=bool((route.get('requirements') or {}).get('verification'))
    role='verifier' if requires_verification else 'reviewer'
    category='verification' if requires_verification else 'review'

    if role not in route.get('agents',[]):
        return False,f'acceptance:{role}_not_routed'

    row=latest.get(category)
    if not row:
        return False,f'acceptance:{category}_missing'
    if row.get('actor')!=role or row.get('status')!='PASS':
        return False,f'acceptance:{category}_not_authoritative_pass'
    if row.get('evidence_type') not in {'INFERRED','DETERMINISTIC'}:
        return False,f'acceptance:{category}_evidence_invalid'

    expected=f'.harness/runs/{task}/handoffs/{role}.json'
    if row.get('artifact')!=expected:
        return False,f'acceptance:{category}_handoff_mismatch'
    handoff_path=ROOT/expected
    if not handoff_path.is_file():
        return False,f'acceptance:{category}_handoff_missing'
    try:
        data=json.loads(handoff_path.read_text(encoding='utf-8'))
        validate_handoff(role,data)
    except Exception:
        return False,f'acceptance:{category}_handoff_invalid'
    if data.get('task_id')!=task or data.get('status')!='PASS':
        return False,f'acceptance:{category}_handoff_not_pass'

    return True,None

def finish_decision(task,risk,require_publication=True):
    task=safe_task_id(task)
    route_path=run_dir(task)/'route.json'
    if not route_path.exists():
        return {'allow':False,'required':['route'],'missing':['route'],'failing':[],'reason':'route.json missing'}
    try:
        route=json.loads(route_path.read_text(encoding='utf-8'))
    except Exception as e:
        return {'allow':False,'required':['route'],'missing':[],'failing':['route'],'reason':str(e)}
    authoritative_risk=route.get('risk')
    if risk != authoritative_risk:
        return {'allow':False,'required':[],'missing':[],'failing':['risk_state_mismatch'],'reason':f'route risk is {authoritative_risk}, caller supplied {risk}'}

    policy=load_json('harness/policies/risk-policy.json')
    req=list(policy['finish_requirements'][authoritative_risk])
    for role,category in ROLE_EVIDENCE.items():
        if role in route.get('agents',[]) and category not in req:
            req.append(category)

    ok,progress_missing,progress_failing,progress_reason=_progress_decision(task,route)
    if not ok:
        return {'allow':False,'required':req+['progress'],'missing':progress_missing,'failing':progress_failing,'reason':progress_reason}

    handoff_missing,handoff_failing=_handoff_decision(task,route)

    try:
        rows=read_evidence(task)
    except Exception as e:
        return {'allow':False,'required':req,'missing':handoff_missing,'failing':handoff_failing+['evidence_chain'],'reason':str(e)}
    latest={}
    for r in rows: latest[r['category']]=r
    missing=list(handoff_missing); failing=list(handoff_failing)
    for cat in req:
        if cat=='acceptance':
            acceptance_ok,acceptance_reason=_acceptance_decision(task,route,latest)
            if not acceptance_ok:
                missing.append('acceptance')
                if acceptance_reason:
                    failing.append(acceptance_reason)
            continue
        r=latest.get(cat)
        if not r:
            missing.append(cat); continue
        if cat in INFERRED_ALLOWED_CATEGORIES:
            if r.get('evidence_type') not in {'INFERRED','DETERMINISTIC'}:
                missing.append(cat); continue
        elif r.get('evidence_type')!='DETERMINISTIC':
            missing.append(cat); continue
        if r.get('status')!='PASS':
            failing.append(cat); continue
        if cat=='checks' and r.get('command') is not None and r.get('exit_code') != 0:
            failing.append(cat)

    tdd=tdd_finish_decision(task)
    missing += tdd.get('missing',[]); failing += tdd.get('failing',[])
    combined_req=list(req)+list(tdd.get('required',[]))+['progress']

    impact=impact_finish_decision(task)
    missing += impact.get('missing',[]); failing += impact.get('failing',[])
    combined_req += list(impact.get('required',[]))

    if require_publication and route.get('isolation') == 'worktree':
        from worktree import publish_status
        publication=publish_status(task)
        combined_req.append('publication')
        if not publication.get('published'):
            missing.append('publication')
            reason=publication.get('reason')
            if reason and reason != 'publish.json missing':
                failing.append('publication:'+reason)

    return {'allow':not missing and not failing,'required':combined_req,'missing':missing,'failing':failing}

def hook(event, payload):
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
        if allow:
            print('{}'); return 0
        out={'hookSpecificOutput':{'hookEventName':'PreToolUse','permissionDecision':'deny','permissionDecisionReason':reason}}
        print(json.dumps(out)); return 0
    if provider=='gemini' and event in {'pre-shell','pre-write'}:
        if allow:
            print('{}'); return 0
        print(json.dumps({'decision':'deny','reason':reason})); return 0
    if provider=='cursor' and event in {'pre-shell','pre-write'}:
        out={'permission':'allow' if allow else 'deny'}
        if not allow:
            out['user_message']=reason; out['agent_message']=reason
        print(json.dumps(out)); return 0 if allow else 2
    if provider=='cursor':
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
