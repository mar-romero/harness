#!/usr/bin/env python3
from __future__ import annotations
import argparse, fnmatch, json, math, re
from pathlib import Path
from harnesslib import ROOT, load_json, safe_task_id, sha256_file, write_json_atomic, run_dir
from context_graph import build_graph, neighborhood
from memory import search as search_memory

def excluded(rel,policy):
    s=rel.as_posix()
    if any(s==d or s.startswith(d.rstrip('/')+'/') for d in policy['exclude_dirs']): return True
    return any(fnmatch.fnmatch(rel.name,g) or fnmatch.fnmatch(s,g) for g in policy['exclude_globs'])
def tokens(text): return {t for t in re.findall(r'[a-zA-Z0-9_\-]{3,}',text.lower()) if t not in {'this','that','with','from','into','para','como','esta','este'}}
def estimate_tokens(size): return max(1,math.ceil(size/4))

def is_test_path(value: str) -> bool:
    p = Path(value)
    parts = [part.lower() for part in p.parts]
    stem = p.stem.lower()

    return (
        any(part in {"test", "tests", "spec", "specs"} for part in parts[:-1])
        or stem.startswith(("test_", "spec_"))
        or stem.endswith(("_test", "_spec"))
    )

def build(task,route=None):
    p=load_json('harness/context-policy.json'); task_id=safe_task_id(task['id']); req=task.get('request') or {}; query=req.get('canonical_english') or task.get('description',''); wanted=tokens(query)|set(map(str.lower,task.get('tags',[]))); explicit={Path(x).as_posix() for x in task.get('files',[])}
    graph=build_graph(); neighbors=set(neighborhood(graph,explicit,int(p.get('graph_neighbor_depth',2)))) if explicit else set(); candidates=[]
    for path in ROOT.rglob('*'):
        if not path.is_file(): continue
        rel=path.relative_to(ROOT)
        if excluded(rel,p): continue
        if path.suffix.lower() not in p['text_extensions'] and rel.as_posix() not in p['always_include'] and rel.as_posix() not in explicit: continue
        size=path.stat().st_size
        if size>p['max_file_bytes'] and rel.as_posix() not in explicit: continue
        score=0; reasons=[]; r=rel.as_posix(); low=r.lower()
        if r in p['always_include']: score+=1000; reasons.append('policy')
        if r in explicit: score+=900; reasons.append('explicit')
        if r in neighbors: score+=int(p.get('graph_neighbor_bonus',350)); reasons.append('dependency-graph-neighbor')
        overlap=wanted & tokens(low)
        if overlap: score+=20*len(overlap); reasons.append('path-token:'+','.join(sorted(overlap)[:5]))
        if is_test_path(r):
            score += 5
        if score: candidates.append((score,size,r,reasons))
    candidates.sort(key=lambda x:(-x[0],x[2])); selected=[]; total=0; total_tokens=0
    for score,size,r,reasons in candidates:
        est=estimate_tokens(size)
        if len(selected)>=p['max_files']: break
        if total+size>p['max_total_bytes'] and score<900: continue
        if total_tokens+est>p.get('max_total_tokens_estimate',10**9) and score<900: continue
        path=ROOT/r; selected.append({'path':r,'bytes':size,'estimated_tokens':est,'sha256':sha256_file(path),'reason':reasons,'score':score}); total+=size; total_tokens+=est
    memories=search_memory(query,int(p.get('max_memory_items',5)))
    return {'task_id':task_id,'policy_version':p['version'],'route':route or {},'files':selected,'memory':memories,'graph_neighbors':sorted(neighbors),'total_bytes':total,'estimated_tokens':total_tokens,'limits':{'files':p['max_files'],'bytes':p['max_total_bytes'],'estimated_tokens':p.get('max_total_tokens_estimate')}}
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('task'); ap.add_argument('--route'); ap.add_argument('--output'); a=ap.parse_args(); task=json.loads(Path(a.task).read_text(encoding='utf-8')); route=json.loads(Path(a.route).read_text()) if a.route else None; out=build(task,route); dest=Path(a.output) if a.output else run_dir(task['id'])/'context.json'; dest=dest if dest.is_absolute() else ROOT/dest; write_json_atomic(dest,out); print(json.dumps(out,indent=2,ensure_ascii=False))
if __name__=='__main__': main()
