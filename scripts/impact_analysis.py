#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from harnesslib import ROOT, load_json, run_dir, safe_task_id, write_json_atomic
from context_graph import build_graph

TEST_RE = re.compile(r"(^|/)(tests?|specs?)(/|$)|(^|/)(test_|spec_)|(_test|_spec)\.", re.I)
CODE_EXTS = {".py",".js",".ts",".tsx",".jsx",".go",".rs",".java",".kt",".cs",".rb",".php",".swift"}
RISK_WEIGHT = {"R0":0.05, "R1":0.18, "R2":0.40, "R3":0.62}

def _policy():
    return load_json("harness/impact-policy.json")

def _is_test(path: str) -> bool:
    p = Path(path)
    parts = [part.lower() for part in p.parts]
    stem = p.stem.lower()

    if any(part in {"test", "tests", "spec", "specs"} for part in parts[:-1]):
        return True

    if stem.startswith(("test_", "spec_")):
        return True

    if stem.endswith(("_test", "_spec")):
        return True

    return False

def _reverse(graph: dict[str,list[str]]) -> dict[str,set[str]]:
    r: dict[str,set[str]] = {}
    for src, deps in graph.items():
        for dep in deps:
            r.setdefault(dep,set()).add(src)
    return r

def _seed_files(task: dict, context: dict | None, graph: dict[str,list[str]], policy: dict) -> tuple[list[str],str]:
    explicit=[]
    for f in task.get("files") or []:
        rel=Path(str(f)).as_posix()
        candidate=(ROOT/rel).resolve()
        try:
            candidate.relative_to(ROOT.resolve())
        except ValueError:
            continue
        explicit.append(rel)
    if explicit:
        return sorted(dict.fromkeys(explicit)), "explicit-task-files"

    candidates=[]
    for row in (context or {}).get("files") or []:
        path=str(row.get("path") or "")
        if not path or not (ROOT/path).is_file():
            continue
        if Path(path).suffix.lower() not in CODE_EXTS:
            continue
        reasons=set(row.get("reason") or [])
        if reasons == {"policy"}:
            continue
        score=float(row.get("score") or 0)
        candidates.append((score,path))
    candidates.sort(key=lambda x:(-x[0],x[1]))
    n=int(policy.get("max_context_seed_files",6))
    seeds=[p for _,p in candidates[:n]]
    if seeds:
        return seeds, "bounded-context"

    # Last-resort path-token localization; conservative and bounded.
    req=task.get("request") or {}
    text=" ".join([
        str(req.get("canonical_english") or ""),
        str(req.get("original_text") or ""),
        str(task.get("description") or "")
    ]).lower()
    toks={x for x in re.findall(r"[a-z0-9_\-]{4,}",text) if x not in {"this","that","with","from","into","para","como"}}
    scored=[]
    for path in graph:
        low=path.lower()
        hit=sum(1 for t in toks if t in low)
        if hit: scored.append((hit,path))
    scored.sort(key=lambda x:(-x[0],x[1]))
    return [p for _,p in scored[:n]], "path-token-fallback"

def _bounded_neighborhood(graph: dict[str,list[str]], seeds: list[str], depth: int, max_nodes: int):
    rev=_reverse(graph)
    seen=set(seeds)
    frontier=set(seeds)
    layers=[]
    for d in range(1, depth+1):
        nxt=set()
        for f in frontier:
            nxt.update(graph.get(f,[]))
            nxt.update(rev.get(f,set()))
        nxt -= seen
        layer=sorted(nxt)[:max(0,max_nodes-len(seen))]
        layers.append({"depth":d,"files":layer})
        seen.update(layer)
        frontier=set(layer)
        if not frontier or len(seen)>=max_nodes:
            break
    return sorted(seen-set(seeds)), layers, rev

