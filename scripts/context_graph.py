#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re
from pathlib import Path
from harnesslib import ROOT, load_json, write_json_atomic, run_dir

IMPORT_PATTERNS=[re.compile(r'^\s*from\s+([\w.]+)\s+import',re.M),re.compile(r'^\s*import\s+([\w.]+)',re.M),re.compile(r'from\s+["\']([^"\']+)["\']'),re.compile(r'require\(["\']([^"\']+)["\']\)')]
EXTS=['.py','.js','.ts','.tsx','.jsx','.go','.rs','.java','.kt','.cs','.rb','.php','.swift']
def candidates():
    p=load_json('harness/context-policy.json'); out=[]
    for x in ROOT.rglob('*'):
        if not x.is_file() or x.suffix.lower() not in EXTS: continue
        rel=x.relative_to(ROOT).as_posix()
        if any(rel==d or rel.startswith(d.rstrip('/')+'/') for d in p['exclude_dirs']): continue
        out.append(rel)
    return sorted(out)
def resolve_import(src,target,known):
    s=Path(src); options=[]
    if target.startswith('.'):
        base=s.parent; t=target.lstrip('./').replace('.','/')
        options += [(base/t).as_posix(),(base/(t+'.py')).as_posix(),(base/t/'__init__.py').as_posix()]
    else:
        t=target.replace('.','/'); options += [t,t+'.py',t+'/__init__.py']
        options += [(s.parent/target).as_posix(),(s.parent/(target+'.py')).as_posix()]
    for o in options:
        for ext in ['',*EXTS]:
            c=o if o.endswith(ext) or not ext else o+ext
            if c in known: return c
    return None
def build_graph():
    files=candidates(); known=set(files); edges={f:set() for f in files}
    for f in files:
        try: text=(ROOT/f).read_text(encoding='utf-8',errors='ignore')[:250000]
        except OSError: continue
        for pat in IMPORT_PATTERNS:
            for m in pat.finditer(text):
                r=resolve_import(f,m.group(1),known)
                if r and r!=f: edges[f].add(r)
    # Test affinity and reverse references.
    stems={Path(f).stem.replace('test_','').replace('_test',''):f for f in files}
    for f in files:
        p=Path(f); stem=p.stem.replace('test_','').replace('_test','')
        if ('test' in p.parts or p.stem.startswith('test_') or p.stem.endswith('_test')):
            for other in files:
                if other!=f and Path(other).stem==stem: edges[f].add(other); edges[other].add(f)
    return {k:sorted(v) for k,v in edges.items() if v}
def neighborhood(graph,seeds,depth=2):
    seen=set(seeds); frontier=set(seeds)
    reverse={}
    for a,bs in graph.items():
        for b in bs: reverse.setdefault(b,set()).add(a)
    for _ in range(depth):
        nxt=set()
        for x in frontier: nxt.update(graph.get(x,[])); nxt.update(reverse.get(x,[]))
        nxt-=seen; seen|=nxt; frontier=nxt
        if not frontier: break
    return sorted(seen-set(seeds))
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--task-id'); ap.add_argument('--output'); a=ap.parse_args(); g=build_graph(); payload={'schema_version':1,'nodes':len(candidates()),'edges':sum(len(v) for v in g.values()),'graph':g}
    dest=Path(a.output) if a.output else (run_dir(a.task_id)/'context-graph.json' if a.task_id else ROOT/'.harness/context-graph.json'); dest=dest if dest.is_absolute() else ROOT/dest; write_json_atomic(dest,payload); print(json.dumps({'output':str(dest),'nodes':payload['nodes'],'edges':payload['edges']},indent=2))
if __name__=='__main__': main()
