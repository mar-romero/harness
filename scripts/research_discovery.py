#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from pathlib import Path

from harnesslib import ROOT, load_json, write_json_atomic

POLICY_PATH = "harness/research-policy.json"
VALID_MODES = {"none", "light", "research", "full"}


def _policy():
    return load_json(POLICY_PATH)


def _resolve_discovery(value: str) -> Path:
    p = Path(value)
    p = p if p.is_absolute() else ROOT / p
    p = p.resolve()
    p.relative_to(ROOT.resolve())
    if not p.is_file():
        raise ValueError(f"discovery file not found: {p}")
    return p


def _blocking_questions(bundle: dict) -> int:
    return sum(1 for q in bundle.get("open_questions", []) if isinstance(q, dict) and q.get("blocking") is True)


def _text(bundle: dict) -> str:
    parts = [
        str(bundle.get("classification", "")),
        str(bundle.get("refined_problem", "")),
        json.dumps(bundle.get("assumptions", []), ensure_ascii=False),
        json.dumps(bundle.get("challenges", []), ensure_ascii=False),
        json.dumps(bundle.get("scope", {}), ensure_ascii=False),
    ]
    return " ".join(parts).lower()


def assess_mode(bundle: dict, policy: dict | None = None) -> dict:
    policy = policy or _policy()
    if not policy.get("enabled", True):
        return {"mode": "none", "reasons": ["research-driven development disabled by policy"]}

    explicit = ((bundle.get("research_rdd") or {}).get("mode") or bundle.get("discovery_mode"))
    if explicit and explicit != "auto":
        if explicit not in VALID_MODES:
            raise ValueError(f"invalid explicit research mode: {explicit}")
        return {"mode": explicit, "reasons": ["explicit discovery mode"]}

    classification = str(bundle.get("classification", "TASK")).upper()
    text = _text(bundle)
    blocking = _blocking_questions(bundle)
    routing = policy.get("routing") or {}
    reasons = []

    full_terms = {
        "irreversible", "security", "privacy", "compliance", "regulated", "financial",
        "architecture", "multi-tenant", "authorization", "authentication"
    }
    research_terms = {
        "external api", "sdk", "provider", "unknown", "unfamiliar", "research",
        "spike", "benchmark", "protocol", "standard", "integration"
    }

    if classification in set(routing.get("full_classifications", ["PROJECT", "EPIC"])):
        reasons.append(f"classification={classification}")
        return {"mode": "full", "reasons": reasons}

    if blocking >= int(routing.get("blocking_question_threshold_for_full", 3)):
        reasons.append(f"{blocking} blocking product questions")
        return {"mode": "full", "reasons": reasons}

    if any(term in text for term in full_terms) and blocking:
        reasons.append("high-cost/high-stakes domain with unresolved decisions")
        return {"mode": "full", "reasons": reasons}

    if any(term in text for term in research_terms):
        reasons.append("external/unknown evidence signal")
        return {"mode": "research", "reasons": reasons}

    if classification == "FEATURE":
        return {"mode": "light", "reasons": ["bounded feature discovery"]}

    if classification == "SPIKE":
        return {"mode": "research", "reasons": ["spike requires evidence before implementation"]}

    return {"mode": "none", "reasons": ["bounded task with no material research signal"]}


def _artifact_path(kind: str, discovery_id: str) -> Path:
    dirs = {
        "research": "planning/research",
        "domain": "planning/domain",
        "decisions": "planning/decisions",
        "scenarios": "planning/scenarios",
    }
    return ROOT / dirs[kind] / f"{discovery_id}.json"


def _skeleton(kind: str, discovery_id: str) -> dict:
    base = {
        "schema_version": 1,
        "discovery_id": discovery_id,
        "artifact_type": kind,
        "status": "draft",
    }
    if kind == "research":
        base.update({"sources": [], "findings": [], "assumptions": [], "contrary_evidence": [], "open_questions": []})
    elif kind == "domain":
        base.update({"vocabulary": [], "relationships": [], "invariants": [], "open_questions": []})
    elif kind == "decisions":
        base.update({"decisions": []})
    elif kind == "scenarios":
        base.update({"scenarios": []})
    return base


def artifact_content_failures(kind: str, data: dict, policy: dict | None = None) -> list[str]:
    policy = policy or _policy()
    failures = []
    minimums = (policy.get("minimum_content") or {}).get(kind, {})
    for field, minimum in minimums.items():
        value = data.get(field)
        if not isinstance(value, list) or len(value) < int(minimum):
            failures.append(f"{kind}.{field} requires at least {minimum} item(s)")
    for question in data.get("open_questions", []) if isinstance(data.get("open_questions", []), list) else []:
        if isinstance(question, dict) and question.get("blocking") is True:
            failures.append(f"{kind} artifact still has blocking open question")
    return failures


