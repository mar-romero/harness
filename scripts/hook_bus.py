#!/usr/bin/env python3
"""Provider-neutral harness hook bus.

Provider-native hooks remain useful defense in depth, but this module is the
canonical lifecycle/audit authority for subscription runs and ACI calls. It does
not pretend to intercept opaque provider-internal tool calls. Instead it:
- validates the agent boundary before a run,
- audits every ACI tool invocation,
- checks read-only integrity after a run,
- validates every writer-changed path against the canonical gate policy before
  the worktree can be published.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harnesslib import ROOT, run_dir
from gate import path_decision

POLICY_PATH = ROOT / "harness" / "hook-bus-policy.json"
_AUDIT_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _policy() -> dict[str, Any]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def _task_id(explicit: str | None = None) -> str | None:
    return explicit or os.environ.get("HARNESS_TASK_ID") or None


def _audit(event: str, payload: dict[str, Any], *, task_id: str | None = None) -> None:
    if os.environ.get("HARNESS_HOOK_AUDIT") == "0" or not _policy().get("audit", True):
        return
    tid = _task_id(task_id)
    if not tid:
        return
    path = run_dir(tid) / "hook-events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"time": _now(), "event": event, **payload}
    # Parallel read-only gates can emit hook events concurrently. Serialize each
    # JSONL append so audit records cannot interleave at the byte level.
    with _AUDIT_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def pre_agent(*, task_id: str, role: str, mode: str, cwd: Path, provider: str) -> dict[str, Any]:
    allowed = True
    reason = "agent boundary valid"
    if mode == "writer":
        expected = (ROOT / ".worktrees" / task_id).resolve()
        if cwd.resolve() != expected:
            allowed = False
            reason = "writer must execute in the task worktree"
    payload = {"allow": allowed, "task_id": task_id, "role": role, "mode": mode, "provider": provider, "cwd": str(cwd), "reason": reason}
    _audit("pre_agent", payload, task_id=task_id)
    return payload


def _changed_paths(cwd: Path) -> list[str]:
    proc = subprocess.run(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=cwd, capture_output=True, check=False)
    if proc.returncode != 0:
        return []
    raw = proc.stdout.decode("utf-8", errors="replace").split("\0")
    out: list[str] = []
    for row in raw:
        if len(row) < 4:
            continue
        value = row[3:]
        if " -> " in value:
            value = value.rsplit(" -> ", 1)[-1]
        value = value.strip().strip('"')
        if value:
            out.append(value.replace("\\", "/"))
    return list(dict.fromkeys(out))


def post_agent(*, task_id: str, role: str, mode: str, cwd: Path, provider: str, exit_code: int, read_only_changed: bool = False) -> dict[str, Any]:
    cfg = _policy()
    violations: list[dict[str, Any]] = []
    changed_paths = _changed_paths(cwd) if mode == "writer" else []
    if mode == "writer":
        for rel in changed_paths:
            # Worktree paths are repository-relative. gate.path_decision resolves
            # against ROOT, which intentionally applies the same canonical
            # protected/secret-path policy used by native provider hooks.
            decision = path_decision(rel)
            if not decision.get("allow"):
                violations.append({"path": rel, **decision})
    allow = not read_only_changed and not violations
    final_exit = int(exit_code)
    if read_only_changed:
        final_exit = int(cfg.get("read_only_mutation_exit_code", 73))
    elif violations and cfg.get("fail_closed_on_writer_path_violation", True):
        final_exit = int(cfg.get("writer_violation_exit_code", 74))
    payload = {
        "allow": allow,
        "task_id": task_id,
        "role": role,
        "mode": mode,
        "provider": provider,
        "exit_code": final_exit,
        "changed_paths": changed_paths,
        "violations": violations,
    }
    _audit("post_agent", payload, task_id=task_id)
    return payload


def pre_tool(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    payload = {"allow": True, "tool": tool, "role": os.environ.get("HARNESS_ROLE"), "provider": os.environ.get("HARNESS_RUNTIME_PROVIDER"), "arguments": arguments}
    _audit("pre_tool", payload)
    return payload


def post_tool(tool: str, result: dict[str, Any]) -> None:
    summary = {"tool": tool, "ok": bool(result.get("ok")), "role": os.environ.get("HARNESS_ROLE"), "provider": os.environ.get("HARNESS_RUNTIME_PROVIDER"), "error": result.get("error")}
    _audit("post_tool", summary)
