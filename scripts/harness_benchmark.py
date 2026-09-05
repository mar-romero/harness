#!/usr/bin/env python3
from __future__ import annotations
import argparse, dataclasses, datetime as dt, html, json, os, re, shutil
import statistics, subprocess, sys, tempfile, time, uuid
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
POLICY_PATH=ROOT/"harness/benchmark-policy.json"
VARIANT_SCRIPT=ROOT/"scripts/benchmark_variant.py"

def now(): return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00","Z")
def load_json(p): return json.loads(Path(p).read_text(encoding="utf-8"))
def write_json(p,d):
    p=Path(p); p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(d,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
def policy(): return load_json(POLICY_PATH)
def resolve(base,value):
    p=Path(value); return (p if p.is_absolute() else base/p).resolve()

def safe_id(v):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,63}",v or ""): raise ValueError(f"invalid id {v!r}")
    return v

def validate_check(c,path):
    if not c.get("name") or not isinstance(c.get("argv"),list) or not c["argv"]:
        raise ValueError(f"{path}: invalid check")
    if any(not isinstance(x,str) for x in c["argv"]): raise ValueError(f"{path}: argv must be strings")

def validate_case(c,path):
    for k in ["schema_version","id","task","workspace","verification"]:
        if k not in c: raise ValueError(f"{path}: missing {k}")
    if c["schema_version"]!=1: raise ValueError(f"{path}: schema_version")
    safe_id(c["id"])
    if not str(c["task"].get("description") or "").strip(): raise ValueError(f"{path}: task.description")
    if not str(c["workspace"].get("source") or "").strip(): raise ValueError(f"{path}: workspace.source")
    for x in c["verification"].get("public_checks",[]): validate_check(x,path)

def load_suite(path):
    path=Path(path).resolve(); s=load_json(path)
    if s.get("schema_version")!=1 or not s.get("name") or not s.get("cases"): raise ValueError("invalid suite")
    out=[]
    for rel in s["cases"]:
        p=resolve(path.parent,rel); c=load_json(p); validate_case(c,p); out.append((p,c))
    return s,out

def copy_repo(src,dst):
    if not src.exists(): raise FileNotFoundError(src)
    if (src/".git").exists():
        cp=subprocess.run(["git","clone","--quiet","--no-hardlinks",str(src),str(dst)],text=True,capture_output=True)
        if cp.returncode: raise RuntimeError(cp.stderr)
    else:
        shutil.copytree(src,dst,ignore=shutil.ignore_patterns("__pycache__",".pytest_cache",".mypy_cache"))
        subprocess.run(["git","init","-q"],cwd=dst,check=True)
        subprocess.run(["git","config","user.email","benchmark@example.invalid"],cwd=dst,check=True)
        subprocess.run(["git","config","user.name","Harness Benchmark"],cwd=dst,check=True)
        subprocess.run(["git","add","."],cwd=dst,check=True)
        subprocess.run(["git","commit","-qm","benchmark base"],cwd=dst,check=True)

def remove_runtime(repo):
    for rel in policy()["workspace"]["strip_runtime_dirs"]:
        p=repo/rel
        if p.is_dir(): shutil.rmtree(p,ignore_errors=True)
        elif p.exists(): p.unlink()

def overlay_harness(repo):
    for rel in policy()["workspace"]["harness_control_paths"]:
        src=ROOT/rel
        if not src.exists(): continue
        dst=repo/rel
        if dst.is_dir(): shutil.rmtree(dst)
        elif dst.exists(): dst.unlink()
        if src.is_dir(): shutil.copytree(src,dst,ignore=shutil.ignore_patterns("__pycache__",".harness",".git"))
        else: dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dst)

