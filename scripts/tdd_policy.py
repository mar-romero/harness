#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re
from pathlib import Path
from harnesslib import ROOT, load_json

RISK_RANK = {"R0":0, "R1":1, "R2":2, "R3":3}
MODES = {
    "not_applicable","test_after_allowed","tdd_preferred","tdd_required",
    "characterization_then_tdd","spike_then_tdd"
}

def _policy():
    return load_json("harness/tdd-policy.json")

def _text(task):
    req = task.get("request") or {}
    return " ".join([
        str(req.get("canonical_english") or ""),
        str(req.get("original_text") or ""),
        str(task.get("description") or ""),
        " ".join(map(str, task.get("tags") or [])),
        " ".join(map(str, task.get("files") or [])),
    ]).lower()

def _match(patterns, text):
    return any(re.search(p, text, re.I) for p in patterns)

def _human_exception(task):
    exc = ((task.get("test_strategy") or {}).get("exception") or {})
    return bool(exc.get("human_approved") is True and str(exc.get("reason") or "").strip())

def _explicit_mode(task, risk):
    cfg = task.get("test_strategy") or {}
    mode = cfg.get("mode")
    if mode not in MODES:
        return None
    p = _policy()
    default = p["risk_defaults"][risk]
    relaxing = {
        "not_applicable": 0, "test_after_allowed": 1, "tdd_preferred": 2,
        "tdd_required": 3, "characterization_then_tdd": 4, "spike_then_tdd": 4
    }[mode] < {
        "not_applicable": 0, "test_after_allowed": 1, "tdd_preferred": 2,
        "tdd_required": 3, "characterization_then_tdd": 4, "spike_then_tdd": 4
    }[default]
    if relaxing and not p["explicit_relaxation"].get(risk, False) and not _human_exception(task):
        return None
    return mode

def profile_task(task, risk="R1", text=None, bug=False, external=False, security=False):
    p = _policy()
    text = (text or _text(task)).lower()
    explicit = _explicit_mode(task, risk)
    factors = task.get("risk_factors") or {}
    reasons = []

    if explicit:
        mode = explicit
        reasons.append("explicit task test_strategy mode")
    elif _match(p["signals"]["not_applicable"], text):
        mode = "not_applicable"; reasons.append("non-production research/documentation signal")
    elif _match(p["signals"]["test_after_allowed"], text):
        mode = "test_after_allowed"; reasons.append("visual/mechanical/generated-work signal")
    elif _match(p["signals"]["spike_then_tdd"], text):
        mode = "spike_then_tdd"; reasons.append("unresolved contract/technical uncertainty signal")
    elif _match(p["signals"]["characterization_then_tdd"], text):
        mode = "characterization_then_tdd"; reasons.append("legacy/brownfield characterization signal")
    elif bug or _match(p["signals"]["tdd_required"], text):
        mode = "tdd_required"; reasons.append("regression/stable behavioral-rule signal")
    elif any(bool(factors.get(k)) for k in p["structured_required_factors"]):
        mode = "tdd_required"; reasons.append("structured correctness-risk factor")
    else:
        mode = p["risk_defaults"][risk]; reasons.append(f"{risk} default")

    required = list(p["evidence"][mode])
    designer = mode in set(p["independent_test_designer"]["required_for_modes"])
    if mode == "tdd_preferred" and RISK_RANK[risk] >= RISK_RANK[p["independent_test_designer"]["minimum_risk_for_preferred"]]:
        designer = True

    mutation_recommended = (
        risk in set(p["mutation"]["recommended_risks"])
        or any(bool(factors.get(k)) for k in p["mutation"]["recommended_factors"])
    )
    cfg = task.get("test_strategy") or {}
    mutation_required = bool(cfg.get("mutation_required", p["mutation"]["required_by_default"]))
    if mutation_required:
        required.append("mutation")

    if designer and mode not in {"not_applicable","test_after_allowed"}:
        required = ["design"] + required

    return {
        "mode": mode,
        "reasons": reasons,
        "test_designer": designer,
        "required_evidence": list(dict.fromkeys(required)),
        "red_evidence_required": "red" in required,
        "negative_boundary_analysis_required": mode in {
            "tdd_required","characterization_then_tdd","spike_then_tdd"
        },
        "mutation_recommended": mutation_recommended,
        "mutation_required": mutation_required,
        "small_uniform_cycles": mode in {
            "tdd_preferred","tdd_required","characterization_then_tdd","spike_then_tdd"
        }
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task")
    ap.add_argument("--risk", choices=RISK_RANK, default="R1")
    a = ap.parse_args()
    task = json.loads(Path(a.task).read_text(encoding="utf-8"))
    print(json.dumps(profile_task(task, risk=a.risk), indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
