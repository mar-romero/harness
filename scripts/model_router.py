#!/usr/bin/env python3
"""Deterministic capability-based model router.

This module never guesses mutable model IDs. A runtime inventory supplied by the
provider, organization, or user is authoritative. If no inventory is available,
it falls back to the active session model (`inherit`) unless risk policy says to
block.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harnesslib import ROOT, load_manifest, run_dir, safe_task_id, write_json_atomic
from task_router import route

POLICY_PATH = ROOT / "harness" / "models.json"
CAP_KEYS = ("reasoning", "coding", "tool_use", "reliability")


def load_policy() -> dict[str, Any]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def inventory_age_hours(inventory: dict[str, Any], now: datetime | None = None) -> float | None:
    generated = _parse_time(inventory.get("generated_at"))
    if generated is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - generated).total_seconds() / 3600.0)


def resolve_inventory_path(provider: str, explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser().resolve()
    key = "HARNESS_MODEL_INVENTORY_" + provider.upper().replace("-", "_")
    value = os.getenv(key) or os.getenv("HARNESS_MODEL_INVENTORY")
    return Path(value).expanduser().resolve() if value else None


def load_inventory(provider: str, explicit: str | None = None) -> tuple[dict[str, Any] | None, Path | None]:
    path = resolve_inventory_path(provider, explicit)
    if path is None:
        return None, None
    data = json.loads(path.read_text(encoding="utf-8"))
    inv_provider = data.get("provider")
    if inv_provider and inv_provider != provider:
        raise ValueError(f"inventory provider {inv_provider!r} does not match requested provider {provider!r}")
    if not isinstance(data.get("models"), list):
        raise ValueError("inventory.models must be a list")
    return data, path


def requirements_for(model_class: str, risk: str, policy: dict[str, Any]) -> dict[str, float]:
    if model_class not in policy["model_classes"]:
        raise ValueError(f"unknown model class: {model_class}")
    req = {k: float(v) for k, v in policy["model_classes"][model_class].get("required", {}).items()}
    floor = policy.get("risk_overrides", {}).get(risk, {}).get("required_floor", {})
    for key, value in floor.items():
        req[key] = max(req.get(key, 0.0), float(value))
    return req


def _cap(model: dict[str, Any], key: str) -> float:
    try:
        return float(model.get("capabilities", {}).get(key, 0))
    except (TypeError, ValueError):
        return 0.0


def eligible(model: dict[str, Any], requirements: dict[str, float]) -> bool:
    if model.get("enabled", True) is not True:
        return False
    return all(_cap(model, key) >= threshold for key, threshold in requirements.items())


def score_model(model: dict[str, Any], model_class: str, policy: dict[str, Any]) -> float:
    weights = policy["model_classes"][model_class].get("weights", {})
    score = 0.0
    for key, weight in weights.items():
        if key in CAP_KEYS:
            value = _cap(model, key)
        else:
            try:
                value = float(model.get(key, 0))
            except (TypeError, ValueError):
                value = 0.0
        score += float(weight) * value
    if policy.get("selection", {}).get("prefer_provider_native") and model.get("native") is True:
        score += 0.000001
    precision = int(policy.get("selection", {}).get("score_precision", 6))
    return round(score, precision)


def select_model(*, task_id: str, provider: str, agent: str, model_class: str, risk: str,
                 inventory: dict[str, Any] | None, policy: dict[str, Any] | None = None,
                 now: datetime | None = None) -> dict[str, Any]:
    policy = policy or load_policy()
    requirements = requirements_for(model_class, risk, policy)
    default_no_inventory = policy["selection"].get("default_no_inventory_action", "inherit")
    default_no_eligible = policy["selection"].get("default_no_eligible_action", "inherit")
    risk_cfg = policy.get("risk_overrides", {}).get(risk, {})

    base = {
        "task_id": task_id,
        "provider": provider,
        "agent": agent,
        "model_class": model_class,
        "risk": risk,
        "model_id": None,
        "score": None,
        "eligible_models": 0,
        "inventory_generated_at": inventory.get("generated_at") if inventory else None,
        "inventory_stale": False,
        "requirements": requirements,
    }
    if inventory is None:
        action = risk_cfg.get("no_inventory_action", default_no_inventory)
        status = "blocked" if action == "block" else "inherit"
        return {**base, "status": status, "action": action, "reason": "no runtime model inventory supplied"}

    max_age = float(policy.get("inventory", {}).get("max_age_hours", 168))
    age = inventory_age_hours(inventory, now=now)
    stale = age is None or age > max_age
    base["inventory_stale"] = stale
    stale_allowed = bool(policy.get("inventory", {}).get("allow_stale_for_r0_r2", True)) if risk != "R3" else bool(policy.get("inventory", {}).get("allow_stale_for_r3", False))
    if stale and not stale_allowed:
        return {**base, "status": "blocked", "action": "block", "reason": "runtime model inventory is missing a valid timestamp or is too stale for this risk level"}

    candidates = [m for m in inventory.get("models", []) if eligible(m, requirements)]
    base["eligible_models"] = len(candidates)
    if not candidates:
        action = risk_cfg.get("no_eligible_action", default_no_eligible)
        status = "blocked" if action == "block" else "inherit"
        return {**base, "status": status, "action": action, "reason": "no enabled runtime model satisfies required capabilities"}

    ranked = sorted(
        ((score_model(m, model_class, policy), str(m.get("id", "")), m) for m in candidates),
        key=lambda x: (-x[0], x[1]),
    )
    best_score, model_id, _model = ranked[0]
    if not model_id:
        raise ValueError("eligible inventory model is missing id")
    return {**base, "status": "selected", "action": "use", "model_id": model_id, "score": best_score,
            "reason": "highest deterministic capability-policy score among eligible runtime models"}


def selections_for_task(task: dict[str, Any], provider: str, inventory: dict[str, Any] | None,
                        only_agent: str | None = None) -> list[dict[str, Any]]:
    manifest = load_manifest()
    routed = route(task)
    agents = [only_agent] if only_agent else routed["agents"]
    out = []
    for agent in agents:
        if agent not in manifest["agents"]:
            raise ValueError(f"unknown agent: {agent}")
        meta = manifest["agents"][agent]
        out.append(select_model(task_id=task["id"], provider=provider, agent=agent,
                                model_class=meta["model_class"], risk=routed["risk"], inventory=inventory))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Select runtime models by capability policy without pinning mutable model IDs.")
    ap.add_argument("task", help="Task JSON compatible with the v2 task router")
    ap.add_argument("--provider", required=True, help="Provider key, e.g. codex, claude, cursor, gemini, opencode, copilot")
    ap.add_argument("--inventory", help="Runtime inventory JSON. Can also use HARNESS_MODEL_INVENTORY[_PROVIDER].")
    ap.add_argument("--agent", help="Select only this agent; default selects all routed agents")
    ap.add_argument("--output", help="Optional output path; default .harness/runs/<task>/model-selections.json")
    args = ap.parse_args()

    task_path = Path(args.task)
    if not task_path.is_absolute():
        task_path = ROOT / task_path
    task = json.loads(task_path.read_text(encoding="utf-8"))
    safe_task_id(task.get("id", ""))
    manifest = load_manifest()
    if args.provider not in manifest.get("providers", {}):
        raise SystemExit(f"unknown provider: {args.provider}")
    inventory, inventory_path = load_inventory(args.provider, args.inventory)
    selections = selections_for_task(task, args.provider, inventory, args.agent)
    payload = {
        "schema_version": 1,
        "task_id": task["id"],
        "provider": args.provider,
        "inventory_path": str(inventory_path) if inventory_path else None,
        "selections": selections,
    }
    dest = Path(args.output) if args.output else run_dir(task["id"]) / "model-selections.json"
    if not dest.is_absolute():
        dest = ROOT / dest
    write_json_atomic(dest, payload)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 2 if any(x["action"] == "block" for x in selections) else 0


if __name__ == "__main__":
    raise SystemExit(main())
