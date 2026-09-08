#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from harnesslib import ROOT

POLICY_PATH = ROOT / "harness" / "models.json"
CAP_KEYS = ("reasoning", "coding", "tool_use", "reliability")


def load_policy() -> dict[str, Any]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def _clamp(value: float) -> float:
    return round(max(0.0, min(5.0, float(value))), 3)


def _task_text(task: dict[str, Any]) -> str:
    req = task.get("request") or {}
    pieces = [
        req.get("canonical_english", ""),
        req.get("original_text", ""),
        task.get("description", ""),
        *[str(x) for x in task.get("tags", [])],
        *[str(x) for x in task.get("files", [])],
    ]
    return " ".join(x for x in pieces if x)


def _merge_delta(values: dict[str, float], delta: dict[str, Any]) -> None:
    for key in CAP_KEYS:
        if key in delta:
            values[key] = values.get(key, 0.0) + float(delta[key])


def profile_task(task: dict[str, Any], risk: str, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """Produce the continuous task capability target used by per-agent routing.

    Formula:
      target = clamp(base + regex signal deltas + structured risk-factor deltas)
      target = max(target, risk target floors)
      explicit task.model_requirements values are additional floors, never downgrades.
    """
    policy = policy or load_policy()
    cfg = policy.get("task_profile", {})
    values = {key: float((cfg.get("base") or {}).get(key, 0.0)) for key in CAP_KEYS}
    signals: list[str] = []
    text = _task_text(task)

    for item in cfg.get("signals", []):
        pattern = item.get("pattern")
        if pattern and re.search(pattern, text, re.I):
            _merge_delta(values, item.get("delta") or {})
            signals.append(str(item.get("name") or pattern))

    factors = task.get("risk_factors") or {}
    factor_deltas = cfg.get("structured_factor_deltas", {})
    for name, enabled in factors.items():
        if enabled and name in factor_deltas:
            _merge_delta(values, factor_deltas[name])
            signals.append(f"risk_factor:{name}")

    floors = (policy.get("risk_overrides", {}).get(risk, {}) or {}).get("target_floors", {})
    for key, floor in floors.items():
        if key in CAP_KEYS:
            values[key] = max(values.get(key, 0.0), float(floor))

    explicit = task.get("model_requirements") or {}
    for key, floor in explicit.items():
        if key in CAP_KEYS:
            values[key] = max(values.get(key, 0.0), float(floor))
            signals.append(f"explicit_floor:{key}")

    requirements = {key: _clamp(values[key]) for key in CAP_KEYS}
    return {
        "requirements": requirements,
        "signals": list(dict.fromkeys(signals)),
        "text_length": len(text),
    }


def target_for_agent(task_profile: dict[str, Any], agent: str, risk: str,
                     policy: dict[str, Any] | None = None) -> dict[str, float]:
    policy = policy or load_policy()
    target = {key: float((task_profile.get("requirements") or {}).get(key, 0.0)) for key in CAP_KEYS}
    adjustment = (policy.get("role_adjustments") or {}).get(agent, {})
    _merge_delta(target, adjustment)
    floors = (policy.get("risk_overrides", {}).get(risk, {}) or {}).get("target_floors", {})
    for key, floor in floors.items():
        if key in CAP_KEYS:
            target[key] = max(target[key], float(floor))
    return {key: _clamp(target[key]) for key in CAP_KEYS}


def main() -> int:
    import argparse
    from task_router import route

    ap = argparse.ArgumentParser(description="Profile a task into reasoning/coding/tool_use/reliability targets.")
    ap.add_argument("task")
    args = ap.parse_args()
    path = Path(args.task)
    if not path.is_absolute():
        path = ROOT / path
    task = json.loads(path.read_text(encoding="utf-8"))
    routed = route(task)
    profile = profile_task(task, routed["risk"])
    print(json.dumps({"task_id": task.get("id"), "risk": routed["risk"], **profile}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