def strip_harness(repo):
    for rel in ["AGENTS.md","AI_POLICY.md","harness",".agents",".opencode",".codex"]:
        p=repo/rel
        if p.is_dir(): shutil.rmtree(p,ignore_errors=True)
        elif p.exists(): p.unlink()
    scripts=repo/"scripts"
    if scripts.exists() and (ROOT/"scripts").exists():
        names={x.name for x in (ROOT/"scripts").iterdir() if x.is_file()}
        for n in names:
            p=scripts/n
            if p.exists() and p.is_file(): p.unlink()
    a=repo/".opencode/agents/benchmark-baseline.md"; a.parent.mkdir(parents=True,exist_ok=True)
    a.write_text("""---
description: Benchmark baseline coding agent with no project harness orchestration.
mode: primary
permissions:
  - action: read
    resource: "*"
    effect: allow
  - action: glob
    resource: "*"
    effect: allow
  - action: grep
    resource: "*"
    effect: allow
  - action: list
    resource: "*"
    effect: allow
  - action: edit
    resource: "*"
    effect: allow
  - action: shell
    resource: "*"
    effect: allow
  - action: external_directory
    resource: "*"
    effect: deny
  - action: webfetch
    resource: "*"
    effect: deny
  - action: websearch
    resource: "*"
    effect: deny
  - action: subagent
    resource: "*"
    effect: deny
---
Implement the requested scoped change. Inspect the repository, make the smallest correct edit, run relevant tests/checks, and stop when the requested behavior is satisfied. Do not access files outside the workspace.
""",encoding="utf-8")

def apply_variant(repo,name,cfg):
    if cfg["kind"]=="baseline": strip_harness(repo); return
    if cfg["kind"]=="harness_ablation":
        argv=[sys.executable,str(VARIANT_SCRIPT),str(repo)]
        for x in cfg.get("ablations",[]): argv+=["--ablation",x]
        cp=subprocess.run(argv,cwd=ROOT,text=True,capture_output=True)
        if cp.returncode: raise RuntimeError(cp.stdout+cp.stderr)

def run_argv(argv,cwd,timeout,env=None,stdin_text=None):
    start=time.monotonic()
    try:
        cp=subprocess.run(argv,cwd=cwd,text=True,input=stdin_text,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,env=env,check=False)
        timed_out=False
    except subprocess.TimeoutExpired as e:
        cp=dataclasses.make_dataclass("CP",[("returncode",int),("stdout",str),("stderr",str)])(124,e.stdout or "",e.stderr or "")
        timed_out=True
    maxb=int(policy().get("max_output_bytes",2000000))
    return {"argv":argv,"returncode":cp.returncode,"timed_out":timed_out,
            "duration_seconds":round(time.monotonic()-start,6),
            "stdout":(cp.stdout or "")[-maxb:],"stderr":(cp.stderr or "")[-maxb:]}

def prompt_for(c):
    t=c["task"]; lines=[f"Benchmark task {c['id']}.","",t["description"].strip()]
    if t.get("acceptance_criteria"): lines+=["","Acceptance criteria:"]+[f"- {x}" for x in t["acceptance_criteria"]]
    if t.get("files"): lines+=["","Known relevant files:"]+[f"- {x}" for x in t["files"]]
    lines+=["","Work only inside this repository. Do not look for benchmark or hidden-test files outside the workspace.",
            "Implement the task, run the checks available to you, and leave the workspace with the candidate changes."]
    return "\n".join(lines)

def provider_command(provider,repo,cfg,prompt,model):
    if provider=="opencode":
        agent="benchmark-baseline" if cfg["kind"]=="baseline" else "harness-orchestrator"
        cmd=["opencode","run","--format","json","--dir",str(repo),"--agent",agent,"--auto"]
        if model: cmd+=["--model",model]
        cmd.append(prompt); return cmd,None
    if provider=="codex":
        cmd=["codex","exec","--json","-C",str(repo),"--sandbox","workspace-write","--ephemeral"]
        if model: cmd+=["--model",model]
        cmd.append("-"); return cmd,prompt
    raise ValueError(provider)

