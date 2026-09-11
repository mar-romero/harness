#!/usr/bin/env python
from __future__ import annotations
import argparse, datetime as dt, hashlib, json
from pathlib import Path
from harnesslib import run_dir, safe_task_id

PHASES = {"design","contract","characterization","red","green","mutation"}
STATUS = {"PASS","FAIL","BLOCKED","INFO"}
EXPECTED_ACTORS = {
    "design":{"test-designer"},
    "contract":{"test-designer","docs-researcher","planner"},
    "characterization":{"implementer"},
    "red":{"implementer"},
    "green":{"implementer"},
    "mutation":{"test-auditor","implementer"},
}

def path(task):
    return run_dir(safe_task_id(task)) / "tdd-evidence.jsonl"

def _hash(rec):
    return hashlib.sha256(json.dumps(rec,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()

def read(task):
    p=path(task)
    if not p.exists(): return []
    rows=[]; prev="GENESIS"
    for n,line in enumerate(p.read_text(encoding="utf-8").splitlines(),1):
        if not line.strip(): continue
        r=json.loads(line)
        stored=r.get("record_hash"); base=dict(r); base.pop("record_hash",None)
        if r.get("prev_hash") != prev or stored != _hash(base):
            raise ValueError(f"invalid TDD evidence chain at entry {n}")
        rows.append(r); prev=stored
    return rows

def append(task, phase, status, actor, claim, command=None, exit_code=None, artifact=None, notes=None):
    safe_task_id(task)
    if phase not in PHASES: raise SystemExit("invalid TDD phase")
    if status not in STATUS: raise SystemExit("invalid status")
    expected=EXPECTED_ACTORS.get(phase)
    if expected and actor not in expected:
        raise SystemExit(f"{phase} actor must be one of {sorted(expected)}")
    rows=read(task); prev=rows[-1]["record_hash"] if rows else "GENESIS"
    rec={
        "timestamp":dt.datetime.now(dt.timezone.utc).isoformat(),
        "task_id":task,"phase":phase,"status":status,"actor":actor,
        "claim":claim,"prev_hash":prev
    }
    if command is not None: rec["command"]=command
    if exit_code is not None: rec["exit_code"]=exit_code
    if artifact is not None: rec["artifact"]=artifact
    if notes is not None: rec["notes"]=notes
    rec["record_hash"]=_hash(rec)
    p=path(task); p.parent.mkdir(parents=True,exist_ok=True)
    with p.open("a",encoding="utf-8") as f:
        f.write(json.dumps(rec,sort_keys=True,ensure_ascii=False)+"\n")
    return rec

def _valid_phase(phase, r):
    if not r or r.get("status")!="PASS": return False, "missing_or_not_pass"
    if phase=="design":
        return (r.get("actor")=="test-designer"), "wrong_actor"
    if phase=="contract":
        return (bool(r.get("artifact")) and r.get("actor") in EXPECTED_ACTORS["contract"]), "missing_contract_artifact_or_wrong_actor"
    if phase=="characterization":
        return (bool(r.get("command")) and r.get("exit_code")==0), "characterization_must_pass"
    if phase=="red":
        return (bool(r.get("command")) and isinstance(r.get("exit_code"),int) and r.get("exit_code")!=0), "red_must_be_observed_nonzero"
    if phase=="green":
        return (bool(r.get("command")) and r.get("exit_code")==0), "green_must_pass"
    if phase=="mutation":
        return (bool(r.get("command")) and r.get("exit_code")==0), "mutation_check_must_pass"
    return True, "ok"

def finish_decision(task):
    route_path=run_dir(safe_task_id(task))/"route.json"
    if not route_path.exists():
        return {"allow":True,"required":[],"missing":[],"failing":[]}
    route=json.loads(route_path.read_text(encoding="utf-8"))
    required=list(((route.get("tdd") or {}).get("required_evidence") or []))
    if not required:
        return {"allow":True,"required":[],"missing":[],"failing":[]}
    try:
        rows=read(task)
    except Exception as e:
        return {"allow":False,"required":required,"missing":[],"failing":["tdd_evidence_chain"],"reason":str(e)}
    latest={}
    for r in rows: latest[r["phase"]]=r
    missing=[]; failing=[]
    for phase in required:
        if phase not in latest:
            missing.append(f"tdd:{phase}"); continue
        ok,_=_valid_phase(phase,latest[phase])
        if not ok: failing.append(f"tdd:{phase}")
    return {"allow":not missing and not failing,"required":[f"tdd:{x}" for x in required],"missing":missing,"failing":failing}

def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest="cmd",required=True)
    p=sub.add_parser("add"); p.add_argument("task"); p.add_argument("--phase",required=True,choices=sorted(PHASES)); p.add_argument("--status",required=True,choices=sorted(STATUS)); p.add_argument("--actor",required=True); p.add_argument("--claim",required=True); p.add_argument("--command"); p.add_argument("--exit-code",type=int); p.add_argument("--artifact"); p.add_argument("--notes")
    p=sub.add_parser("summary"); p.add_argument("task")
    p=sub.add_parser("finish"); p.add_argument("task")
    a=ap.parse_args()
    if a.cmd=="add":
        print(json.dumps(append(a.task,a.phase,a.status,a.actor,a.claim,a.command,a.exit_code,a.artifact,a.notes),indent=2))
    elif a.cmd=="summary":
        print(json.dumps(read(a.task),indent=2))
    else:
        d=finish_decision(a.task); print(json.dumps(d,indent=2)); raise SystemExit(0 if d["allow"] else 2)

if __name__=="__main__":
    main()
