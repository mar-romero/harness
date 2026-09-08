#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from harnesslib import ROOT, load_json, run_dir, safe_task_id, write_json_atomic

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00","Z")

def _policy():
    return load_json("harness/agent-budget-policy.json")

def budget_path(task_id: str):
    return run_dir(safe_task_id(task_id))/"agent-budget.json"

def _impact_for(task_id: str):
    p=run_dir(task_id)/"impact.json"
    if not p.exists(): return {}
    try: return json.loads(p.read_text(encoding="utf-8"))
    except Exception: return {}

def _external_uncertainty(task: dict, route: dict):
    rf=task.get("risk_factors") or {}
    if not rf.get("external_contract") and "docs-researcher" not in route.get("agents",[]):
        return False
    text=" ".join([
        str(task.get("description") or ""),
        str((task.get("request") or {}).get("canonical_english") or ""),
        " ".join(map(str,task.get("tags") or []))
    ]).lower()
    uncertainty_terms=("unknown","uncertain","verify","research","investigate","contract","version","sdk","api")
    return any(x in text for x in uncertainty_terms) or ((route.get("tdd") or {}).get("mode")=="spike_then_tdd")

def build_initial(task: dict, route: dict, impact: dict | None=None):
    p=_policy(); allowed=list(dict.fromkeys(route.get("agents") or []))
    impact=impact or {}
    severity=(impact.get("impact") or {}).get("severity","low")
    support=set()
    for a in p["initial"]["always_if_routed"]:
        if a in allowed: support.add(a)
    if route.get("risk") in set(p["initial"]["planner_risks"]) or severity in set(p["initial"]["planner_impact_severities"]):
        if "planner" in allowed: support.add("planner")
    if p["initial"]["docs_researcher_when_external_uncertainty"] and _external_uncertainty(task,route):
        if "docs-researcher" in allowed: support.add("docs-researcher")

    execution=[a for a in p["execution_agents"] if a in allowed]
    gates=[a for a in p["gate_agents"] if a in allowed]
    deferred=[
        a for a in p["support_agents"]
        if a in allowed and a not in support
    ]
    current=sorted(support, key=allowed.index) + [a for a in execution if a not in support]
    state={
        "schema_version":1,
        "task_id":task["id"],
        "risk":route.get("risk","R1"),
        "impact_severity":severity,
        "route_agents":allowed,
        "current_agents":current,
        "mandatory_execution_agents":execution,
        "mandatory_gate_agents":gates,
        "deferred_support_agents":deferred,
        "signals":[],
        "escalations":[],
        "created_at":now(),
        "updated_at":now()
    }
    return state


def _reconcile_existing(state: dict, task: dict, route: dict) -> tuple[dict, bool]:
    """Rebuild stale routing fields without discarding durable escalation evidence."""
    impact = _impact_for(task["id"])
    expected = build_initial(task, route, impact)
    routing_fields = ("risk", "route_agents", "impact_severity")
    if all(state.get(field) == expected[field] for field in routing_fields):
        return state, False

    old_agents = list(state.get("route_agents") or [])
    old_current = list(state.get("current_agents") or [])
    expected["created_at"] = state.get("created_at", expected["created_at"])
    expected["signals"] = list(state.get("signals") or [])
    expected["escalations"] = list(state.get("escalations") or [])
    for agent in old_current:
        if agent in expected["route_agents"] and agent not in expected["mandatory_gate_agents"]:
            if agent not in expected["current_agents"]:
                expected["current_agents"].append(agent)
    expected["history"] = list(state.get("history") or []) + [{
        "at": now(),
        "event": "route_reconciled",
        "old_risk": state.get("risk"),
        "new_risk": expected["risk"],
        "old_route_agents": old_agents,
        "new_route_agents": expected["route_agents"],
    }]
    return expected, True

def init(task: dict, route: dict, overwrite: bool=False):
    path=budget_path(task["id"])
    if path.exists() and not overwrite:
        state = json.loads(path.read_text(encoding="utf-8"))
        state, changed = _reconcile_existing(state, task, route)
        if changed:
            write_json_atomic(path, state)
        return state
    state=build_initial(task,route,_impact_for(task["id"]))
    write_json_atomic(path,state)
    return state