IN_KEYS={"input_tokens","inputtokens","prompt_tokens","prompttokens","cached_input_tokens","cache_read_input_tokens"}
OUT_KEYS={"output_tokens","outputtokens","completion_tokens","completiontokens"}
TOTAL_KEYS={"total_tokens","totaltokens"}
COST_KEYS={"cost","cost_usd","costusd","total_cost","totalcost"}
def extract_executor_usage(text):
    v={"input":0.0,"output":0.0,"total":0.0,"cost":0.0}; n=0
    def walk(x):
        if isinstance(x,dict):
            for k,z in x.items():
                nk=re.sub(r"[^a-z0-9_]","",str(k).lower())
                if isinstance(z,(int,float)) and not isinstance(z,bool):
                    if nk in IN_KEYS: v["input"]=max(v["input"],float(z))
                    elif nk in OUT_KEYS: v["output"]=max(v["output"],float(z))
                    elif nk in TOTAL_KEYS: v["total"]=max(v["total"],float(z))
                    elif nk in COST_KEYS: v["cost"]=max(v["cost"],float(z))
                else: walk(z)
        elif isinstance(x,list):
            for z in x: walk(z)
    for line in text.splitlines():
        try: obj=json.loads(line.strip())
        except Exception: continue
        n+=1; walk(obj)
    if not v["total"]: v["total"]=v["input"]+v["output"]
    return {"input_tokens":v["input"],"output_tokens":v["output"],"tokens":v["total"],
            "cost_usd":v["cost"],"json_events_parsed":n,"provenance":"provider-json-best-effort"}

def render_argv(argv,w): return [x.replace("{workspace}",str(w)) for x in argv]
def run_check(c,w,timeout):
    r=run_argv(render_argv(c["argv"],w),w/str(c.get("cwd") or "."),int(c.get("timeout_seconds") or timeout))
    mv=None; m=c.get("metric")
    if m:
        mm=re.search(m["regex"],r["stdout"]+"\n"+r["stderr"],re.M)
        if mm: mv=float(mm.group(1))*float(m.get("scale",1.0))
    return {"name":c["name"],"kind":c.get("kind","acceptance"),"passed":r["returncode"]==0,
            "metric_name":(m or {}).get("name"),"metric_value":mv,**r}

def load_oracle(case_path,c,private_root):
    rel=c["verification"].get("private_oracle")
    if not rel: return None,None,False
    if private_root:
        p=(private_root/c["id"]/Path(rel).name)
        if not p.exists(): p=private_root/rel
        ext=True
    else:
        p=resolve(case_path.parent,rel); ext=False
    p=p.resolve()
    if not p.exists(): raise FileNotFoundError(p)
    d=load_json(p)
    if d.get("schema_version")!=1: raise ValueError("oracle schema_version")
    return p,d,ext

def private_guard_enter(private_root,case_id):
    """Temporarily make the case private directory inaccessible on POSIX.

    This is defense in depth. Provider sandboxing remains the primary isolation
    boundary. The runner restores the original mode before loading hidden tests.
    """
    if not private_root or os.name!="posix":
        return None
    target=(private_root/case_id).resolve()
    if not target.exists() or not target.is_dir():
        return None
    mode=target.stat().st_mode & 0o7777
    target.chmod(0)
    return (target,mode)

def private_guard_exit(guard):
    if guard:
        target,mode=guard
        target.chmod(mode)

def inject_hidden(op,oracle,w):
    for x in oracle.get("files",[]):
        src=resolve(op.parent,x["source"]); dst=(w/x["destination"]).resolve()
        dst.relative_to(w.resolve()); dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dst)

def setup_case(case_path,c,vname,vcfg,workroot):
    w=workroot/c["id"]/vname/uuid.uuid4().hex[:10]
    copy_repo(resolve(case_path.parent,c["workspace"]["source"]),w); remove_runtime(w)
    if vcfg["kind"]!="baseline" and c["workspace"].get("harness_overlay",False): overlay_harness(w)
    apply_variant(w,vname,vcfg)
    for argv in c["workspace"].get("setup_commands",[]):
        r=run_argv(render_argv(argv,w),w,120)
        if r["returncode"]: raise RuntimeError(f"setup failed {r}")
    return w

def git_changed(w):
    cp=subprocess.run(["git","status","--porcelain"],cwd=w,text=True,capture_output=True)
    return sorted({line[3:].strip() for line in cp.stdout.splitlines() if len(line)>=4})

