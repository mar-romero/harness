#!/usr/bin/env python3
"""Deterministic per-agent model and reasoning-effort router.

Runtime provider inventory is authoritative for availability. OpenRouter data is an
external prior, not an availability source. The router uses:

1. task capability target (reasoning/coding/tool_use/reliability),
2. role adjustments and hard model-class/risk floors,
3. quality/cost/latency/local-evidence scoring,
4. independent reviewer/verifier selection when a comparable alternative exists,
5. a separate reasoning-effort decision.
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
from model_task_profile import CAP_KEYS, profile_task, target_for_agent

POLICY_PATH = ROOT / "harness" / "models.json"
PROVIDER_DIR = ROOT / "harness" / "model-providers"


def load_policy() -> dict[str, Any]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def load_provider_policy(provider: str) -> dict[str, Any]:
    path = PROVIDER_DIR / f"{provider}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def inventory_age_hours(inventory: dict[str, Any], now: datetime | None = None) -> float | None:
    generated = _parse_time(inventory.get("availability_generated_at") or inventory.get("generated_at"))
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


def _objective(model: dict[str, Any], key: str) -> float:
    try:
        return max(0.0, min(5.0, float(model.get(key, 0)))) / 5.0
    except (TypeError, ValueError):
        return 0.0


def _quality_component(capability: float, target: float, policy: dict[str, Any]) -> float:
    if target <= 0:
        return 1.0
    coverage = min(capability / target, 1.0)
    surplus = 0.0
    if capability > target and target < 5.0:
        surplus = min((capability - target) / (5.0 - target), 1.0)
    cfg = policy.get("scoring", {})
    return float(cfg.get("coverage_weight", 0.85)) * coverage + float(cfg.get("surplus_weight", 0.15)) * surplus


def _local_component(model: dict[str, Any], policy: dict[str, Any]) -> float:
    evidence = model.get("local_evidence") or {}
    neutral = float(policy.get("scoring", {}).get("local_evidence_neutral", 0.5))
    try:
        score = max(0.0, min(5.0, float(evidence.get("score")))) / 5.0
        samples = max(0.0, float(evidence.get("samples", 0)))
    except (TypeError, ValueError):
        return neutral
    full = max(1.0, float(policy.get("scoring", {}).get("local_evidence_full_weight_samples", 20)))
    confidence = min(samples / full, 1.0)
    return score * confidence + neutral * (1.0 - confidence)


def score_model_details(model: dict[str, Any], model_class: str, risk: str, target: dict[str, float],
                        policy: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    class_cfg = policy["model_classes"][model_class]
    # Backward compatibility with the v1 policy shape used by the existing test suite.
    # The v1 formula is intentionally preserved when `weights` exists without
    # `quality_weights`; production v2 policy uses the task-aware formula below.
    if "quality_weights" not in class_cfg and "weights" in class_cfg:
        total = 0.0
        for key, weight in class_cfg.get("weights", {}).items():
            value = _cap(model, key) if key in CAP_KEYS else float(model.get(key, 0) or 0)
            total += float(weight) * value
        precision = int(policy.get("selection", {}).get("score_precision", 6))
        return round(total, precision), {"legacy_v1_score": True}

    quality_weights = class_cfg.get("quality_weights", {})
    dynamic_weights: dict[str, float] = {}
    for key in CAP_KEYS:
        base = float(quality_weights.get(key, 0.0))
        importance = 0.5 + float(target.get(key, 0.0)) / 5.0
        dynamic_weights[key] = base * importance
    total_weight = sum(dynamic_weights.values()) or 1.0
    dynamic_weights = {k: v / total_weight for k, v in dynamic_weights.items()}

    components = {key: _quality_component(_cap(model, key), float(target.get(key, 0.0)), policy) for key in CAP_KEYS}
    quality = sum(dynamic_weights[k] * components[k] for k in CAP_KEYS)
    cost = _objective(model, "cost")
    latency = _objective(model, "latency")
    local = _local_component(model, policy)

    objective = policy.get("scoring", {}).get("risk_objective_weights", {}).get(risk, {})
    total = (
        float(objective.get("quality", 0.65)) * quality
        + float(objective.get("cost", 0.15)) * cost
        + float(objective.get("latency", 0.15)) * latency
        + float(objective.get("local_evidence", 0.05)) * local
    ) * 100.0
    precision = int(policy.get("scoring", {}).get("score_precision", 6))
    score = round(total, precision)
    return score, {
        "quality": round(quality, precision),
        "cost": round(cost, precision),
        "latency": round(latency, precision),
        "local_evidence": round(local, precision),
        "quality_dimension_weights": {k: round(v, precision) for k, v in dynamic_weights.items()},
        "quality_components": {k: round(v, precision) for k, v in components.items()},
        "objective_weights": objective,
    }


def score_model(model: dict[str, Any], model_class: str, policy: dict[str, Any],
                risk: str = "R1", target: dict[str, float] | None = None) -> float:
    """Backward-compatible public scorer used by tests and external callers."""
    if target is None:
        target = {k: float(policy["model_classes"][model_class].get("required", {}).get(k, 0.0)) for k in CAP_KEYS}
    return score_model_details(model, model_class, risk, target, policy)[0]


def _model_family(model: dict[str, Any]) -> str:
    return str(model.get("family") or model.get("id") or "")


def _model_vendor(model: dict[str, Any]) -> str:
    if model.get("vendor"):
        return str(model["vendor"])
    mid = str(model.get("openrouter_id") or model.get("id") or "")
    return mid.split("/", 1)[0] if "/" in mid else ""


def _supported_efforts(model: dict[str, Any], provider: str, provider_policy: dict[str, Any]) -> list[str]:
    explicit = model.get("supported_efforts")
    if isinstance(explicit, list) and explicit:
        return [str(x) for x in explicit]
    effort_cfg = provider_policy.get("effort", {})
    mid = str(model.get("id", ""))
    for rule in effort_cfg.get("model_rules", []):
        import re
        if re.search(str(rule.get("pattern", "^$")), mid):
            return [str(x) for x in rule.get("supported", [])]
    return [str(x) for x in effort_cfg.get("unknown_model_supported", [])]


def _nearest_effort(desired: str, supported: list[str], policy: dict[str, Any]) -> str | None:
    if not supported:
        return None
    order = [str(x) for x in policy.get("effort", {}).get("order", [])]
    if desired in supported:
        return desired
    if desired not in order:
        return supported[0]
    idx = order.index(desired)
    supported_set = set(supported)
    for pos in range(idx - 1, -1, -1):
        if order[pos] in supported_set:
            return order[pos]
    for pos in range(idx + 1, len(order)):
        if order[pos] in supported_set:
            return order[pos]
    return supported[0]


def select_effort(target: dict[str, float], agent: str, risk: str, supported: list[str],
                  policy: dict[str, Any]) -> tuple[str | None, float]:
    cfg = policy.get("effort", {})
    pressure = sum(float(cfg.get("pressure_weights", {}).get(k, 0.0)) * float(target.get(k, 0.0)) for k in CAP_KEYS)
    pressure += float(cfg.get("role_bias", {}).get(agent, 0.0))
    pressure += float(cfg.get("risk_bias", {}).get(risk, 0.0))
    pressure = max(0.0, min(5.0, pressure))

    desired = str(cfg.get("top_effort", "max"))
    for item in cfg.get("thresholds", []):
        if pressure < float(item.get("max_exclusive", 999)):
            desired = str(item.get("effort"))
            break

    order = [str(x) for x in cfg.get("order", [])]
    cap = str(cfg.get("risk_caps", {}).get(risk, desired))
    if desired in order and cap in order and order.index(desired) > order.index(cap):
        desired = cap
    return _nearest_effort(desired, supported, policy), round(pressure, 3)


def _runtime_model_id(base_id: str, effort: str | None, provider_policy: dict[str, Any]) -> str:
    cfg = provider_policy.get("effort", {})
    if effort and cfg.get("mode") == "variant-suffix":
        return base_id + str(cfg.get("separator", "#")) + effort
    return base_id


def _rank_candidates(candidates: list[dict[str, Any]], model_class: str, risk: str,
                     target: dict[str, float], policy: dict[str, Any]) -> list[tuple[float, str, dict[str, Any], dict[str, Any]]]:
    ranked = []
    for model in candidates:
        score, breakdown = score_model_details(model, model_class, risk, target, policy)
        ranked.append((score, str(model.get("id", "")), model, breakdown))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    return ranked


def _prefer_independent(ranked: list[tuple[float, str, dict[str, Any], dict[str, Any]]],
                        avoid_models: set[str], avoid_families: set[str], avoid_vendors: set[str],
                        policy: dict[str, Any]) -> tuple[tuple[float, str, dict[str, Any], dict[str, Any]], dict[str, Any]]:
    best = ranked[0]
    chosen = best
    info = {"strength": "not_applicable", "baseline_model": best[1], "rules_applied": []}
    cfg = policy.get("independence", {})

    if avoid_models:
        different = [x for x in ranked if x[1] not in avoid_models]
        if different and different[0][0] >= float(cfg.get("different_model_min_relative_score", 0.90)) * best[0]:
            chosen = different[0]
            info["strength"] = "different_model"
            info["rules_applied"].append("different_model")
        else:
            info["strength"] = "same_model_fallback"

    if cfg.get("different_family_preferred") and avoid_families:
        family_alt = [x for x in ranked if _model_family(x[2]) not in avoid_families and x[1] not in avoid_models]
        if family_alt and family_alt[0][0] >= float(cfg.get("different_family_min_relative_score", 0.92)) * best[0]:
            chosen = family_alt[0]
            info["strength"] = "different_family"
            info["rules_applied"].append("different_family")

    if cfg.get("different_vendor_preferred") and avoid_vendors:
        vendor_alt = [x for x in ranked if _model_vendor(x[2]) not in avoid_vendors and x[1] not in avoid_models]
        if vendor_alt and vendor_alt[0][0] >= float(cfg.get("different_vendor_min_relative_score", 0.94)) * best[0]:
            chosen = vendor_alt[0]
            info["strength"] = "different_vendor"
            info["rules_applied"].append("different_vendor")

    info["selected_model"] = chosen[1]
    return chosen, info


def select_model(*, task_id: str, provider: str, agent: str, model_class: str, risk: str,
                 inventory: dict[str, Any] | None, policy: dict[str, Any] | None = None,
                 now: datetime | None = None, target: dict[str, float] | None = None,
                 avoid_models: set[str] | None = None, avoid_families: set[str] | None = None,
                 avoid_vendors: set[str] | None = None) -> dict[str, Any]:
    policy = policy or load_policy()
    provider_policy = load_provider_policy(provider)
    requirements = requirements_for(model_class, risk, policy)
    target = target or {k: max(requirements.get(k, 0.0), 2.5) for k in CAP_KEYS}
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
        "base_model_id": None,
        "reasoning_effort": None,
        "effort_pressure": None,
        "score": None,
        "eligible_models": 0,
        "inventory_generated_at": inventory.get("generated_at") if inventory else None,
        "inventory_stale": False,
        "requirements": requirements,
        "target": target,
    }
    if inventory is None:
        action = risk_cfg.get("no_inventory_action", default_no_inventory)
        status = "blocked" if action == "block" else "inherit"
        return {**base, "status": status, "action": action, "reason": "no runtime model inventory supplied"}

    inventory_cfg = policy.get("inventory", {})
    enforce_age = bool(inventory_cfg.get("enforce_max_age", True))
    max_age_raw = inventory_cfg.get("max_age_hours", 168)
    age = inventory_age_hours(inventory, now=now)
    stale = False
    if enforce_age:
        max_age = float(168 if max_age_raw is None else max_age_raw)
        stale = age is None or age > max_age
    base["inventory_stale"] = stale
    stale_allowed = bool(inventory_cfg.get("allow_stale_for_r0_r2", True)) if risk != "R3" else bool(inventory_cfg.get("allow_stale_for_r3", False))
    if stale and not stale_allowed:
        return {**base, "status": "blocked", "action": "block", "reason": "runtime model inventory is missing a valid timestamp or is too stale for this risk level"}

    candidates = [m for m in inventory.get("models", []) if eligible(m, requirements)]
    base["eligible_models"] = len(candidates)
    if not candidates:
        action = risk_cfg.get("no_eligible_action", default_no_eligible)
        status = "blocked" if action == "block" else "inherit"
        return {**base, "status": status, "action": action, "reason": "no enabled runtime model satisfies required capabilities"}

    ranked = _rank_candidates(candidates, model_class, risk, target, policy)
    chosen, independence = _prefer_independent(
        ranked,
        avoid_models or set(),
        avoid_families or set(),
        avoid_vendors or set(),
        policy,
    )
    best_score, base_model_id, model, breakdown = chosen
    if not base_model_id:
        raise ValueError("eligible inventory model is missing id")
    supported = _supported_efforts(model, provider, provider_policy)
    effort, pressure = select_effort(target, agent, risk, supported, policy)
    runtime_id = _runtime_model_id(base_model_id, effort, provider_policy)
    return {
        **base,
        "status": "selected",
        "action": "use",
        "model_id": runtime_id,
        "base_model_id": base_model_id,
        "reasoning_effort": effort,
        "effort_pressure": pressure,
        "score": best_score,
        "score_breakdown": breakdown,
        "independence": independence,
        "model_family": _model_family(model),
        "model_vendor": _model_vendor(model),
        "openrouter_id": model.get("openrouter_id"),
        "reason": "highest deterministic task/role/risk score among eligible runtime models, subject to independence preference",
    }


def selections_for_task(task: dict[str, Any], provider: str, inventory: dict[str, Any] | None,
                        only_agent: str | None = None) -> list[dict[str, Any]]:
    manifest = load_manifest()
    policy = load_policy()
    routed = route(task)
    agents = [only_agent] if only_agent else routed["agents"]
    task_profile = profile_task(task, routed["risk"], policy)
    out: list[dict[str, Any]] = []
    by_agent: dict[str, dict[str, Any]] = {}

    for agent in agents:
        if agent not in manifest["agents"]:
            raise ValueError(f"unknown agent: {agent}")
        meta = manifest["agents"][agent]
        target = target_for_agent(task_profile, agent, routed["risk"], policy)
        independence_cfg = policy.get("independence", {}).get("roles", {}).get(agent, {})
        avoid_agents = [str(x) for x in independence_cfg.get("avoid_agents", [])]
        previous = [by_agent[x] for x in avoid_agents if x in by_agent and by_agent[x].get("action") == "use"]
        avoid_models = {str(x.get("base_model_id") or x.get("model_id")) for x in previous}
        avoid_families = {str(x.get("model_family")) for x in previous if x.get("model_family")}
        avoid_vendors = {str(x.get("model_vendor")) for x in previous if x.get("model_vendor")}
        selection = select_model(
            task_id=task["id"], provider=provider, agent=agent,
            model_class=meta["model_class"], risk=routed["risk"], inventory=inventory,
            policy=policy, target=target, avoid_models=avoid_models,
            avoid_families=avoid_families, avoid_vendors=avoid_vendors,
        )
        selection["task_profile"] = task_profile
        out.append(selection)
        by_agent[agent] = selection
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Select runtime models and reasoning effort by task/role capability fit.")
    ap.add_argument("task", help="Task JSON compatible with the task router")
    ap.add_argument("--provider", required=True, help="Provider key, e.g. codex or opencode")
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
        "schema_version": 2,
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
