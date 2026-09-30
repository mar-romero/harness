#!/usr/bin/env python
"""Bind one durable task to the Cursor harness runtime.

Cursor exposes no model-list API to the IDE agent, so the host catalog is the set
of model IDs the running Cursor agent can pass to its Task tool. The orchestrator
records that catalog with ``--host-models``; OpenRouter enrichment stays
operator-triggered (``openrouter_sync.py --provider cursor``). Task activation never
performs network calls: it intersects the last scored inventory with the recorded
host catalog and routes one model per agent.
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

from harnesslib import (  # noqa: E402
    adopt_json_immutable, assert_overlay_writable, provider_active_path, provider_catalog_path, provider_enriched_inventory_path,
    provider_model_selections_path, run_dir, runtime_root, safe_task_id, sha256_file, worktree_identity,
    write_json_atomic, write_json_immutable,
)
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

PROVIDER = "cursor"
ENRICHED_INVENTORY = provider_enriched_inventory_path(PROVIDER)
ACTIVE = provider_active_path(PROVIDER)


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


def record_host_models(models: list[str]) -> dict:
    """Persist the Cursor Task-tool model IDs visible in the current session."""
    assert_overlay_writable(PROVIDER)
    ids = list(dict.fromkeys(m.strip() for m in models if m and m.strip() and m.strip() != "inherit"))
    if not ids:
        raise ValueError("at least one Cursor model ID is required")
    snapshot = {
        "schema_version": 1,
        "provider": PROVIDER,
        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": "cursor-task-tool-model-list",
        "models": ids,
    }
    write_json_atomic(provider_catalog_path(PROVIDER), snapshot)
    return snapshot


def _select_inventory() -> tuple[dict | None, Path | None, dict]:
    status = {
        "mode": "manual-openrouter-sync",
        "network_refresh": False,
        "inventory_exists": ENRICHED_INVENTORY.exists(),
        "availability_filtered": False,
        "available_models": None,
    }
    if not ENRICHED_INVENTORY.exists():
        status["reason"] = "scored inventory missing; run python scripts/openrouter_sync.py --provider cursor --cache-only"
        return None, None, status
    inventory, path = load_inventory(PROVIDER, str(ENRICHED_INVENTORY))
    try:
        cfg = load_provider_config(PROVIDER)
        available = {row["id"] for row in discover_provider(PROVIDER, cfg) if row.get("enabled", True)}
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


def _load_or_route(task: dict, route_path: Path) -> dict:
    """Reuse the frozen route after assessment; never silently rewrite it."""
    if route_path.exists():
        frozen = json.loads(route_path.read_text(encoding="utf-8"))
        if frozen.get("task_id") != task.get("id"):
            raise ValueError("frozen route task_id mismatch; refusing activation")
        return frozen
    return route(task)


def activate(task_path: Path) -> dict:
    assert_overlay_writable(PROVIDER)
    task = json.loads(task_path.read_text(encoding="utf-8"))
    if task.get("request"):
        task = normalize_task(task)
    task_id = safe_task_id(task.get("id", ""))
    out_dir = run_dir(task_id)
    route_path = out_dir / "route.json"
    routed = _load_or_route(task, route_path)
    context_path = out_dir / "context.json"
    models_path = provider_model_selections_path(PROVIDER)
    task_snapshot_path = out_dir / "task.json"

    write_json_immutable(task_snapshot_path, task)
    write_json_immutable(route_path, routed)
    progress = init_progress(task_id, routed)
    context = (
        json.loads(context_path.read_text(encoding="utf-8"))
        if context_path.exists()
        else build_context(task, routed)
    )
    context = adopt_json_immutable(context_path, context)

    # HARNESS_IMPACT_BUDGET_ACTIVATION
    capture_impact_baseline(task_id)
    impact_path = out_dir / "impact.json"
    impact = (
        json.loads(impact_path.read_text(encoding="utf-8"))
        if impact_path.exists()
        else build_impact_plan(task, routed, context)
    )
    impact = adopt_json_immutable(impact_path, impact)
    agent_budget = init_agent_budget(task, routed)
    budget_path = out_dir / "agent-budget.json"

    inventory, inventory_path, inventory_status = _select_inventory()
    selections = selections_for_task(task, PROVIDER, inventory)
    model_payload = {
        "schema_version": 2,
        "task_id": task_id,
        "provider": PROVIDER,
        "inventory_path": inventory_path.relative_to(ROOT).as_posix() if inventory_path else None,
        "inventory_sha256": sha256_file(inventory_path) if inventory_path else None,
        "inventory_status": inventory_status,
        "selections": selections,
    }
    write_json_atomic(models_path, model_payload)

    active = {
        "schema_version": 3,
        "provider": PROVIDER,
        "overlay": worktree_identity(),
        "activated_at": datetime.now(timezone.utc).isoformat(),
        "task_id": task_id,
        "task_path": task_path.relative_to(ROOT).as_posix(),
        "task_snapshot_path": task_snapshot_path.relative_to(runtime_root()).as_posix(),
        "risk": routed["risk"],
        "route_path": route_path.relative_to(runtime_root()).as_posix(),
        "context_path": context_path.relative_to(runtime_root()).as_posix(),
        "impact_path": impact_path.relative_to(runtime_root()).as_posix(),
        "agent_budget_path": budget_path.relative_to(runtime_root()).as_posix(),
        "current_agents": agent_budget.get("current_agents", []),
        "mandatory_gate_agents": agent_budget.get("mandatory_gate_agents", []),
        "model_selections_path": models_path.relative_to(ROOT).as_posix(),
        "model_selections_sha256": sha256_file(models_path),
        "agents": routed["agents"],
        "human_gate": routed["human_gate"],
        "progress_path": (out_dir / "progress.json").relative_to(runtime_root()).as_posix(),
        "progress_state": progress["state"],
        "current_step": progress["current_step"],
        "selections": selections,
    }
    write_json_atomic(ACTIVE, active)
    return active


def delegation_plan(active: dict) -> list[dict]:
    """Compact per-agent view the Cursor orchestrator passes to the Task tool."""
    plan = []
    for sel in active.get("selections", []):
        use = sel.get("action") == "use" and sel.get("model_id")
        plan.append({
            "agent": sel.get("agent"),
            "subagent_type": sel.get("agent"),
            "model": sel["model_id"] if use else "inherit",
            "action": sel.get("action"),
            "reasoning_effort": sel.get("reasoning_effort"),
            "independence": (sel.get("independence") or {}).get("strength"),
            "reason": sel.get("reason"),
        })
    return plan


def main() -> int:
    ap = argparse.ArgumentParser(description="Activate one harness task for the Cursor runtime.")
    ap.add_argument("task", nargs="?", help="Task JSON path under the project")
    ap.add_argument("--host-models", help="Comma-separated Cursor Task-tool model IDs visible in this session")
    ap.add_argument("--clear", action="store_true", help="Clear the Cursor active task/model mapping")
    ap.add_argument("--full", action="store_true", help="Print the full active binding instead of the delegation plan")
    args = ap.parse_args()

    if args.clear:
        from harnesslib import reject_legacy_provider_state, quarantine_provider_active
        reject_legacy_provider_state(PROVIDER)
        receipt = quarantine_provider_active(PROVIDER, reason="cleared via cursor_activate_task.py --clear")
        if receipt is None:
            ACTIVE.unlink(missing_ok=True)
        print(json.dumps({
            "cleared": True,
            "quarantined": receipt is not None,
            "path": str(ACTIVE.relative_to(ROOT)),
        }, indent=2))
        return 0
    if args.host_models is not None:
        snapshot = record_host_models(args.host_models.split(","))
        print(json.dumps({"host_catalog": str(provider_catalog_path(PROVIDER).relative_to(ROOT)),
                          "models": snapshot["models"]}, indent=2))
        if not args.task:
            return 0
    if not args.task:
        ap.error("task is required unless --clear or --host-models is used")

    active = activate(resolve_task(args.task))
    if args.full:
        print(json.dumps(active, indent=2, ensure_ascii=False))
    else:
        print(json.dumps({
            "task_id": active["task_id"],
            "risk": active["risk"],
            "agents": active["agents"],
            "human_gate": active["human_gate"],
            "current_step": active["current_step"],
            "delegation": delegation_plan(active),
        }, indent=2, ensure_ascii=False))
    blocked = [x for x in active["selections"] if x.get("action") == "block"]
    return 2 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