def harness_metrics(w,case_id,oracle):
    rd=w/".harness/runs"/case_id
    out={"first_pass":None,"tdd_valid_red":None,"impact_recall":None,"human_intervention":0.0,"agents_used":None}
    p=rd/"progress.json"
    if p.exists():
        try:
            d=load_json(p); attempts=d.get("attempts") or {}; out["first_pass"]=1.0 if int(attempts.get("IMPLEMENT",0))<=1 and int(d.get("failures",0))==0 else 0.0
        except Exception: pass
    p=rd/"tdd-evidence.jsonl"
    if p.exists():
        seen=False; valid=False
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line.strip(): continue
                r=json.loads(line)
                if r.get("phase")=="red":
                    seen=True
                    if r.get("status")=="PASS" and isinstance(r.get("exit_code"),int) and r["exit_code"]!=0: valid=True
            out["tdd_valid_red"]=1.0 if valid else (0.0 if seen else None)
        except Exception: pass
    p=rd/"impact.json"; truth=set((oracle or {}).get("impacted_files") or [])
    if p.exists() and truth:
        try:
            d=load_json(p); pred=set(d.get("planned_surface") or [])|set(d.get("related_tests") or [])
            out["impact_recall"]=len(pred&truth)/len(truth)
        except Exception: pass
    p=rd/"evidence.jsonl"
    if p.exists():
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip() and json.loads(line).get("category")=="human_approval": out["human_intervention"]=1.0
        except Exception: pass
    p=rd/"agent-budget.json"
    if p.exists():
        try:
            d=load_json(p); out["agents_used"]=float(len(set(d.get("current_agents") or [])|set(d.get("mandatory_gate_agents") or [])))
        except Exception: pass
    elif (rd/"route.json").exists():
        try: out["agents_used"]=float(len(load_json(rd/"route.json").get("agents") or []))
        except Exception: pass
    return out

def execute_one(case_path,c,provider,vname,vcfg,rep,workroot,private_root,model,timeout,keep):
    w=setup_case(case_path,c,vname,vcfg,workroot)
    cmd,stdin_text=provider_command(provider,w,vcfg,prompt_for(c),model)
    env=os.environ.copy(); env.update({"HARNESS_BENCHMARK_MODE":"1","HARNESS_BENCHMARK_VARIANT":vname,"HARNESS_BENCHMARK_CASE":c["id"]})
    guard=private_guard_enter(private_root,c["id"])
    try:
        ex=run_argv(cmd,w,timeout,env,stdin_text)
    finally:
        private_guard_exit(guard)
    usage=extract_executor_usage(ex["stdout"])
    public=[run_check(x,w,timeout) for x in c["verification"].get("public_checks",[])]
    op,oracle,external=load_oracle(case_path,c,private_root)
    hidden=[]
    if oracle:
        inject_hidden(op,oracle,w); hidden=[run_check(x,w,timeout) for x in oracle.get("checks",[])]
    pub_ok=all(x["passed"] for x in public) if public else True
    hid_ok=all(x["passed"] for x in hidden) if hidden else True
    suc=(ex["returncode"]==0 and pub_ok and hid_ok)
    regression=[x for x in public+hidden if x["kind"]=="regression"]
    mut=[x["metric_value"] for x in public+hidden if x.get("metric_name")=="mutation_score" and isinstance(x.get("metric_value"),(int,float))]
    hm=harness_metrics(w,c["id"],oracle)
    first=hm["first_pass"] if hm["first_pass"] is not None else (1.0 if suc else 0.0)
    row={"schema_version":1,"case_id":c["id"],"variant":vname,"provider":provider,"repeat":rep,"model":model,
         "private_oracle_external":external,"executor":ex,"usage":usage,"public_checks":public,"hidden_checks":hidden,
         "changed_files":git_changed(w),
         "metrics":{"task_success":1.0 if suc else 0.0,"first_pass_success":first,
                    "hidden_test_pass_rate":(sum(x["passed"] for x in hidden)/len(hidden)) if hidden else None,
                    "bug_introduction":1.0 if any(not x["passed"] for x in regression) else 0.0,
                    "mutation_score":statistics.mean(mut) if mut else None,"impact_recall":hm["impact_recall"],
                    "tdd_valid_red":hm["tdd_valid_red"],"human_intervention":hm["human_intervention"],
                    "tokens":usage["tokens"],"cost_usd":usage["cost_usd"],"latency_seconds":ex["duration_seconds"],
                    "agents_used":hm["agents_used"]}}
    retain=keep or (not suc and policy()["workspace"]["keep_on_failure"])
    row["workspace"]=str(w) if retain else None
    if not retain: shutil.rmtree(w,ignore_errors=True)
    return row

