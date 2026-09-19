#!/usr/bin/env python
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, os, time
from contextlib import contextmanager
from pathlib import Path
from harnesslib import run_dir, safe_task_id, sha256_file, write_json_atomic

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
    return _selection(task)[0]


def _attempt_name(number):
    return f"tdd-attempts/{number:04d}.jsonl" if number else "tdd-evidence.jsonl"


def _attempt_path(directory, name):
    candidate = directory / name
    if candidate.is_symlink() or not candidate.resolve().is_relative_to(directory.resolve()):
        raise ValueError("TDD attempt path escapes its run directory or is a symlink")
    return candidate


def _selection(task):
    """Resolve one explicit attempt; never recover a broken selector implicitly."""
    task = safe_task_id(task)
    directory = run_dir(task)
    selector = directory / "tdd-attempts.json"
    if not selector.exists():
        if (directory / "tdd-attempts").exists() or selector.is_symlink():
            raise ValueError("TDD attempt selection missing; refusing legacy fallback")
        return _attempt_path(directory, _attempt_name(0)), None
    manifest = json.loads(selector.read_text(encoding="utf-8"))
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
            or manifest.get("task_id") != task):
        raise ValueError("invalid TDD attempt selection")
    history = manifest.get("superseded")
    if not isinstance(history, list) or not history:
        raise ValueError("TDD attempt selection has no failure provenance")
    for number, failed in enumerate(history):
        if (not isinstance(failed, dict) or failed.get("path") != _attempt_name(number)
                or failed.get("status") != "FAILED"
                or any(not isinstance(failed.get(key), str) or not failed[key].strip()
                       for key in ("reason", "validation_error", "superseded_at"))):
            raise ValueError("invalid TDD failure provenance")
        preserved = _attempt_path(directory, failed["path"])
        if not preserved.is_file() or sha256_file(preserved) != failed.get("sha256"):
            raise ValueError("preserved TDD failure missing or changed")
    if manifest.get("active_attempt") != _attempt_name(len(history)):
        raise ValueError("invalid active TDD attempt reference")
    active = _attempt_path(directory, manifest["active_attempt"])
    if not active.is_file():
        raise ValueError("active TDD attempt missing; refusing legacy fallback")
    return active, manifest


@contextmanager
def _task_lock(task):
    """Serialize one task's evidence-chain transaction across processes."""
    lock_path = run_dir(safe_task_id(task)) / "tdd-evidence.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            unlock = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            unlock = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        try:
            yield
        finally:
            unlock()

