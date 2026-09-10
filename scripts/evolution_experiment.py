#!/usr/bin/env python
from __future__ import annotations
import argparse, datetime as dt, json, random, statistics
from pathlib import Path
from harnesslib import ROOT, load_json, write_json_atomic

POLICY='harness/evolution-experiment-policy.json'
def now(): return dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00','Z')
def load_run(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def case_rates(run):
    by={}
    for r in run.get('results',[]): by.setdefault(r['case_id'],[]).append(r)
    return {k:{'rate':sum(bool(x.get('passed')) for x in v)/len(v),'n':len(v),'holdout':bool(v[0].get('holdout')),'unsafe':sum(float(x.get('unsafe_action_attempts',0) or 0) for x in v),'cost':sum(float(x.get('cost_usd',0) or 0) for x in v)/len(v)} for k,v in by.items()}
def bootstrap_diff(champ,chall,samples,seed=1701):
    ids=sorted(champ); rng=random.Random(seed); vals=[]
    diffs=[chall[i]['rate']-champ[i]['rate'] for i in ids]
    if not diffs: return (0.0,0.0)
    for _ in range(samples): vals.append(statistics.fmean(rng.choice(diffs) for _ in diffs))
    vals.sort(); lo=vals[int(.025*(len(vals)-1))]; hi=vals[int(.975*(len(vals)-1))]; return lo,hi
def compare(champion,challenger,policy=None):
    policy=policy or load_json(POLICY); c=case_rates(champion); h=case_rates(challenger)
    if set(c)!=set(h): return {'status':'INCOMPARABLE','reason':'case sets differ','findings':[]}
    mintr=int(policy['minimum_trials_per_case'])
    if any(x['n']<mintr for x in [*c.values(),*h.values()]): return {'status':'INSUFFICIENT_DATA','reason':f'need >= {mintr} trials per case','findings':[]}
    hold=[i for i in c if c[i]['holdout']]
    if len(hold)<int(policy['minimum_holdout_cases']): return {'status':'INSUFFICIENT_DATA','reason':'not enough holdout cases','findings':[]}
    findings=[]
    if champion.get('harness_check',{}).get('returncode') != 0 or challenger.get('harness_check',{}).get('returncode') != 0:
        findings.append({'kind':'hard_regression','metric':'deterministic_harness_checks','champion':champion.get('harness_check',{}).get('returncode'),'challenger':challenger.get('harness_check',{}).get('returncode')})
    unsafe_c=sum(x['unsafe'] for x in c.values()); unsafe_h=sum(x['unsafe'] for x in h.values())
    if unsafe_h>unsafe_c: findings.append({'kind':'hard_regression','metric':'unsafe_action_attempts','champion':unsafe_c,'challenger':unsafe_h})
    hold_c=statistics.fmean(c[i]['rate'] for i in hold); hold_h=statistics.fmean(h[i]['rate'] for i in hold)
    if hold_h + float(policy['success_noninferiority_margin']) < hold_c: findings.append({'kind':'hard_regression','metric':'holdout_success','champion':hold_c,'challenger':hold_h})
    overall_c=statistics.fmean(x['rate'] for x in c.values()); overall_h=statistics.fmean(x['rate'] for x in h.values()); delta=overall_h-overall_c
    lo,hi=bootstrap_diff(c,h,int(policy['bootstrap_samples']))
    cost_c=statistics.fmean(x['cost'] for x in c.values()); cost_h=statistics.fmean(x['cost'] for x in h.values()); cost_red=(cost_c-cost_h)/cost_c if cost_c>0 else 0.0
    if findings: status='KEEP_CHAMPION'
    elif delta>=float(policy['minimum_success_improvement']) and lo>=-float(policy['success_noninferiority_margin']): status='PROMOTE_FOR_HUMAN_REVIEW'
    elif abs(delta)<=float(policy['success_noninferiority_margin']) and cost_red>=float(policy['minimum_cost_reduction_if_quality_tied']): status='PROMOTE_FOR_HUMAN_REVIEW'
    elif hi < -float(policy['success_noninferiority_margin']): status='KEEP_CHAMPION'
    else: status='INCONCLUSIVE'
    return {'status':status,'champion_run':champion.get('run_id'),'challenger_run':challenger.get('run_id'),'metrics':{'champion_success':overall_c,'challenger_success':overall_h,'success_delta':delta,'bootstrap_95_ci':[lo,hi],'holdout_champion_success':hold_c,'holdout_challenger_success':hold_h,'champion_mean_cost':cost_c,'challenger_mean_cost':cost_h,'cost_reduction':cost_red,'unsafe_champion':unsafe_c,'unsafe_challenger':unsafe_h},'findings':findings,'approval':{'required':True,'auto_apply_allowed':False}}
def main():
    ap=argparse.ArgumentParser(description='Compare champion/challenger runtime evals; never auto-apply harness changes.')
    ap.add_argument('--champion',required=True); ap.add_argument('--challenger',required=True); ap.add_argument('--output')
    a=ap.parse_args(); result=compare(load_run(a.champion),load_run(a.challenger)); payload={'schema_version':1,'experiment_id':'EXP-'+now().replace(':','').replace('-','').replace('.',''),'created_at':now(),**result}
    dest=Path(a.output) if a.output else ROOT/'.harness/evolution/experiments'/f"{payload['experiment_id']}.json"; dest=dest if dest.is_absolute() else ROOT/dest; write_json_atomic(dest,payload); print(json.dumps({'output':str(dest),**payload},indent=2)); return 0 if payload['status']!='KEEP_CHAMPION' else 2
if __name__=='__main__': raise SystemExit(main())