METRIC_MAP={"task_success_rate":"task_success","first_pass_success_rate":"first_pass_success",
"hidden_test_pass_rate":"hidden_test_pass_rate","bug_introduction_rate":"bug_introduction",
"mutation_score":"mutation_score","impact_recall":"impact_recall","tdd_valid_red_rate":"tdd_valid_red",
"human_intervention_rate":"human_intervention","tokens":"tokens","cost_usd":"cost_usd",
"latency_seconds":"latency_seconds","agents_used_mean":"agents_used"}

def stat(vals):
    if not vals: return {"n":0,"mean":None,"median":None,"stdev":None}
    return {"n":len(vals),"mean":statistics.mean(vals),"median":statistics.median(vals),
            "stdev":statistics.stdev(vals) if len(vals)>1 else 0.0,"min":min(vals),"max":max(vals)}

def aggregate(rows,variants):
    agg={}
    for v in variants:
        rr=[r for r in rows if r["variant"]==v]; agg[v]={"runs":len(rr),"metrics":{}}
        for outk,ink in METRIC_MAP.items():
            vals=[float(r["metrics"][ink]) for r in rr if isinstance(r["metrics"].get(ink),(int,float)) and not isinstance(r["metrics"].get(ink),bool)]
            agg[v]["metrics"][outk]=stat(vals)
    delta={}
    if "baseline" in agg:
        for v in variants:
            if v=="baseline": continue
            delta[v]={}
            for m in METRIC_MAP:
                a=agg["baseline"]["metrics"][m]["mean"]; b=agg[v]["metrics"][m]["mean"]
                delta[v][m]=None if a is None or b is None else b-a
    return agg,delta

