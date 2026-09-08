#!/usr/bin/env python3
"""Inject active Codex task context at session and subagent boundaries."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def audit_session_boundary(payload: dict) -> None:
    """Create durable proof that the Codex audit hook was active.

    Record only lifecycle metadata: never prompts, tool arguments, or context.
    """
    audit = ROOT / ".harness" / "codex" / "permission-audit.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "at": datetime.now(timezone.utc).isoformat(),
        "event": payload.get("hook_event_name"),
        "session_id": payload.get("session_id"),
        "decision": "session_boundary",
        "reason": "Codex harness audit initialized",
    }
    with audit.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def _context() -> str | None:
    active_path = ROOT / ".harness" / "codex" / "active-task.json"
    if not active_path.is_file():
        return None
    active = json.loads(active_path.read_text(encoding="utf-8"))
    required = ("task_id", "risk", "route_path", "context_path", "progress_path", "task_snapshot_path")
    if any(not active.get(key) for key in required):
        raise ValueError("active Codex task binding is incomplete")
    for key in ("route_path", "context_path", "progress_path", "task_snapshot_path"):
        artifact = (ROOT / active[key]).resolve()
        artifact.relative_to(ROOT.resolve())
        if not artifact.is_file():
            raise ValueError(f"active Codex artifact is missing: {active[key]}")
    return (
        f"Harness runtime: task={active['task_id']}; risk={active['risk']}; "
        f"route={active['route_path']}; context={active['context_path']}; "
        f"progress={active['progress_path']}; snapshot={active['task_snapshot_path']}. "
        "Treat these immutable runtime artifacts as authoritative and respect your role boundary."
    )


def main() -> int:
    payload = json.load(sys.stdin)
    event = payload.get("hook_event_name")
    if event not in {"SessionStart", "SubagentStart"}:
        return 0
    try:
        audit_session_boundary(payload)
        context = _context()
        if context:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": event,
                "additionalContext": context,
            }}))
    except Exception as exc:
        print(json.dumps({"systemMessage": f"Codex harness context hook failed closed: {exc}"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
