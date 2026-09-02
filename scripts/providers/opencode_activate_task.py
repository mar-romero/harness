#!/usr/bin/env python3
"""Bind one durable task to the OpenCode harness runtime.

This adapter is intentionally provider-specific. It reuses the canonical task
router, context compiler and v3 model router, then writes a small active-task
file consumed by the OpenCode plugin. It never edits provider agent files or
application code.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from harnesslib import run_dir, safe_task_id, write_json_atomic  # noqa: E402
from task_router import route  # noqa: E402
from context_compiler import build as build_context  # noqa: E402
from orchestrator import init_progress  # noqa: E402
from request_normalizer import normalize_task  # noqa: E402
try:
    from model_router import load_inventory, selections_for_task  # noqa: E402
except ImportError as exc:
    raise SystemExit("OpenCode overlay requires the v3 model-routing files (scripts/model_router.py and harness/models.json). Apply the v3 changes #9/#10 first.") from exc

RUNTIME = ROOT / ".harness" / "opencode"
INVENTORY = RUNTIME / "model-inventory.json"
ACTIVE = RUNTIME / "active-task.json"


def resolve_task(value: str) -> Path:
    p = Path(value)
    p = p if p.is_absolute() else ROOT / p
    p = p.resolve()
    try:
        p.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise SystemExit("task path must be inside the project workspace") from exc
    if not p.is_file():
        raise SystemExit(f"task file not found: {p}")
    return p


def activate(task_path: Path) -> dict:
    task = json.loads(task_path.read_text(encoding="utf-8"))
    if task.get('request'):
        task = normalize_task(task)
    task_id = safe_task_id(task.get("id", ""))
    routed = route(task)
    out_dir = run_dir(task_id)
    route_path = out_dir / "route.json"
    context_path = out_dir / "context.json"
    models_path = out_dir / "model-selections.json"

    write_json_atomic(route_path, routed)
    progress = init_progress(task_id, routed)
    context = build_context(task, routed)
    write_json_atomic(context_path, context)

    inventory, inventory_path = load_inventory("opencode", str(INVENTORY) if INVENTORY.exists() else None)
    selections = selections_for_task(task, "opencode", inventory)
    model_payload = {
        "schema_version": 1,
        "task_id": task_id,
        "provider": "opencode",
        "inventory_path": str(inventory_path) if inventory_path else None,
        "selections": selections,
    }
    write_json_atomic(models_path, model_payload)

    active = {
        "schema_version": 1,
        "activated_at": datetime.now(timezone.utc).isoformat(),
        "task_id": task_id,
        "task_path": task_path.relative_to(ROOT).as_posix(),
        "risk": routed["risk"],
        "route_path": route_path.relative_to(ROOT).as_posix(),
        "context_path": context_path.relative_to(ROOT).as_posix(),
        "model_selections_path": models_path.relative_to(ROOT).as_posix(),
        "agents": routed["agents"],
        "human_gate": routed["human_gate"],
        "progress_path": (out_dir / "progress.json").relative_to(ROOT).as_posix(),
        "progress_state": progress["state"],
        "current_step": progress["current_step"],
        "selections": selections,
    }
    write_json_atomic(ACTIVE, active)
    return active


def main() -> int:
    ap = argparse.ArgumentParser(description="Activate one harness task for the OpenCode plugin runtime.")
    ap.add_argument("task", nargs="?", help="Task JSON path under the project")
    ap.add_argument("--clear", action="store_true", help="Clear the OpenCode active task/model mapping")
    args = ap.parse_args()

    if args.clear:
        ACTIVE.unlink(missing_ok=True)
        print(json.dumps({"cleared": True, "path": str(ACTIVE.relative_to(ROOT))}, indent=2))
        return 0
    if not args.task:
        ap.error("task is required unless --clear is used")

    active = activate(resolve_task(args.task))
    print(json.dumps(active, indent=2, ensure_ascii=False))
    blocked = [x for x in active["selections"] if x.get("action") == "block"]
    return 2 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