def html_report(p):
    vs=p["variants"]; metrics=list(METRIC_MAP)
    def fmt(v,m):
        if v is None: return "—"
        if m in {"tokens","cost_usd","latency_seconds","agents_used_mean"}: return f"{v:,.3f}"
        return f"{v*100:.1f}%"
    header="".join(f"<th>{html.escape(v)}</th>" for v in vs)
    body="".join("<tr><th>"+html.escape(m)+"</th>"+"".join(f"<td>{fmt(p['aggregate'][v]['metrics'][m]['mean'],m)}</td>" for v in vs)+"</tr>" for m in metrics)
    runs="".join(f"<tr><td>{html.escape(r['case_id'])}</td><td>{html.escape(r['variant'])}</td><td>{r['repeat']}</td><td>{'PASS' if r['metrics']['task_success'] else 'FAIL'}</td><td>{r['metrics']['tokens']:.0f}</td><td>{r['metrics']['latency_seconds']:.2f}s</td></tr>" for r in p["runs"])
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Harness benchmark</title>
<style>body{{font-family:system-ui;max-width:1300px;margin:32px auto;padding:0 20px;color:#1f2328}}table{{border-collapse:collapse;width:100%;margin:18px 0 32px}}th,td{{border:1px solid #d0d7de;padding:8px;text-align:right}}th:first-child,td:first-child{{text-align:left}}thead th{{background:#f6f8fa}}code{{background:#f6f8fa;padding:2px 5px}}</style></head>
<body><h1>Harness benchmark: {html.escape(p['suite'])}</h1><p>Provider <code>{html.escape(p['provider'])}</code> · repetitions {p['repetitions']}</p>
<h2>Aggregate</h2><table><thead><tr><th>Metric</th>{header}</tr></thead><tbody>{body}</tbody></table>
<h2>Runs</h2><table><thead><tr><th>Case</th><th>Variant</th><th>Repeat</th><th>Verified result</th><th>Tokens</th><th>Latency</th></tr></thead><tbody>{runs}</tbody></table></body></html>"""

def record_eval(payload,variant,report_dir):
    metrics={m:float(s["mean"]) for m,s in payload["aggregate"][variant]["metrics"].items() if s["mean"] is not None}
    mp=report_dir/f"eval-recorder-metrics-{variant}.json"; write_json(mp,metrics)
    cp=subprocess.run([sys.executable,str(ROOT/"scripts/eval_recorder.py"),"--label",f"benchmark-{payload['suite']}-{variant}","--metrics",str(mp)],cwd=ROOT,text=True,capture_output=True)
    return {"returncode":cp.returncode,"metrics_path":str(mp),"stdout":cp.stdout,"stderr":cp.stderr}

def main():
    ap=argparse.ArgumentParser()
    sub=ap.add_subparsers(dest="cmd",required=True)
    p=sub.add_parser("validate"); p.add_argument("suite")
    p=sub.add_parser("run"); p.add_argument("suite"); p.add_argument("--provider",choices=["opencode","codex"],default="opencode")
    p.add_argument("--variants",default="baseline,full"); p.add_argument("--repetitions",type=int); p.add_argument("--timeout",type=int)
    p.add_argument("--model"); p.add_argument("--private-root"); p.add_argument("--keep-workspaces",action="store_true")
    p.add_argument("--record-eval",action="store_true"); p.add_argument("--record-variant")
    p=sub.add_parser("report"); p.add_argument("report"); p.add_argument("--output")
    a=ap.parse_args()
    if a.cmd=="validate":
        s,c=load_suite(a.suite); print(json.dumps({"ok":True,"suite":s["name"],"cases":[x["id"] for _,x in c]},indent=2)); return 0
    if a.cmd=="report":
        rp=Path(a.report).resolve(); out=Path(a.output).resolve() if a.output else rp.with_suffix(".html")
        out.write_text(html_report(load_json(rp)),encoding="utf-8"); print(out); return 0
    s,cases=load_suite(a.suite); pol=policy(); variants=[x.strip() for x in a.variants.split(",") if x.strip()]
    bad=[v for v in variants if v not in pol["variants"]]
    if bad: raise SystemExit(f"unknown variants {bad}")
    reps=a.repetitions or pol["default_repetitions"]; timeout=a.timeout or pol["default_timeout_seconds"]
    rid=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+uuid.uuid4().hex[:8]
    rd=ROOT/".harness/benchmarks/runs"/rid; rd.mkdir(parents=True,exist_ok=True)
    wr=Path(tempfile.mkdtemp(prefix=f"harness-bench-{rid}-")); private=Path(a.private_root).resolve() if a.private_root else None
    rows=[]
    for cp,c in cases:
        for v in variants:
            for rep in range(1,reps+1):
                print(f"[benchmark] {c['id']} variant={v} repeat={rep}/{reps}",flush=True)
                row=execute_one(cp,c,a.provider,v,pol["variants"][v],rep,wr,private,a.model,timeout,a.keep_workspaces)
                rows.append(row); write_json(rd/"runs"/f"{c['id']}--{v}--{rep}.json",row)
    if not a.keep_workspaces and not any(r.get("workspace") for r in rows): shutil.rmtree(wr,ignore_errors=True)
    agg,delta=aggregate(rows,variants)
    payload={"schema_version":1,"run_id":rid,"created_at":now(),"suite":s["name"],"provider":a.provider,"model":a.model,
             "variants":variants,"repetitions":reps,"private_root_used":bool(private),"aggregate":agg,"delta_vs_baseline":delta,"runs":rows}
    write_json(rd/"report.json",payload); (rd/"report.html").write_text(html_report(payload),encoding="utf-8")
    ev=None
    if a.record_eval:
        ev=record_eval(payload,a.record_variant or pol["record_variant_default"],rd); write_json(rd/"eval-recorder-result.json",ev)
    print(json.dumps({"run_id":rid,"report_json":str(rd/"report.json"),"report_html":str(rd/"report.html"),"eval_recorder":ev},indent=2))
    return 0

if __name__=="__main__":
    try: raise SystemExit(main())
    except KeyboardInterrupt: raise SystemExit(130)
