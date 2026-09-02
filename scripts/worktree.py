#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, shutil
from pathlib import Path
from harnesslib import ROOT, git, safe_task_id, write_json_atomic

def lock(task): return ROOT/'.harness'/'locks'/f'{task}.json'
def wt(task): return ROOT/'.worktrees'/task

def ensure_git():
    r=git('rev-parse','--is-inside-work-tree',check=False)
    if r.returncode!=0: raise SystemExit('not inside a git repository; initialize git before creating worktrees')

def create(task,base='HEAD',execute=False):
    task=safe_task_id(task); ensure_git(); path=wt(task); lk=lock(task)
    if lk.exists(): raise SystemExit(f'writer lock already exists: {lk}')
    branch=f'agent/{task}'
    cmd=['worktree','add','-b',branch,str(path),base]
    if execute:
        path.parent.mkdir(parents=True,exist_ok=True); git(*cmd)
        write_json_atomic(lk,{'task_id':task,'worktree':str(path),'branch':branch,'pid':os.getpid()})
    return {'execute':execute,'command':['git',*cmd],'worktree':str(path),'branch':branch}

def status(task):
    task=safe_task_id(task); path=wt(task); lk=lock(task)
    d={'task_id':task,'exists':path.exists(),'lock':lk.exists()}
    if path.exists():
        r=git('status','--porcelain',cwd=path,check=False); d['dirty']=bool(r.stdout.strip()); d['status']=r.stdout.splitlines()
    return d

def remove(task,execute=False,force=False):
    task=safe_task_id(task); d=status(task)
    if d.get('dirty') and not force: raise SystemExit('worktree has uncommitted changes; refuse removal without --force')
    cmd=['worktree','remove',str(wt(task))] + (['--force'] if force else [])
    if execute:
        git(*cmd); lock(task).unlink(missing_ok=True)
    return {'execute':execute,'command':['git',*cmd]}

def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('create'); p.add_argument('task'); p.add_argument('--base',default='HEAD'); p.add_argument('--execute',action='store_true')
    p=sub.add_parser('status'); p.add_argument('task')
    p=sub.add_parser('remove'); p.add_argument('task'); p.add_argument('--execute',action='store_true'); p.add_argument('--force',action='store_true')
    a=ap.parse_args(); out=create(a.task,a.base,a.execute) if a.cmd=='create' else status(a.task) if a.cmd=='status' else remove(a.task,a.execute,a.force); print(json.dumps(out,indent=2))
if __name__=='__main__': main()
