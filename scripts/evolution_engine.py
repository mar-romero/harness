#!/usr/bin/env python
"""Analyze harness evaluation history and emit human-reviewed proposals only.

This engine has no code path that edits agents, skills, policies, hooks, tests,
or provider adapters. Its sole artifact is a structured proposal under
.harness/evolution/proposals (or a caller-selected output path).
"""
from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harnesslib import ROOT, write_json_atomic

POLICY_PATH = ROOT / "harness" / "evolution-policy.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_policy() -> dict[str, Any]:
    data = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    if data.get("allow_auto_apply") is not False or data.get("mode") != "proposal-only":
        raise ValueError("evolution policy must be proposal-only with allow_auto_apply=false")
    return data


def load_runs(history: Path) -> list[dict[str, Any]]:
    runs = []
    for path in sorted(history.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("schema_version") == 1 and data.get("run_id"):
            data["_path"] = str(path)
            runs.append(data)
    runs.sort(key=lambda x: (x.get("created_at", ""), x.get("run_id", "")))
    return runs


def deterministic_pass(run: dict[str, Any]) -> bool:
    checks = run.get("deterministic", {})
    return bool(checks) and all(isinstance(v, dict) and v.get("returncode") == 0 for v in checks.values())


def _median_metric(runs: list[dict[str, Any]], key: str) -> float | None:
    vals = [r.get("metrics", {}).get(key) for r in runs]
    nums = [float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return statistics.median(nums) if nums else None


def _change(candidate: float, baseline: float, relative: bool) -> float:
    if not relative:
        return candidate - baseline
    denom = abs(baseline)
    if denom < 1e-12:
        return 0.0 if abs(candidate) < 1e-12 else float("inf")
    return (candidate - baseline) / denom


def _severity(delta: float, cfg: dict[str, Any]) -> str | None:
    direction = cfg["direction"]
    material = float(cfg["material_delta"])
    critical = float(cfg["critical_delta"])
    regression = -delta if direction == "higher" else delta
    if regression >= critical:
        return "critical"
    if regression >= material:
        return "material"
    return None


def _proposal_for_metric(metric: str, severity: str) -> dict[str, Any]:
    mapping = {
        "task_success_rate": ("routing", "Review task routing, context packs, and the responsible agent/skill contracts against failed benchmark cases."),
        "bug_introduction_rate": ("agent-contract", "Strengthen implementation/check contracts and add defect-reproducing eval cases before changing prompts."),
        "review_precision": ("agent-contract", "Inspect reviewer evidence thresholds and false-positive cases; add adversarial review evals."),
        "review_recall": ("agent-contract", "Inspect missed-defect cases and reviewer/test-auditor coverage; add falsification fixtures."),
        "unsafe_action_attempts": ("security-gates", "Inspect pre-tool gates, trust boundaries, and prompt-injection/tool-output defenses. Do not weaken gates."),
        "human_intervention_rate": ("routing", "Identify reversible decisions unnecessarily escalated to humans and tighten deterministic routing/evidence rules."),
        "tokens": ("context", "Inspect context-pack size, skill progressive loading, and duplicate instructions without removing required evidence."),
        "latency_seconds": ("model-policy", "Inspect model-class routing and unnecessary agent stages while preserving risk requirements."),
        "cost_usd": ("model-policy", "Inspect model-class routing and context size; never trade away security/correctness hard gates for cost."),
    }
    area, action = mapping.get(metric, ("eval-coverage", f"Investigate regression in {metric} and add a reproducing eval before changing the harness."))
    return {"target_area": area, "severity": severity, "suggested_action": action,
            "constraints": ["proposal only", "add/reproduce evidence first", "full eval required before approval", "human approval required"]}


def analyze_runs(runs: list[dict[str, Any]], policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or load_policy()
    minimum = int(policy.get("minimum_baseline_runs", 2))
    if len(runs) < minimum + 1:
        candidate = runs[-1]["run_id"] if runs else "none"
        return {"status": "INSUFFICIENT_BASELINE", "baseline_runs": [r.get("run_id") for r in runs[:-1]], "candidate_run": candidate,
                "findings": [], "proposals": [], "reason": f"need at least {minimum} baseline runs plus one candidate"}

    candidate = runs[-1]
    window = int(policy.get("baseline_window", 5))
    baselines = runs[max(0, len(runs) - 1 - window):-1]
    findings: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []

    if not deterministic_pass(candidate):
        findings.append({"kind": "hard_regression", "severity": "critical", "name": "deterministic_checks_failed",
                         "evidence": {k: v.get("returncode") for k, v in candidate.get("deterministic", {}).items()}})
        proposals.append({"target_area": "eval-coverage", "severity": "critical",
                          "suggested_action": "Fix or explain the deterministic regression before considering any optimization. Preserve valid failing tests.",
                          "constraints": ["do not delete failing tests", "do not weaken gates", "human approval required"]})
        return {"status": "BLOCKED_REGRESSION", "baseline_runs": [r["run_id"] for r in baselines], "candidate_run": candidate["run_id"],
                "findings": findings, "proposals": proposals, "reason": "candidate deterministic checks do not pass"}

    metric_cfgs = policy.get("metrics", {})
    for metric, cfg in metric_cfgs.items():
        base = _median_metric(baselines, metric)
        cand = candidate.get("metrics", {}).get(metric)
        if base is None or not isinstance(cand, (int, float)) or isinstance(cand, bool):
            continue
        delta = _change(float(cand), base, bool(cfg.get("relative", False)))
        severity = _severity(delta, cfg)
        if severity:
            finding = {"kind": "metric_regression", "metric": metric, "severity": severity,
                       "baseline_median": base, "candidate": float(cand), "delta": delta,
                       "relative": bool(cfg.get("relative", False))}
            findings.append(finding)
            p = _proposal_for_metric(metric, severity)
            p["evidence"] = finding
            proposals.append(p)

    status = "HUMAN_REVIEW_REQUIRED" if proposals else "NO_CHANGE"
    return {"status": status, "baseline_runs": [r["run_id"] for r in baselines], "candidate_run": candidate["run_id"],
            "findings": findings, "proposals": proposals,
            "reason": "material regressions detected" if proposals else "no material regression against configured thresholds"}


def main() -> int:
    ap = argparse.ArgumentParser(description="Create a controlled, non-applying harness evolution proposal from recorded eval history.")
    ap.add_argument("--history", default=".harness/evolution/history")
    ap.add_argument("--output", help="Optional proposal path; default .harness/evolution/proposals/<timestamp>.json")
    args = ap.parse_args()

    policy = load_policy()
    history = Path(args.history)
    if not history.is_absolute():
        history = ROOT / history
    runs = load_runs(history)
    result = analyze_runs(runs, policy)
    created = now_iso()
    proposal_id = "EVOL-" + created.replace(":", "").replace("-", "").replace(".", "")
    payload = {
        "schema_version": 1,
        "proposal_id": proposal_id,
        "created_at": created,
        "mode": "proposal-only",
        **result,
        "approval": {"required": True, "auto_apply_allowed": False},
        "protected_paths": policy.get("protected_paths", []),
    }
    dest = Path(args.output) if args.output else ROOT / ".harness" / "evolution" / "proposals" / f"{proposal_id}.json"
    if not dest.is_absolute():
        dest = ROOT / dest
    write_json_atomic(dest, payload)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 2 if payload["status"] == "BLOCKED_REGRESSION" else 0


if __name__ == "__main__":
    raise SystemExit(main())