def _hash(rec):
    return hashlib.sha256(json.dumps(rec,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()

def read(task):
    p, manifest = _selection(task)
    return _read_chain(p, task, manifest["active_attempt"] if manifest else None)


def _read_chain(p, task, attempt=None):
    if not p.exists(): return []
    rows=[]; prev="GENESIS"
    for n,line in enumerate(p.read_text(encoding="utf-8").splitlines(),1):
        if not line.strip(): continue
        r=json.loads(line)
        if not isinstance(r, dict):
            raise ValueError(f"invalid TDD evidence record at entry {n}")
        stored=r.get("record_hash"); base=dict(r); base.pop("record_hash",None)
        if r.get("prev_hash") != prev or stored != _hash(base):
            raise ValueError(f"invalid TDD evidence chain at entry {n}")
        if r.get("task_id") != task or r.get("attempt") != attempt:
            raise ValueError(f"TDD evidence attempt/task mismatch at entry {n}")
        rows.append(r); prev=stored
    return rows


def supersede(task, reason):
    """Preserve a failed chain in place and atomically select a fresh attempt."""
    task = safe_task_id(task)
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("TDD supersession requires a nonempty reason")
    with _task_lock(task):
        current, manifest = _selection(task)
        if not current.is_file():
            raise ValueError("no failed TDD attempt to supersede")
        reference = manifest["active_attempt"] if manifest else _attempt_name(0)
        try:
            _read_chain(current, task, reference if manifest else None)
        except ValueError as exc:
            validation_error = str(exc)
        else:
            raise ValueError("cannot supersede a valid TDD evidence chain")
        history = list(manifest["superseded"]) if manifest else []
        history.append({
            "path": reference, "status": "FAILED", "sha256": sha256_file(current),
            "validation_error": validation_error, "reason": reason.strip(),
            "superseded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        })
        active = _attempt_name(len(history))
        fresh = _attempt_path(run_dir(task), active)
        fresh.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation preserves orphaned files after interruption. A missing
        # selector with an attempts directory fails closed instead of using legacy.
        with fresh.open("xb") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        selection = {"schema_version": 1, "task_id": task,
                     "active_attempt": active, "superseded": history}
        write_json_atomic(run_dir(task) / "tdd-attempts.json", selection)
        return selection

def append(task, phase, status, actor, claim, command=None, exit_code=None, artifact=None, notes=None):
    task=safe_task_id(task)
    if phase not in PHASES: raise SystemExit("invalid TDD phase")
    if status not in STATUS: raise SystemExit("invalid status")
    expected=EXPECTED_ACTORS.get(phase)
    if expected and actor not in expected:
        raise SystemExit(f"{phase} actor must be one of {sorted(expected)}")
    with _task_lock(task):
        p, manifest = _selection(task)
        attempt = manifest["active_attempt"] if manifest else None
        rows=_read_chain(p, task, attempt); prev=rows[-1]["record_hash"] if rows else "GENESIS"
        rec={
            "timestamp":dt.datetime.now(dt.timezone.utc).isoformat(),
            "task_id":task,"phase":phase,"status":status,"actor":actor,
            "claim":claim,"prev_hash":prev
        }
        if attempt is not None: rec["attempt"] = attempt
        if command is not None: rec["command"]=command
        if exit_code is not None: rec["exit_code"]=exit_code
        if artifact is not None: rec["artifact"]=artifact
        if notes is not None: rec["notes"]=notes
        rec["record_hash"]=_hash(rec)
        p.parent.mkdir(parents=True,exist_ok=True)
        with p.open("a",encoding="utf-8") as f:
            f.write(json.dumps(rec,sort_keys=True,ensure_ascii=False)+"\n")
            f.flush()
            os.fsync(f.fileno())
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
        p, manifest = _selection(task)
        rows=_read_chain(p, task, manifest["active_attempt"] if manifest else None)
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
    return {"allow":not missing and not failing,"required":[f"tdd:{x}" for x in required],"missing":missing,"failing":failing,
            "active_attempt":manifest["active_attempt"] if manifest else _attempt_name(0)}

def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest="cmd",required=True)
    p=sub.add_parser("add"); p.add_argument("task"); p.add_argument("--phase",required=True,choices=sorted(PHASES)); p.add_argument("--status",required=True,choices=sorted(STATUS)); p.add_argument("--actor",required=True); p.add_argument("--claim",required=True); p.add_argument("--command"); p.add_argument("--exit-code",type=int); p.add_argument("--artifact"); p.add_argument("--notes")
    p=sub.add_parser("summary"); p.add_argument("task")
    p=sub.add_parser("finish"); p.add_argument("task")
    p=sub.add_parser("supersede"); p.add_argument("task"); p.add_argument("--reason",required=True)
    a=ap.parse_args()
    if a.cmd=="add":
        print(json.dumps(append(a.task,a.phase,a.status,a.actor,a.claim,a.command,a.exit_code,a.artifact,a.notes),indent=2))
    elif a.cmd=="summary":
        print(json.dumps(read(a.task),indent=2))
    elif a.cmd=="supersede":
        print(json.dumps(supersede(a.task,a.reason),indent=2))
    else:
        d=finish_decision(a.task); print(json.dumps(d,indent=2)); raise SystemExit(0 if d["allow"] else 2)

if __name__=="__main__":
    main()