def required_artifacts(discovery_id: str, mode: str, policy: dict | None = None) -> list[tuple[str, Path]]:
    policy = policy or _policy()
    templates = (policy.get("artifacts") or {}).get(mode, [])
    out = []
    for template in templates:
        rel = template.format(id=discovery_id)
        kind = Path(rel).parent.name
        if kind == "research":
            artifact_type = "research"
        elif kind == "domain":
            artifact_type = "domain"
        elif kind == "decisions":
            artifact_type = "decisions"
        elif kind == "scenarios":
            artifact_type = "scenarios"
        else:
            raise ValueError(f"unsupported Research-RDD artifact path: {rel}")
        out.append((artifact_type, ROOT / rel))
    return out


def init(discovery_path: Path) -> dict:
    bundle = json.loads(discovery_path.read_text(encoding="utf-8"))
    discovery_id = str(bundle.get("discovery_id") or "").strip()
    if not discovery_id:
        raise ValueError("discovery_id is required")
    assessment = assess_mode(bundle)
    mode = assessment["mode"]
    refs = {}
    created = []
    for kind, path in required_artifacts(discovery_id, mode):
        refs[kind] = path.relative_to(ROOT).as_posix()
        if not path.exists():
            write_json_atomic(path, _skeleton(kind, discovery_id))
            created.append(refs[kind])

    rrdd = dict(bundle.get("research_rdd") or {})
    rrdd.update({
        "mode": mode,
        "assessment_reasons": assessment["reasons"],
        "artifacts": refs,
        "ready": mode in {"none", "light"},
    })
    bundle["research_rdd"] = rrdd
    write_json_atomic(discovery_path, bundle)
    return {"discovery_id": discovery_id, "mode": mode, "created": created, "artifacts": refs}


def status(discovery_path: Path) -> dict:
    policy = _policy()
    bundle = json.loads(discovery_path.read_text(encoding="utf-8"))
    discovery_id = str(bundle.get("discovery_id") or "").strip()
    if not discovery_id:
        raise ValueError("discovery_id is required")
    assessment = assess_mode(bundle, policy)
    mode = assessment["mode"]
    promotion = policy.get("promotion") or {}
    required_status = {
        "research": promotion.get("research_status_required", "complete"),
        "domain": promotion.get("domain_status_required", "complete"),
        "decisions": promotion.get("decisions_status_required", "approved"),
        "scenarios": promotion.get("scenarios_status_required", "complete"),
    }
    missing, failing, artifacts = [], [], {}
    for kind, path in required_artifacts(discovery_id, mode, policy):
        rel = path.relative_to(ROOT).as_posix()
        artifacts[kind] = rel
        if not path.is_file():
            missing.append(rel)
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            failing.append(f"{rel}: invalid JSON: {exc}")
            continue
        if data.get("discovery_id") != discovery_id or data.get("artifact_type") != kind:
            failing.append(f"{rel}: discovery/type mismatch")
            continue
        expected = required_status[kind]
        if data.get("status") != expected:
            failing.append(f"{rel}: status={data.get('status')!r}, requires {expected!r}")
            continue
        for problem in artifact_content_failures(kind, data, policy):
            failing.append(f"{rel}: {problem}")

    blocking = _blocking_questions(bundle)
    if blocking:
        failing.append(f"{blocking} blocking product question(s) remain")

    ready = not missing and not failing
    rrdd = dict(bundle.get("research_rdd") or {})
    rrdd.update({
        "mode": mode,
        "assessment_reasons": assessment["reasons"],
        "artifacts": artifacts,
        "ready": ready,
    })
    bundle["research_rdd"] = rrdd
    write_json_atomic(discovery_path, bundle)
    return {
        "discovery_id": discovery_id,
        "mode": mode,
        "ready": ready,
        "missing": missing,
        "failing": failing,
        "artifacts": artifacts,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Adaptive Research-Driven Development discovery control plane.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("assess", "init", "status"):
        p = sub.add_parser(name)
        p.add_argument("discovery")
    args = ap.parse_args()
    try:
        path = _resolve_discovery(args.discovery)
        if args.cmd == "assess":
            bundle = json.loads(path.read_text(encoding="utf-8"))
            out = assess_mode(bundle)
        elif args.cmd == "init":
            out = init(path)
        else:
            out = status(path)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        if args.cmd == "status" and not out.get("ready"):
            return 2
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}, indent=2, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
