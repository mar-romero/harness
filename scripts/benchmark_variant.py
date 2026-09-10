#!/usr/bin/env python
"""Apply benchmark-only ablations to a disposable workspace."""
from __future__ import annotations
import argparse, json, re, subprocess, sys
from pathlib import Path

def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))

def dump_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

def disable_tdd(repo: Path):
    p=repo/"harness/tdd-policy.json"
    if p.exists():
        d=load_json(p)
        for risk in ["R0","R1","R2","R3"]:
            d.setdefault("risk_defaults",{})[risk]="test_after_allowed"
        for key in list(d.get("signals",{})):
            if key != "test_after_allowed":
                d["signals"][key]=[]
        d["structured_required_factors"]=[]
        for mode in list(d.get("evidence",{})):
            d["evidence"][mode]=[]
        dump_json(p,d)
    py=repo/"scripts/tdd_policy.py"
    if py.exists():
        s=py.read_text(encoding="utf-8")
        marker="# BENCHMARK_ABLATION_TDD"
        if marker not in s:
            m=re.search(r"^def profile_task\([^\n]+\):\n",s,re.M)
            if not m:
                raise RuntimeError("could not patch tdd_policy.py")
            inject=m.group(0) + "    " + marker + "\n    return {'mode':'test_after_allowed','reasons':['benchmark ablation'],'test_designer':False,'required_evidence':[],'red_evidence_required':False,'negative_boundary_analysis_required':False,'mutation_recommended':False,'mutation_required':False,'small_uniform_cycles':False}\n"
            s=s[:m.start()]+inject+s[m.end():]
            py.write_text(s,encoding="utf-8")

def disable_impact(repo: Path):
    p=repo/"harness/impact-policy.json"
    if p.exists():
        d=load_json(p)
        for risk in ["R0","R1","R2","R3"]:
            d.setdefault("finish_gate",{})[risk]=False
        d["graph_depth"]=0
        dump_json(p,d)
    cp=repo/"harness/context-policy.json"
    if cp.exists():
        d=load_json(cp)
        d["graph_neighbor_depth"]=0
        d["graph_neighbor_bonus"]=0
        dump_json(cp,d)

def eager_agents(repo: Path):
    p=repo/"harness/agent-budget-policy.json"
    if not p.exists():
        return
    d=load_json(p)
    support=list(d.get("support_agents",["explorer","planner","debugger","docs-researcher"]))
    d.setdefault("initial",{})["always_if_routed"]=support
    d["initial"]["planner_risks"]=["R0","R1","R2","R3"]
    d["initial"]["planner_impact_severities"]=["low","medium","high","critical"]
    dump_json(p,d)

def no_reviewer(repo: Path):
    p=repo/"scripts/task_router.py"
    if not p.exists():
        return
    s=p.read_text(encoding="utf-8")
    marker="# BENCHMARK_ABLATION_REVIEWER"
    if marker in s:
        return
    anchor="agents=list(dict.fromkeys(agents)); skills=list(dict.fromkeys(skills));"
    if anchor not in s:
        raise RuntimeError("could not locate task_router dedupe anchor")
    repl=anchor+" "+marker+"\n    agents=[a for a in agents if a!='reviewer']; skills=[x for x in skills if x not in {'bounded-review','independent-review'}]"
    p.write_text(s.replace(anchor,repl,1),encoding="utf-8")

def no_code_quality(repo: Path):
    p=repo/"harness/manifest.yaml"
    if not p.exists():
        return
    d=load_json(p)
    for agent in ["implementer","reviewer"]:
        if agent in d.get("agents",{}):
            d["agents"][agent]["skills"]=[x for x in d["agents"][agent].get("skills",[]) if x!="code-quality"]
    dump_json(p,d)
    cp=subprocess.run([sys.executable,str(repo/"scripts/compile_harness.py")],cwd=repo,text=True,capture_output=True)
    if cp.returncode:
        raise RuntimeError("compile_harness failed after code-quality ablation:\n"+cp.stdout+cp.stderr)

def apply(repo: Path, ablations: list[str]):
    dispatch={
        "tdd":disable_tdd,
        "impact":disable_impact,
        "progressive_budget":eager_agents,
        "reviewer":no_reviewer,
        "code_quality":no_code_quality,
    }
    for item in ablations:
        if item not in dispatch:
            raise ValueError(f"unknown ablation: {item}")
        dispatch[item](repo)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--ablation",action="append",default=[])
    a=ap.parse_args()
    repo=Path(a.repo).resolve()
    apply(repo,a.ablation)
    print(json.dumps({"repo":str(repo),"ablations":a.ablation},indent=2))
if __name__=="__main__":
    main()