def _severity(task: dict, risk: str, seeds: list[str], graph: dict[str,list[str]], rev: dict[str,set[str]], affected: list[str], policy: dict):
    max_fanin=max((len(rev.get(s,set())) for s in seeds), default=0)
    max_fanout=max((len(graph.get(s,[])) for s in seeds), default=0)
    critical=0
    critical_tokens=[str(x).lower() for x in policy.get("critical_path_tokens",[])]
    for p in seeds + affected:
        low=p.lower()
        if any(t in low for t in critical_tokens):
            critical += 1
    score = RISK_WEIGHT.get(risk,0.18)
    score += min(0.12, len(seeds)*0.025)
    score += min(0.18, max_fanin/30.0)
    score += min(0.12, max_fanout/30.0)
    score += min(0.18, len(affected)/100.0)
    score += min(0.18, critical*0.045)
    score=min(1.0,score)
    th=policy["severity_thresholds"]
    if score <= th["low_max"]: sev="low"
    elif score <= th["medium_max"]: sev="medium"
    elif score <= th["high_max"]: sev="high"
    else: sev="critical"
    return {
        "score":round(score,4),
        "severity":sev,
        "max_seed_fanin":max_fanin,
        "max_seed_fanout":max_fanout,
        "critical_path_hits":critical,
        "affected_count":len(affected)
    }

def build_plan(task: dict, route: dict | None=None, context: dict | None=None) -> dict:
    policy=_policy()
    task_id=safe_task_id(task["id"])
    risk=(route or {}).get("risk") or task.get("risk") or "R1"
    graph=build_graph()
    seeds,seed_source=_seed_files(task,context,graph,policy)
    affected,layers,rev=_bounded_neighborhood(
        graph,seeds,int(policy.get("graph_depth",2)),int(policy.get("max_affected_files",80))
    )
    deps=sorted({d for s in seeds for d in graph.get(s,[])})
    dependents=sorted({d for s in seeds for d in rev.get(s,set())})
    related_tests=sorted({
        p for p in set(seeds+affected+deps+dependents)
        if _is_test(p)
    })
    sev=_severity(task,risk,seeds,graph,rev,affected,policy)
    confidence="high" if seed_source=="explicit-task-files" else ("medium" if seeds else "low")
    return {
        "schema_version":1,
        "task_id":task_id,
        "risk":risk,
        "seed_source":seed_source,
        "seed_confidence":confidence,
        "seeds":seeds,
        "direct_dependencies":deps,
        "direct_dependents":dependents,
        "related_tests":related_tests,
        "affected":affected,
        "layers":layers,
        "impact":sev,
        "planned_surface":sorted(set(seeds+affected)),
        "verification_required":bool(policy["finish_gate"].get(risk,False)),
        "notes":{
            "affected_is_not_edit_list":True,
            "use_aci_to_confirm_ambiguous_critical_edges":True
        }
    }

def plan(task_path: Path, route_path: Path | None=None, context_path: Path | None=None) -> dict:
    task=json.loads(task_path.read_text(encoding="utf-8"))
    route=json.loads(route_path.read_text(encoding="utf-8")) if route_path and route_path.exists() else None
    context=json.loads(context_path.read_text(encoding="utf-8")) if context_path and context_path.exists() else None
    out=build_plan(task,route,context)
    dest=run_dir(task["id"])/"impact.json"
    write_json_atomic(dest,out)
    return out

def _git_changed(base: str = "HEAD", cwd: Path = ROOT) -> list[str]:
    cmds = [
        ["git", "diff", "--name-only", "--relative", base],
        ["git", "diff", "--cached", "--name-only", "--relative", base],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ]
    out = []
    for cmd in cmds:
        cp = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
        if cp.returncode == 0:
            out.extend(x.strip() for x in cp.stdout.splitlines() if x.strip())
    return sorted(dict.fromkeys(out))


def capture_baseline(task_id: str) -> dict:
    task_id = safe_task_id(task_id)

    doc = {
        "schema_version": 1,
        "task_id": task_id,
        "base": "HEAD",
        "preexisting_changed_files": _git_changed("HEAD"),
    }

    write_json_atomic(
        run_dir(task_id) / "impact-baseline.json",
        doc,
    )
    return doc