def load(task_id: str):
    return json.loads(budget_path(task_id).read_text(encoding="utf-8"))

def _activate(state: dict, agents: list[str], signal: str):
    allowed=state["route_agents"]
    newly=[]
    for a in agents:
        if a in allowed and a not in state["current_agents"] and a not in state["mandatory_gate_agents"]:
            state["current_agents"].append(a); newly.append(a)
        if a in state["deferred_support_agents"]:
            state["deferred_support_agents"].remove(a)
    if signal not in state["signals"]: state["signals"].append(signal)
    state["escalations"].append({"at":now(),"signal":signal,"activated_agents":newly})
    state["updated_at"]=now()
    return newly

def signal(task_id: str, name: str, note: str | None=None):
    state=load(task_id); p=_policy()
    if name not in p["escalation"]:
        raise SystemExit(f"unknown escalation signal: {name}")
    newly=_activate(state,list(p["escalation"][name]),name)
    if note: state["escalations"][-1]["note"]=note
    write_json_atomic(budget_path(task_id),state)
    return {"task_id":task_id,"signal":name,"activated_agents":newly,"state":state}

def observe_progress(task_id: str, step: str, status: str, progress: dict | None=None):
    path=budget_path(task_id)
    if not path.exists(): return None
    state=load(task_id)
    p=_policy()
    mapping={
        ("IMPLEMENT","FAIL"):"implementation_fail",
        ("IMPLEMENT","BLOCKED"):"blocked",
        ("IMPLEMENT","INSUFFICIENT"):"blocked",
        ("CHECKS","FAIL"):"checks_fail",
        ("CHECKS","BLOCKED"):"blocked",
        ("CHECKS","INSUFFICIENT"):"blocked",
        ("REVIEW","FAIL"):"review_fail",
        ("REVIEW","BLOCKED"):"blocked",
        ("REVIEW","INSUFFICIENT"):"blocked",
    }
    sig=mapping.get((step,status))
    activated=[]
    if sig:
        activated += _activate(state,list(p["escalation"][sig]),sig)
    failures=int((progress or {}).get("failures",0))
    if failures >= int(p["repeat_failure_threshold"]) and "repeated_failure" not in state["signals"]:
        activated += _activate(state,list(p["escalation"]["repeated_failure"]),"repeated_failure")
    write_json_atomic(path,state)
    return {"activated_agents":list(dict.fromkeys(activated)),"current_agents":state["current_agents"],"signals":state["signals"]}

def stage_agents(task_id: str, stage: str):
    state=load(task_id)
    if stage=="pre_impl":
        return state["current_agents"]
    if stage=="gates":
        return state["mandatory_gate_agents"]
    if stage=="all_authorized":
        return state["route_agents"]
    raise SystemExit("stage must be pre_impl, gates, or all_authorized")

def _inside(value: str) -> Path:
    p=Path(value); p=p if p.is_absolute() else ROOT/p; p=p.resolve()
    try: p.relative_to(ROOT.resolve())
    except ValueError as e: raise SystemExit("path must be inside repository") from e
    return p

def main():
    ap=argparse.ArgumentParser()
    sub=ap.add_subparsers(dest="cmd",required=True)
    p=sub.add_parser("init"); p.add_argument("task"); p.add_argument("--route",required=True); p.add_argument("--overwrite",action="store_true")
    p=sub.add_parser("status"); p.add_argument("task_id")
    p=sub.add_parser("signal"); p.add_argument("task_id"); p.add_argument("signal"); p.add_argument("--note")
    p=sub.add_parser("agents"); p.add_argument("task_id"); p.add_argument("--stage",choices=["pre_impl","gates","all_authorized"],default="pre_impl")
    a=ap.parse_args()
    if a.cmd=="init":
        task=json.loads(_inside(a.task).read_text(encoding="utf-8"))
        route=json.loads(_inside(a.route).read_text(encoding="utf-8"))
        out=init(task,route,a.overwrite)
    elif a.cmd=="status":
        out=load(a.task_id)
    elif a.cmd=="signal":
        out=signal(a.task_id,a.signal,a.note)
    else:
        out={"task_id":a.task_id,"stage":a.stage,"agents":stage_agents(a.task_id,a.stage)}
    print(json.dumps(out,indent=2,ensure_ascii=False))

if __name__=="__main__":
    main()
