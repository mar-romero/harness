#!/usr/bin/env python
"""Codex ``PreToolUse`` bridge for the canonical safety gates.

The hook deliberately blocks only unsafe operations. Safe operations return no
allow decision, preserving Codex's own approval and sandbox controls.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gate import command_decision, path_decision


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / ".harness" / "codex" / "permission-audit.jsonl"
PATCH_PATH = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+?)\s*$", re.MULTILINE)


def _allowed_subagents() -> frozenset[str]:
    """Load the canonical role allowlist rather than duplicating it in a hook."""
    manifest = json.loads((ROOT / "harness" / "manifest.yaml").read_text(encoding="utf-8"))
    agents = manifest.get("agents")
    if not isinstance(agents, dict) or not all(isinstance(name, str) for name in agents):
        raise ValueError("harness manifest has no valid agent allowlist")
    return frozenset(agents)


def _requested_agent_type(tool_input: dict[str, Any]) -> str | None:
    """Extract the stable agent profile argument from a local Agent call."""
    for key in ("agent_type", "agent", "profile", "name"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _patch_paths(command: str) -> list[str]:
    return [match.group(1).strip() for match in PATCH_PATH.finditer(command)]


def evaluate(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return a blocking gate decision, or ``None`` to defer to Codex."""
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return {"allow": False, "reason": "Codex hook payload has no tool_input"}

    if tool_name == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str) or not command.strip():
            return {"allow": False, "reason": "Codex Bash hook payload has no command"}
        decision = command_decision(command)
        return decision if not decision.get("allow") else None

    if tool_name == "apply_patch":
        command = tool_input.get("command")
        if not isinstance(command, str):
            return {"allow": False, "reason": "Codex apply_patch hook payload has no patch command"}
        paths = _patch_paths(command)
        if not paths:
            return {"allow": False, "reason": "Codex apply_patch paths could not be determined"}
        for path in paths:
            decision = path_decision(path)
            if not decision.get("allow"):
                return decision
        return None

    if tool_name == "Agent":
        agent_type = _requested_agent_type(tool_input)
        if agent_type is None:
            return {"allow": False, "reason": "Codex Agent hook payload has no agent type"}
        if agent_type not in _allowed_subagents():
            return {
                "allow": False,
                "reason": f"Codex subagent '{agent_type}' is not allowlisted by harness/manifest.yaml",
            }
        return None

    return None


def _audit(payload: dict[str, Any], decision: dict[str, Any] | None) -> None:
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "at": datetime.now(timezone.utc).isoformat(),
        "event": payload.get("hook_event_name"),
        "tool_name": payload.get("tool_name"),
        "requested_agent_type": _requested_agent_type(payload.get("tool_input", {}))
        if payload.get("tool_name") == "Agent" and isinstance(payload.get("tool_input"), dict)
        else None,
        "session_id": payload.get("session_id"),
        "decision": "deny" if decision else "defer",
        "reason": decision.get("reason") if decision else None,
    }
    with AUDIT.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def emit(decision: dict[str, Any] | None) -> None:
    if decision is None:
        return
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": decision.get("reason", "Blocked by repository policy."),
        }
    }))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("Codex hook payload must be an object")
        decision = evaluate(payload)
        _audit(payload, decision)
        emit(decision)
        return 0
    except Exception as exc:
        # A malformed hook must not turn into an implicit authorization.
        emit({"allow": False, "reason": f"Codex safety hook failed closed: {exc}"})
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
