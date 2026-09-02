#!/usr/bin/env python3
from __future__ import annotations
import argparse, datetime as dt, json, math, statistics, subprocess, tempfile, time, uuid
from pathlib import Path
from harnesslib import ROOT, write_json_atomic

def now(): return dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00','Z')
def load_suite(p):
    d=json.loads(Path(p).read_text(encoding='utf-8')); cases=d.get('cases')
    if not isinstance(cases,list) or not cases: raise ValueError('suite requires non-empty cases')
    ids=[x.get('id') for x in cases]
    if any(not x for x in ids) or len(ids)!=len(set(ids)): raise ValueError('case ids must be unique/non-empty')
    return d

def summarize(results,trials):
    by={}
    for r in results: by.setdefault(r['case_id'],[]).append(r)
    rates=[sum(1 for x in xs if x['passed'])/len(xs) for xs in by.values()]
    strict=[all(x['passed'] for x in xs) for xs in by.values()]
    nums=lambda k:[float(r[k]) for r in results if isinstance(r.get(k),(int,float)) and not isinstance(r.get(k),bool)]
    out={'cases':len(by),'trials_per_case':trials,'rollouts':len(results),'mean_success_rate':sum(rates)/len(rates),'pass_power_k':sum(strict)/len(strict)}
    for k in ['tokens','cost_usd','latency_seconds','unsafe_action_attempts','human_interventions']:
        v=nums(k)
        if v: out['mean_'+k]=statistics.fmean(v)
    return out

def run_suite(suite_path,adapter,trials,timeout,provider=None,model=None,agent=None):
    suite=load_suite(suite_path); results=[]
    for case in suite['cases']:
        for trial in range(1,trials+1):
            with tempfile.TemporaryDirectory(prefix='harness-eval-') as td:
                inp=Path(td)/'case.json'; out=Path(td)/'result.json'; inp.write_text(json.dumps(case,ensure_ascii=False),encoding='utf-8')
                start=time.monotonic(); cp=subprocess.run([*adapter,str(inp),str(out)],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout,check=False); elapsed=time.monotonic()-start
                payload={}
                if out.exists():
                    try: payload=json.loads(out.read_text(encoding='utf-8'))
                    except Exception: payload={}
                passed=bool(payload.get('passed')) and cp.returncode==0
                row={'case_id':case['id'],'trial':trial,'holdout':bool(case.get('holdout')),'passed':passed,'adapter_returncode':cp.returncode,'wall_seconds':round(elapsed,6),'output_tail':cp.stdout[-2000:]}
                for k,v in payload.items():
                    if k not in row: row[k]=v
                results.append(row)
    hcheck=subprocess.run(['python3','scripts/check_harness.py'],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,check=False)
    rid='RTE-'+now().replace(':','').replace('-','').replace('.','')+'-'+uuid.uuid4().hex[:6]
    return {'schema_version':1,'run_id':rid,'harness_check':{'returncode':hcheck.returncode,'output_tail':hcheck.stdout[-4000:]},'created_at':now(),'suite':suite.get('suite') or Path(suite_path).stem,'provider':provider,'model':model,'agent':agent,'adapter':adapter,'results':results,'summary':summarize(results,trials)}

def main():
    ap=argparse.ArgumentParser(description='Repeated live/runtime evaluation lab for harness+provider combinations.')
    ap.add_argument('--suite',required=True); ap.add_argument('--adapter',nargs='+',required=True); ap.add_argument('--trials',type=int,default=5); ap.add_argument('--timeout',type=int,default=900); ap.add_argument('--provider'); ap.add_argument('--model'); ap.add_argument('--agent'); ap.add_argument('--output')
    a=ap.parse_args();
    if a.trials<1: ap.error('--trials must be >=1')
    try: payload=run_suite(a.suite,a.adapter,a.trials,a.timeout,a.provider,a.model,a.agent)
    except Exception as e: print(json.dumps({'ok':False,'reason':str(e)},indent=2)); raise SystemExit(2)
    dest=Path(a.output) if a.output else ROOT/'.harness/runtime-evals/runs'/f"{payload['run_id']}.json"; dest=dest if dest.is_absolute() else ROOT/dest
    write_json_atomic(dest,payload); print(json.dumps({'ok':True,'output':str(dest),'run_id':payload['run_id'],'summary':payload['summary']},indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
