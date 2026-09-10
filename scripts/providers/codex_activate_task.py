#!/usr/bin/env python
"""Bind one durable task to Codex and materialize per-agent model/effort TOMLs.

Codex custom agents can override `model` and `model_reasoning_effort`. This
activation reads the operator-generated local scored inventory, writes the durable
selection, then recompiles generated adapters. It never calls OpenRouter.
`compile_harness.py` reads the active binding and injects only selected values.
Clearing the task restores normal inheritance.
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
from impact_analysis import (  # noqa: E402
    build_plan as build_impact_plan,
    capture_baseline as capture_impact_baseline,
)
from agent_budget import init as init_agent_budget  # noqa: E402
from request_normalizer import normalize_task  # noqa: E402
from model_router import load_inventory, selections_for_task  # noqa: E402
from openrouter_sync import discover_provider, load_provider_config  # noqa: E402
from compile_harness import compile_all  # noqa: E402

RUNTIME = ROOT / ".harness" / "codex"
ENRICHED_INVENTORY = ROOT / ".harness" / "model-inventories" / "codex.json"
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


def _select_inventory() -> tuple[dict | None, Path | None, dict]:
    status = {
        "mode": "manual-openrouter-sync",
        "network_refresh": False,
        "inventory_exists": ENRICHED_INVENTORY.exists(),
        "availability_filtered": False,
        "available_models": None,
    }
    if not ENRICHED_INVENTORY.exists():
        status["reason"] = "scored inventory missing; run python scripts/openrouter_sync.py --provider codex"
        return None, None, status
    inventory, path = load_inventory("codex", str(ENRICHED_INVENTORY))
    try:
        cfg = load_provider_config("codex")
        available = {row["id"] for row in discover_provider("codex", cfg) if row.get("enabled", True)}
    except Exception as exc:
        status["availability_error"] = str(exc)
        available = set()
    if available:
        original = list(inventory.get("models", []))
        inventory = dict(inventory)
        inventory["models"] = [m for m in original if m.get("id") in available]
        status["availability_filtered"] = True
        status["available_models"] = len(available)
        status["scored_models_after_filter"] = len(inventory["models"])
    return inventory, path, status


def activate(task_path: Path) -> dict:
    task = json.loads(task_path.read_text(encoding="utf-8"))
    if task.get("request"):
        task = normalize_task(task)
    task_id = safe_task_id(task.get("id", ""))
    routed = route(task)
    out_dir = run_dir(task_id)
    route_path = out_dir / "route.json"
    context_path = out_dir / "context.json"
    models_path = out_dir / "model-selections.json"
    task_snapshot_path = out_dir / "task.json"

    # Freeze the authorized task surface before generating any durable runtime
    # artifact. Consumers must never trust a subsequently edited tasks/*.json.
    write_json_atomic(task_snapshot_path, task)
    write_json_atomic(route_path, routed)
    progress = init_progress(task_id, routed)
    context = build_context(task, routed)
    write_json_atomic(context_path, context)

    # HARNESS_IMPACT_BUDGET_ACTIVATION
    capture_impact_baseline(task_id)
    baseline_path = out_dir / "impact-baseline.json"
    impact = build_impact_plan(task, routed, context)
    impact_path = out_dir / "impact.json"
    write_json_atomic(impact_path, impact)
    agent_budget = init_agent_budget(task, routed)
    budget_path = out_dir / "agent-budget.json"

    inventory, inventory_path, inventory_status = _select_inventory()
    selections = selections_for_task(task, "codex", inventory)
    model_payload = {
        "schema_version": 2,
        "task_id": task_id,
        "provider": "codex",
        "inventory_path": str(inventory_path) if inventory_path else None,
        "inventory_status": inventory_status,
        "openrouter_fetch_error": inventory.get("openrouter_fetch_error") if inventory else None,
        "selections": selections,
    }
    write_json_atomic(models_path, model_payload)

    active = {
        "schema_version": 2,
        "activated_at": datetime.now(timezone.utc).isoformat(),
        "task_id": task_id,
        "task_path": task_path.relative_to(ROOT).as_posix(),
        "task_snapshot_path": task_snapshot_path.relative_to(ROOT).as_posix(),
        "impact_baseline_path": baseline_path.relative_to(ROOT).as_posix(),
        "risk": routed["risk"],
        "route_path": route_path.relative_to(ROOT).as_posix(),
        "context_path": context_path.relative_to(ROOT).as_posix(),
        "impact_path": impact_path.relative_to(ROOT).as_posix(),
        "agent_budget_path": budget_path.relative_to(ROOT).as_posix(),
        "current_agents": agent_budget.get("current_agents", []),
        "mandatory_gate_agents": agent_budget.get("mandatory_gate_agents", []),
        "model_selections_path": models_path.relative_to(ROOT).as_posix(),
        "agents": routed["agents"],
        "human_gate": routed["human_gate"],
        "progress_path": (out_dir / "progress.json").relative_to(ROOT).as_posix(),
        "progress_state": progress["state"],
        "current_step": progress["current_step"],
        "selections": selections,
    }
    write_json_atomic(ACTIVE, active)
    if compile_all(check=False) != 0:
        raise SystemExit("failed to regenerate provider adapters after Codex model activation")
    return active


def clear() -> None:
    ACTIVE.unlink(missing_ok=True)
    if compile_all(check=False) != 0:
        raise SystemExit("failed to restore inherited Codex agent configuration")


def refresh_active() -> dict:
    """Reconcile an active Codex binding with the current local model catalog.

    Codex loads custom-agent TOMLs at session creation. A refresh therefore
    applies to subsequently created sessions/subagents; it never pretends to
    hot-swap a model already running in this turn.
    """
    if not ACTIVE.is_file():
        return {"refreshed": False, "reason": "no active Codex task binding"}
    active = json.loads(ACTIVE.read_text(encoding="utf-8"))
    snapshot_rel = active.get("task_snapshot_path")
    if not isinstance(snapshot_rel, str) or not snapshot_rel:
        raise SystemExit("active Codex binding has no immutable task snapshot")
    snapshot = (ROOT / snapshot_rel).resolve()
    snapshot.relative_to(ROOT.resolve())
    task = json.loads(snapshot.read_text(encoding="utf-8"))
    if task.get("id") != active.get("task_id"):
        raise SystemExit("active Codex binding and immutable task snapshot disagree")
    inventory, inventory_path, inventory_status = _select_inventory()
    selections = selections_for_task(task, "codex", inventory)
    models_path = ROOT / active["model_selections_path"]
    write_json_atomic(models_path, {
        "schema_version": 2,
        "task_id": active["task_id"],
        "provider": "codex",
        "inventory_path": str(inventory_path) if inventory_path else None,
        "inventory_status": inventory_status,
        "openrouter_fetch_error": inventory.get("openrouter_fetch_error") if inventory else None,
        "selections": selections,
    })
    active["selections"] = selections
    active["refreshed_at"] = datetime.now(timezone.utc).isoformat()
    write_json_atomic(ACTIVE, active)
    if compile_all(check=False) != 0:
        raise SystemExit("failed to regenerate Codex agent bindings after refresh")
    return {"refreshed": True, "task_id": active["task_id"], "selections": selections}


def main() -> int:
    ap = argparse.ArgumentParser(description="Activate one harness task for Codex dynamic per-agent routing.")
    ap.add_argument("task", nargs="?")
    ap.add_argument("--clear", action="store_true")
    ap.add_argument("--refresh-active", action="store_true")
    args = ap.parse_args()
    if args.clear:
        clear()
        print(json.dumps({"cleared": True, "path": str(ACTIVE.relative_to(ROOT)), "codex_agents_restored": True}, indent=2))
        return 0
    if args.refresh_active:
        print(json.dumps(refresh_active(), indent=2, ensure_ascii=False))
        return 0
    if not args.task:
        ap.error("task is required unless --clear is used")
    active = activate(resolve_task(args.task))
    print(json.dumps(active, indent=2, ensure_ascii=False))
    return 2 if any(x.get("action") == "block" for x in active["selections"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
