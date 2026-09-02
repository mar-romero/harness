#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re
from pathlib import Path
from harnesslib import ROOT, load_manifest, safe_task_id, write_json_atomic, run_dir

RISK_RANK={'R0':0,'R1':1,'R2':2,'R3':3}
def maxrisk(a,b): return a if RISK_RANK[a]>=RISK_RANK[b] else b

def _factor(task,name): return bool((task.get('risk_factors') or {}).get(name,False))
def route(task):
    m=load_manifest(); req=task.get('request') or {}; canonical=req.get('canonical_english') or task.get('description','')
    original=req.get('original_text','')
    text=' '.join([canonical, original, *task.get('tags',[]), *task.get('files',[])]).lower()
    explicit=task.get('risk'); risk=explicit if explicit in RISK_RANK else 'R1'; reasons=[]
    if re.search(r'\b(doc|docs|documentation|readme|typo|formatting|rename|documentaci[oó]n|formato|renombrar)\b', text) and not explicit:
        risk='R0'; reasons.append('documentation/mechanical signal')
    r2=[r'\bmigration\b',r'\bmigraci[oó]n\b',r'\bschema\b',r'\besquema\b',r'\bdatabase\b',r'\bbase de datos\b',r'\bconcurr',r'\bpayment',r'\bpago',r'\bexternal api',r'\bapi externa',r'\bpersist',r'\bcalculation',r'\bc[aá]lculo',r'\bqueue\b',r'\bcola\b']
    r3=[r'\bauth(entication|orization)?\b',r'\bautenticaci[oó]n\b',r'\bautorizaci[oó]n\b',r'\bproduction\b',r'\bproducci[oó]n\b',r'\bprod\b',r'\bsecret',r'\bsecreto',r'\bcredential',r'\bcredencial',r'\bdestruct',r'\bdestructiv',r'\bdelete all\b',r'\beliminar todo\b',r'\bterraform destroy\b']
    structured_r2=['schema_change','database_change','concurrency','financial','external_contract','migration','persistence','important_calculation']
    structured_r3=['touches_auth','touches_production','uses_secrets','destructive_operation','irreversible_change','security_boundary']
    if any(_factor(task,x) for x in structured_r2) or any(re.search(p,text) for p in r2): risk=maxrisk(risk,'R2'); reasons.append('structured/high-correctness-risk signal')
    if any(_factor(task,x) for x in structured_r3) or any(re.search(p,text) for p in r3): risk=maxrisk(risk,'R3'); reasons.append('structured critical/security/production signal')
    bug=bool(_factor(task,'bug_fix') or re.search(r'\b(bug|error|exception|fail|broken|regression|incorrect|falla|fallo|roto|incorrect[oa])\b',text))
    external=bool(_factor(task,'external_contract') or re.search(r'\b(api|sdk|library|protocol|provider|dependency|version|biblioteca|protocolo|proveedor|dependencia|versi[oó]n)\b',text))
    security=bool(any(_factor(task,x) for x in ['touches_auth','uses_secrets','security_boundary']) or re.search(r'\b(auth|security|secret|credential|permission|token|xss|sql injection|csrf|seguridad|secreto|credencial|permiso|autenticaci[oó]n|autorizaci[oó]n)\b',text))
    agents=['explorer']; skills=['task-intake','grounded-evidence']
    if risk!='R0': agents.append('planner')
    if bug: agents.append('debugger'); skills += ['debugging','systemic-defect-triage']
    if external: agents.append('docs-researcher'); skills.append('source-research')
    agents.append('implementer'); skills += ['software-engineering','implementation-loop','evidence-ledger','worktree-isolation']
    if risk in ('R1','R2','R3'): agents.append('reviewer'); skills.append('bounded-review')
    if risk in ('R2','R3'): agents += ['test-auditor','verifier']; skills += ['test-strategy','verification']
    if security or risk=='R3': agents.append('security-reviewer'); skills += ['prompt-injection-defense','tool-output-validation']
    agents=list(dict.fromkeys(agents)); skills=list(dict.fromkeys(skills)); reqs=m['risk_levels'][risk]
    confidence='high' if explicit or reasons or task.get('risk_factors') else 'medium'
    return {'task_id':task['id'],'risk':risk,'risk_reasons':reasons or ['default normal risk'],'confidence':confidence,'agents':agents,'skills':skills,'isolation':'worktree' if 'implementer' in agents else 'none','human_gate':bool(reqs.get('human_gate')),'requirements':reqs,'canonical_language':'en'}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('task'); ap.add_argument('--output'); a=ap.parse_args(); task=json.loads(Path(a.task).read_text(encoding='utf-8')); safe_task_id(task.get('id',''))
    out=route(task); dest=Path(a.output) if a.output else run_dir(task['id'])/'route.json'; dest=dest if dest.is_absolute() else ROOT/dest
    write_json_atomic(dest,out); print(json.dumps(out,indent=2,ensure_ascii=False))
if __name__=='__main__': main()
