#!/usr/bin/env python3
from __future__ import annotations
import argparse, datetime as dt, json
from pathlib import Path
from harnesslib import ROOT, load_json, run_dir, safe_task_id, write_json_atomic

POLICY=ROOT/'harness/orchestrator-policy.json'
def now(): return dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00','Z')
def path(task): return run_dir(task)/'progress.json'

def steps_for(route):
    out=['IMPLEMENT','CHECKS']
    agents=route.get('agents',[])
    if 'reviewer' in agents: out.append('REVIEW')
    if 'test-auditor' in agents: out.append('TEST_AUDIT')
    if 'verifier' in agents: out.append('VERIFY')
    if 'security-reviewer' in agents: out.append('SECURITY_REVIEW')
    if route.get('human_gate'): out.append('HUMAN_GATE')
    out.append('CLOSE'); return out

def init_progress(task,route,overwrite=False):
    safe_task_id(task); p=path(task)
    if p.exists() and not overwrite: return json.loads(p.read_text(encoding='utf-8'))
    steps=steps_for(route); state={'schema_version':1,'task_id':task,'risk':route['risk'],'state':'READY','current_step':steps[0],'steps':steps,'completed':[],'attempts':{},'failures':0,'recommended_action':'start','history':[{'at':now(),'event':'initialized','step':steps[0]}]}
    write_json_atomic(p,state); return state

def load(task): return json.loads(path(safe_task_id(task)).read_text(encoding='utf-8'))
def _next(state):
    cur=state['current_step']; i=state['steps'].index(cur); return state['steps'][i+1] if i+1<len(state['steps']) else None

def record(task,status,step=None,note=None):
    policy=json.loads(POLICY.read_text(encoding='utf-8')); s=load(task); step=step or s['current_step']
    if step!=s['current_step']: raise ValueError(f'expected current step {s["current_step"]}, got {step}')
    s['attempts'][step]=int(s['attempts'].get(step,0))+1; evt={'at':now(),'event':'step_result','step':step,'status':status,'attempt':s['attempts'][step]}
    if note: evt['note']=note
    s['history'].append(evt)
    if status=='PASS':
        if step not in s['completed']: s['completed'].append(step)
        nxt=_next(s)
        if nxt is None or step=='CLOSE': s['state']='DONE'; s['current_step']='CLOSE'; s['recommended_action']='none'
        else: s['state']='RUNNING'; s['current_step']=nxt; s['recommended_action']='continue'
    elif status in {'BLOCKED','INSUFFICIENT'}:
        s['state']='BLOCKED'; s['failures']+=1; s['recommended_action']=policy['failure_actions'].get(step,'escalate_or_replan')
    elif status=='FAIL':
        s['failures']+=1
        if step=='SECURITY_REVIEW' or s['attempts'][step]>=int(policy['max_attempts_per_step']) or s['failures']>=int(policy['max_total_failures']):
            s['state']='STALLED'; s['recommended_action']=policy['failure_actions'].get(step,'replan')
        else:
            s['state']='WAITING'; s['recommended_action']=policy['failure_actions'].get(step,'retry_once_then_replan')
    else: raise ValueError('status must be PASS, FAIL, BLOCKED or INSUFFICIENT')
    write_json_atomic(path(task),s); return s

def resume(task,step=None,note=None):
    s=load(task)
    if s['state'] not in {'WAITING','BLOCKED','STALLED'}: raise ValueError(f'cannot resume from state {s["state"]}')
    if step:
        if step not in s['steps']: raise ValueError('step not in route')
        s['current_step']=step
    s['state']='RUNNING'; s['recommended_action']='continue'; s['history'].append({'at':now(),'event':'resumed','step':s['current_step'],'note':note or ''}); write_json_atomic(path(task),s); return s

def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('init'); p.add_argument('task'); p.add_argument('--route',required=True); p.add_argument('--overwrite',action='store_true')
    p=sub.add_parser('status'); p.add_argument('task')
    p=sub.add_parser('record'); p.add_argument('task'); p.add_argument('--status',required=True,choices=['PASS','FAIL','BLOCKED','INSUFFICIENT']); p.add_argument('--step'); p.add_argument('--note')
    p=sub.add_parser('resume'); p.add_argument('task'); p.add_argument('--step'); p.add_argument('--note')
    a=ap.parse_args()
    try:
        if a.cmd=='init': s=init_progress(a.task,json.loads(Path(a.route).read_text(encoding='utf-8')),a.overwrite)
        elif a.cmd=='status': s=load(a.task)
        elif a.cmd=='record': s=record(a.task,a.status,a.step,a.note)
        else: s=resume(a.task,a.step,a.note)
        print(json.dumps(s,indent=2,ensure_ascii=False))
    except Exception as e: print(json.dumps({'ok':False,'reason':str(e)},ensure_ascii=False,indent=2)); raise SystemExit(2)
if __name__=='__main__': main()
