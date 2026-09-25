#!/usr/bin/env python
"""Bind one durable task to the OpenCode harness runtime.

The OpenCode plugin remains authoritative for runtime model availability. OpenRouter
enrichment is operator-triggered only: task activation never performs network calls.
Activation reads the previously generated scored inventory, intersects it with the
currently visible local OpenCode catalog, and routes using that local snapshot.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from harnesslib import (  # noqa: E402
    assert_overlay_writable, provider_active_path, provider_enriched_inventory_path, provider_model_selections_path,
    provider_overlay_dir,
    read_provider_active, reject_legacy_provider_state, run_dir, runtime_root, safe_task_id,
    sha256_file, worktree_identity, write_json_atomic, write_json_immutable,
    _reject_reparse_components,
    shared_migration_lock,
)
from task_router import route  # noqa: E402
from context_compiler import build as build_context  # noqa: E402
from orchestrator import init_progress  # noqa: E402
from impact_analysis import (
    build_plan as build_impact_plan,
    capture_baseline as capture_impact_baseline,
) # noqa: E402
from agent_budget import init as init_agent_budget  # noqa: E402
from request_normalizer import normalize_task  # noqa: E402
from model_router import load_inventory, selections_for_task  # noqa: E402
from openrouter_sync import discover_provider, load_provider_config  # noqa: E402

ENRICHED_INVENTORY = provider_enriched_inventory_path("opencode")
ACTIVE = provider_active_path("opencode")


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
        status["reason"] = "scored inventory missing; run python scripts/openrouter_sync.py --provider opencode"
        return None, None, status
    inventory, path = load_inventory("opencode", str(ENRICHED_INVENTORY))
    try:
        cfg = load_provider_config("opencode")
        available = {row["id"] for row in discover_provider("opencode", cfg) if row.get("enabled", True)}
    except Exception as exc:
        status["availability_error"] = str(exc)
        status["availability_verified"] = False
        available = set()
        inventory = dict(inventory)
        inventory["models"] = []
        status["scored_models_after_filter"] = 0
        return inventory, path, status
    status["availability_verified"] = True
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
        frozen = json.loads(route_path.read_text(encoding='utf-8'))
        if frozen.get('task_id') != task.get('id'):
            raise ValueError('frozen route task_id mismatch; refusing activation')
        return frozen
    return route(task)


def _authorize_invalid_binding_replacement(task_id: str, human_gate: str | None) -> dict:
    """Require verified backups and record an immutable gate receipt."""
    if not human_gate or len(human_gate) < 8:
        raise ValueError('--replace-invalid-active requires an explicit --human-gate token')
    reject_legacy_provider_state('opencode')
    active_path = provider_active_path('opencode')
    try:
        active = read_provider_active('opencode')
    except ValueError as exc:
        invalid_reason = str(exc)
    else:
        if active is None:
            raise ValueError('there is no invalid active OpenCode binding to replace')
        raise ValueError('refusing to replace a valid active OpenCode binding')
    if not active_path.is_file():
        raise ValueError('invalid active OpenCode binding disappeared during replacement')
    try:
        active_payload = json.loads(active_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError('invalid active OpenCode binding cannot be attributed safely') from exc
    if not isinstance(active_payload, dict) or active_payload.get('task_id') != task_id:
        raise ValueError('invalid active OpenCode binding belongs to a different task')

    overlay = provider_overlay_dir('opencode')
    # legacy-preserved is a sibling of overlays under .harness.
    preserved = ROOT / '.harness' / 'legacy-preserved' / task_id / 'opencode'
    names = {
        'active-task.json': 'active-task.pre-round-21-20260925.json',
        'model-selections.json': 'model-selections.pre-round-21-20260925.json',
        'enriched-inventory.json': 'enriched-inventory.pre-round-21-20260925.json',
    }
    backups = []
    for source_name, backup_name in names.items():
        source = overlay / source_name
        backup = preserved / backup_name
        _reject_reparse_components(source)
        _reject_reparse_components(backup)
        if not source.is_file() or not backup.is_file():
            raise ValueError(f'verified replacement backup missing for {source_name}')
        source_digest = sha256_file(source)
        backup_digest = sha256_file(backup)
        if source_digest != backup_digest:
            raise ValueError(f'active state changed since backup: {source_name}')
        backups.append({
            'source': str(source.relative_to(ROOT)),
            'backup': str(backup.relative_to(ROOT)),
            'sha256': source_digest,
        })

    body = {
        'schema_version': 1,
        'task_id': task_id,
        'provider': 'opencode',
        'status': 'AUTHORIZED_INVALID_BINDING_REPLACEMENT',
        'reason': invalid_reason,
        'worktree': str(ROOT),
        'worktree_id': worktree_identity()['worktree_id'],
        'human_gate': 'explicit user authorization recorded in task conversation',
        'human_gate_sha256': hashlib.sha256(human_gate.encode('utf-8')).hexdigest(),
        'backups': backups,
    }
    receipt = {
        **body,
        'receipt_sha256': hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(',', ':')).encode('utf-8')
        ).hexdigest(),
    }
    return write_json_immutable(
        run_dir(task_id) / 'migration' / 'state-replacements' /
        f'opencode-{worktree_identity()["worktree_id"]}-{receipt["receipt_sha256"][:16]}.json',
        receipt,
    )


def _activate_unlocked(task_path: Path, replace_invalid_active: bool = False,
                       human_gate: str | None = None) -> dict:
    # Fail before touching shared run evidence if an old global binding could
    # make this operation select or overwrite another checkout's state.
    task = json.loads(task_path.read_text(encoding="utf-8"))
    if task.get("request"):
        task = normalize_task(task)
    task_id = safe_task_id(task.get("id", ""))
    if replace_invalid_active:
        _authorize_invalid_binding_replacement(task_id, human_gate)
    else:
        assert_overlay_writable("opencode")
    out_dir = run_dir(task_id)
    route_path = out_dir / "route.json"
    routed = _load_or_route(task, route_path)
    context_path = out_dir / "context.json"
    models_path = provider_model_selections_path("opencode")
    task_snapshot_path = out_dir / "task.json"

    # Freeze the authorized task surface for the lifetime of this run. Publication
    # must not trust a mutable tasks/*.json file after activation.
    write_json_immutable(task_snapshot_path, task)
    write_json_immutable(route_path, routed)
    progress = init_progress(task_id, routed)
    context = (
        json.loads(context_path.read_text(encoding='utf-8'))
        if context_path.exists()
        else build_context(task, routed)
    )
    write_json_immutable(context_path, context)

    # HARNESS_IMPACT_BUDGET_ACTIVATION
    baseline = capture_impact_baseline(task_id)

    impact_path = out_dir / "impact.json"
    impact = (
        json.loads(impact_path.read_text(encoding='utf-8'))
        if impact_path.exists()
        else build_impact_plan(task, routed, context)
    )
    write_json_immutable(impact_path, impact)
    agent_budget = init_agent_budget(task, routed)
    budget_path = out_dir / "agent-budget.json"

    inventory, inventory_path, inventory_status = _select_inventory()
    selections = selections_for_task(task, "opencode", inventory)
    local_inventory_path = None
    if inventory is not None:
        # The OpenCode plugin validates that selections bind to the provider's
        # per-worktree overlay, not to the shared scored-inventory source.
        local_inventory_path = provider_overlay_dir("opencode") / "enriched-inventory.json"
        write_json_atomic(local_inventory_path, inventory)
    model_payload = {
        "schema_version": 2,
        "task_id": task_id,
        "provider": "opencode",
        "inventory_path": local_inventory_path.relative_to(ROOT).as_posix() if local_inventory_path else None,
        "inventory_sha256": sha256_file(local_inventory_path) if local_inventory_path else None,
        "inventory_status": inventory_status,
        "selections": selections,
    }
    write_json_atomic(models_path, model_payload)

    active = {
        "schema_version": 3,
        "provider": "opencode",
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
    # Task checks temporarily release the shared migration lock while running
    # the suite. Tag activations launched by that subprocess so cleanup can
    # distinguish them from an unrelated concurrent writer.
    if os.environ.get("HARNESS_TASK_CHECKS_OWNER"):
        active["task_checks_owner"] = os.environ["HARNESS_TASK_CHECKS_OWNER"]
    write_json_atomic(ACTIVE, active)
    return active


def activate(task_path: Path, replace_invalid_active: bool = False,
             human_gate: str | None = None) -> dict:
    with shared_migration_lock():
        return _activate_unlocked(task_path, replace_invalid_active, human_gate)


def clear(human_gate: str | None = None) -> dict:
    from harnesslib import reject_legacy_provider_state, quarantine_provider_active
    if not human_gate:
        raise ValueError('--clear requires an explicit --human-gate approval token')
    with shared_migration_lock():
        reject_legacy_provider_state("opencode")
        receipt = quarantine_provider_active(
            "opencode",
            reason="cleared via opencode_activate_task.py --clear",
            human_gate=human_gate,
        )
        return {
            "cleared": False,
            "quarantined": receipt is not None,
            "source_preserved": True,
            "reason": "active binding preserved; atomic ownership of source removal is not proven",
            "path": str(ACTIVE.relative_to(ROOT)),
        }


def main() -> int:
    ap = argparse.ArgumentParser(description="Activate one harness task for the OpenCode runtime.")
    ap.add_argument("task", nargs="?", help="Task JSON path under the project")
    ap.add_argument("--clear", action="store_true", help="Clear the OpenCode active task/model mapping")
    ap.add_argument("--human-gate", help="explicit approval token required with --clear")
    ap.add_argument(
        "--replace-invalid-active", action="store_true",
        help="replace this task's invalid active binding after verified backups",
    )
    args = ap.parse_args()

    if args.clear:
        print(json.dumps(clear(args.human_gate), indent=2))
        return 0
    if not args.task:
        ap.error("task is required unless --clear is used")

    active = activate(
        resolve_task(args.task),
        replace_invalid_active=args.replace_invalid_active,
        human_gate=args.human_gate,
    )
    print(json.dumps(active, indent=2, ensure_ascii=False))
    blocked = [x for x in active["selections"] if x.get("action") == "block"]
    return 2 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