def verify(task_id: str, base: str="HEAD", reviewed_expansion: bool=False, actor: str | None=None, reason: str | None=None) -> dict:
    task_id=safe_task_id(task_id)
    p=run_dir(task_id)/"impact.json"
    if not p.exists():
        raise SystemExit("impact plan missing; run impact_analysis.py plan first")
    plan_doc=json.loads(p.read_text(encoding="utf-8"))
    policy=_policy()
    raw_changed, baseline_mode = _task_changed_files(task_id, base)

    changed = [
        x for x in raw_changed
        if not any(x.startswith(pre) for pre in policy.get("ignored_diff_prefixes",[]))
        and Path(x).name not in set(policy.get("ignored_diff_files",[]))
    ]
    planned=set(plan_doc.get("planned_surface") or [])
    unexpected=sorted(set(changed)-planned)

    reviewed=False
    if unexpected and reviewed_expansion:
        if actor!="verifier" or not (reason or "").strip():
            raise SystemExit("--reviewed-expansion requires --actor verifier and non-empty --reason")
        reviewed=True

    status="PASS" if not unexpected or reviewed else "REVIEW_REQUIRED"
    doc={
        "schema_version":1,
        "task_id":task_id,
        "base":base,
        "baseline_mode":baseline_mode,
        "changed_files":changed,
        "planned_surface_count":len(planned),
        "unexpected_changed_files":unexpected,
        "reviewed_expansion":reviewed,
        "reviewed_by":actor if reviewed else None,
        "review_reason":reason if reviewed else None,
        "related_tests":plan_doc.get("related_tests") or [],
        "impact_severity":(plan_doc.get("impact") or {}).get("severity"),
        "status":status
    }
    write_json_atomic(run_dir(task_id)/"impact-verification.json",doc)
    return doc

def _task_changed_files(task_id: str, base: str = "HEAD") -> tuple[list[str], str]:
    task_id = safe_task_id(task_id)
    route_path = run_dir(task_id) / "route.json"
    route = json.loads(route_path.read_text(encoding="utf-8")) if route_path.exists() else {}

    worktree = ROOT / ".worktrees" / task_id
    lock = ROOT / ".harness" / "locks" / f"{task_id}.json"
    if route.get("isolation") == "worktree":
        if not worktree.exists() or not lock.exists():
            raise SystemExit("task requires worktree isolation but assigned worktree/lock is missing")
        return _git_changed(base, cwd=worktree), "task-worktree"

    current = set(_git_changed(base, cwd=ROOT))
    baseline_path = run_dir(task_id) / "impact-baseline.json"
    if not baseline_path.exists():
        return sorted(current), "legacy-head"

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    preexisting = set(baseline.get("preexisting_changed_files") or [])
    return sorted(current - preexisting), "task-baseline"

def finish_decision(task_id: str) -> dict:
    task_id=safe_task_id(task_id)
    route_path=run_dir(task_id)/"route.json"
    risk="R1"
    if route_path.exists():
        risk=json.loads(route_path.read_text(encoding="utf-8")).get("risk","R1")
    required=bool(_policy()["finish_gate"].get(risk,False))
    if not required:
        return {"allow":True,"required":[],"missing":[],"failing":[]}
    p=run_dir(task_id)/"impact-verification.json"
    if not p.exists():
        return {"allow":False,"required":["impact_verification"],"missing":["impact_verification"],"failing":[]}
    try:
        d=json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return {"allow":False,"required":["impact_verification"],"missing":[],"failing":["impact_verification"],"reason":str(e)}
    if d.get("status")!="PASS":
        return {"allow":False,"required":["impact_verification"],"missing":[],"failing":["impact_verification"]}
    return {"allow":True,"required":["impact_verification"],"missing":[],"failing":[]}

def _resolve_inside(value: str) -> Path:
    p=Path(value)
    p=p if p.is_absolute() else ROOT/p
    p=p.resolve()
    try: p.relative_to(ROOT.resolve())
    except ValueError as e: raise SystemExit("path must remain inside repository") from e
    return p

def main():
    ap=argparse.ArgumentParser()
    sub=ap.add_subparsers(dest="cmd",required=True)
    p=sub.add_parser("plan"); p.add_argument("task"); p.add_argument("--route"); p.add_argument("--context")
    p=sub.add_parser("verify"); p.add_argument("task_id"); p.add_argument("--base",default="HEAD"); p.add_argument("--reviewed-expansion",action="store_true"); p.add_argument("--actor"); p.add_argument("--reason")
    p=sub.add_parser("finish"); p.add_argument("task_id")
    a=ap.parse_args()
    if a.cmd=="plan":
        out=plan(_resolve_inside(a.task),_resolve_inside(a.route) if a.route else None,_resolve_inside(a.context) if a.context else None)
    elif a.cmd=="verify":
        out=verify(a.task_id,a.base,a.reviewed_expansion,a.actor,a.reason)
    else:
        out=finish_decision(a.task_id)
    print(json.dumps(out,indent=2,ensure_ascii=False))
    if a.cmd=="verify" and out.get("status")!="PASS": raise SystemExit(2)
    if a.cmd=="finish" and not out.get("allow"): raise SystemExit(2)

if __name__=="__main__":
    main()
