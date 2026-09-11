#!/usr/bin/env python
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

from harnesslib import ROOT, load_json, load_manifest, run_dir, safe_task_id, write_json_atomic

POLICY_PATH = "harness/receipt-policy.json"
RUNTIME = ROOT / ".harness" / "receipt-review"


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def policy() -> dict:
    return load_json(POLICY_PATH)


def _json_digest(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _run_bytes(*args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd or ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


def _run_text(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=cwd or ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)
    return r.stdout


def mode_status() -> dict:
    p = RUNTIME / "mode.json"
    default = bool(policy().get("enabled_by_default", False))
    if not p.is_file():
        return {"mode": "on" if default else "off", "source": "policy_default"}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        mode = data.get("mode")
        if mode not in {"on", "off"}:
            raise ValueError("mode must be on/off")
        return {"mode": mode, "source": "clone_local", "updated_at": data.get("updated_at")}
    except Exception as exc:
        return {"mode": "unknown", "source": "invalid_clone_local", "reason": str(exc)}


def set_mode(enabled: bool) -> dict:
    data = {"schema_version": 1, "mode": "on" if enabled else "off", "updated_at": now()}
    write_json_atomic(RUNTIME / "mode.json", data)
    return mode_status()


def _active_provider(task: str) -> str | None:
    found = []
    for provider in ("codex", "opencode"):
        p = ROOT / ".harness" / provider / "active-task.json"
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("task_id") == task:
            found.append(provider)
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        raise ValueError("task is simultaneously active in more than one provider")
    return None


def _session_id(provider: str, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("HARNESS_SESSION_ID")
    if env:
        return env
    p = ROOT / ".harness" / provider / "session.json"
    if not p.is_file():
        raise ValueError(f"current {provider} session id unavailable; start/resume the provider session first or pass --session-id")
    data = json.loads(p.read_text(encoding="utf-8"))
    sid = data.get("session_id")
    if not isinstance(sid, str) or not sid:
        raise ValueError(f"current {provider} session id is invalid")
    return sid


def _consent_path(provider: str, session_id: str) -> Path:
    key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return RUNTIME / "consent" / provider / f"{key}.json"


def consent_status(task: str, provider: str | None = None, session_id: str | None = None) -> dict:
    task = safe_task_id(task)
    provider = provider or _active_provider(task)
    if provider not in {"codex", "opencode"}:
        raise ValueError("cannot resolve active provider for session-scoped review consent")
    sid = _session_id(provider, session_id)
    p = _consent_path(provider, sid)
    granted = False
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            granted = data.get("granted") is True and data.get("session_hash") == hashlib.sha256(sid.encode()).hexdigest()
        except Exception:
            granted = False
    return {"provider": provider, "session_id": sid, "granted": granted, "path": p.relative_to(ROOT).as_posix()}


def grant_consent(task: str, provider: str | None = None, session_id: str | None = None) -> dict:
    task = safe_task_id(task)
    provider = provider or _active_provider(task)
    if provider not in {"codex", "opencode"}:
        raise ValueError("cannot resolve active provider for session-scoped review consent")
    sid = _session_id(provider, session_id)
    digest = hashlib.sha256(sid.encode()).hexdigest()
    p = _consent_path(provider, sid)
    write_json_atomic(p, {
        "schema_version": 1,
        "provider": provider,
        "session_hash": digest,
        "granted": True,
        "granted_at": now(),
        "scope": "provider_session",
    })
    return consent_status(task, provider, sid)


def clear_consent(task: str, provider: str | None = None, session_id: str | None = None) -> dict:
    task = safe_task_id(task)
    provider = provider or _active_provider(task)
    if provider not in {"codex", "opencode"}:
        raise ValueError("cannot resolve active provider")
    sid = _session_id(provider, session_id)
    _consent_path(provider, sid).unlink(missing_ok=True)
    return {"provider": provider, "session_id": sid, "granted": False}


def _task_route(task: str) -> dict:
    p = run_dir(task) / "route.json"
    if not p.is_file():
        raise ValueError("route.json missing")
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("task_id") != task:
        raise ValueError("route task mismatch")
    return data


def _lock_or_publish(task: str) -> tuple[str, str | None, Path | None]:
    lock = ROOT / ".harness" / "locks" / f"{task}.json"
    if lock.is_file():
        data = json.loads(lock.read_text(encoding="utf-8"))
        base = data.get("base_commit")
        worktree = Path(data.get("worktree", ""))
        if not base or not worktree.is_dir():
            raise ValueError("writer lock lacks valid base/worktree")
        return base, None, worktree

    pub = run_dir(task) / "publish.json"
    if pub.is_file():
        data = json.loads(pub.read_text(encoding="utf-8"))
        if data.get("status") == "PASS" and data.get("base_commit") and data.get("commit"):
            return data["base_commit"], data["commit"], None
    raise ValueError("candidate worktree/published commit unavailable")


def _changed_paths_worktree(base: str, cwd: Path) -> list[str]:
    tracked = _run_text("diff", "--no-renames", "--name-only", base, "--", cwd=cwd).splitlines()
    untracked = _run_text("ls-files", "--others", "--exclude-standard", cwd=cwd).splitlines()
    return sorted({x.strip().replace("\\", "/") for x in tracked + untracked if x.strip()})


def _changed_paths_commit(base: str, commit: str) -> list[str]:
    lines = _run_text("diff", "--no-renames", "--name-only", f"{base}..{commit}", "--", cwd=ROOT).splitlines()
    return sorted({x.strip().replace("\\", "/") for x in lines if x.strip()})


def _worktree_entry(cwd: Path, rel: str) -> dict:
    p = cwd / rel
    try:
        st = os.lstat(p)
    except FileNotFoundError:
        return {"path": rel, "kind": "deleted", "mode": "000000", "sha256": None, "size": 0}
    if stat.S_ISLNK(st.st_mode):
        raw = os.readlink(p).encode("utf-8", errors="surrogateescape")
        return {"path": rel, "kind": "symlink", "mode": "120000", "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"unsupported candidate path type: {rel}")
    raw = p.read_bytes()
    mode = "100755" if (st.st_mode & 0o111) else "100644"
    return {"path": rel, "kind": "file", "mode": mode, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}


def _commit_entry(commit: str, rel: str) -> dict:
    tree = _run_text("ls-tree", commit, "--", rel, cwd=ROOT).strip()
    if not tree:
        return {"path": rel, "kind": "deleted", "mode": "000000", "sha256": None, "size": 0}
    left, _, _path = tree.partition("\t")
    mode, typ, _obj = left.split()
    if typ != "blob":
        raise ValueError(f"unsupported committed candidate path type: {rel}")
    raw = _run_bytes("show", f"{commit}:{rel}", cwd=ROOT).stdout
    kind = "symlink" if mode == "120000" else "file"
    return {"path": rel, "kind": kind, "mode": mode, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}


def _changed_lines(base: str, commit: str | None, cwd: Path | None, paths: list[str]) -> int:
    if commit:
        out = _run_text("diff", "--numstat", f"{base}..{commit}", "--", cwd=ROOT)
    else:
        out = _run_text("diff", "--numstat", base, "--", cwd=cwd)
    total = 0
    seen = set()
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            seen.add(parts[-1].replace("\\", "/"))
            for value in parts[:2]:
                if value.isdigit():
                    total += int(value)
    if cwd:
        for rel in paths:
            if rel in seen:
                continue
            p = cwd / rel
            if p.is_file() and not p.is_symlink():
                try:
                    total += len(p.read_text(encoding="utf-8").splitlines())
                except UnicodeDecodeError:
                    pass
    return total


def candidate_snapshot(task: str) -> dict:
    task = safe_task_id(task)
    base, commit, worktree = _lock_or_publish(task)
    paths = _changed_paths_commit(base, commit) if commit else _changed_paths_worktree(base, worktree)
    if commit:
        entries = [_commit_entry(commit, rel) for rel in paths]
    else:
        entries = [_worktree_entry(worktree, rel) for rel in paths]
    subject = {"base_commit": base, "files": entries}
    return {
        "schema_version": 1,
        "task_id": task,
        "base_commit": base,
        "published_commit": commit,
        "files": entries,
        "changed_paths": len(entries),
        "changed_lines": _changed_lines(base, commit, worktree, paths),
        "subject_hash": _json_digest(subject),
    }


def classify_snapshot(snapshot: dict, route: dict, cfg: dict | None = None) -> tuple[str, list[str]]:
    cfg = cfg or policy().get("assessment", {})
    entries = snapshot.get("files", [])
    paths = [str(x.get("path", "")).lower() for x in entries]
    reasons: list[str] = []
    task_risk = route.get("risk", "R1")
    if task_risk in {"R2", "R3"}:
        return "high", [f"existing harness risk {task_risk}"]

    high_fragments = [x.lower() for x in cfg.get("high_path_fragments", [])]
    if any(any(fragment in ("/" + p) for fragment in high_fragments) for p in paths):
        return "high", ["security/production/high-impact path signal"]

    if snapshot.get("changed_paths", 0) >= int(cfg.get("high_changed_paths", 20)):
        return "high", ["large changed-path surface"]
    if snapshot.get("changed_lines", 0) >= int(cfg.get("high_changed_lines", 500)):
        return "high", ["large changed-line surface"]

    passive_ext = set(cfg.get("passive_extensions", []))
    passive_prefixes = tuple(cfg.get("passive_prefixes", []))
    passive = bool(entries)
    for entry in entries:
        rel = str(entry.get("path", ""))
        suffix = Path(rel).suffix.lower()
        if not (suffix in passive_ext or rel.startswith(passive_prefixes)):
            passive = False
            break
        if entry.get("mode") == "100755":
            passive = False
            break
    if passive:
        return "passive", ["only passive documentation/planning bytes changed"]
    return "medium", reasons or ["ordinary executable/configuration candidate"]


def assess(task: str) -> dict:
    task = safe_task_id(task)
    route = _task_route(task)
    snap = candidate_snapshot(task)
    risk, reasons = classify_snapshot(snap, route)
    out = {
        "schema_version": 1,
        "task_id": task,
        "risk": risk,
        "reasons": reasons,
        "subject_hash": snap["subject_hash"],
        "changed_paths": snap["changed_paths"],
        "changed_lines": snap["changed_lines"],
        "base_commit": snap["base_commit"],
    }
    write_json_atomic(run_dir(task) / "receipt-assessment.json", out)
    return out


def _small_implementer(task: str) -> tuple[bool, str]:
    p = run_dir(task) / "model-selections.json"
    if not p.is_file():
        return False, "no explicit implementer model selection"
    data = json.loads(p.read_text(encoding="utf-8"))
    cfg = policy().get("assessment", {})
    name_fragments = [x.lower() for x in cfg.get("small_model_name_fragments", [])]
    efforts = {x.lower() for x in cfg.get("small_model_efforts", [])}
    for selection in data.get("selections", []):
        if selection.get("agent") != "implementer":
            continue
        model = str(selection.get("model_id") or selection.get("base_model_id") or "").lower()
        effort = str(selection.get("reasoning_effort") or "").lower()
        if effort in efforts:
            return True, f"implementer reasoning_effort={effort}"
        if any(x in model for x in name_fragments):
            return True, f"small-model name signal: {model}"
        return False, f"implementer model={model or 'inherited'}"
    return False, "implementer selection inherited/unknown"


def _authoritative_checks_pass(task: str) -> bool:
    p = run_dir(task) / "checks-report.json"
    if not p.is_file():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return False
    return data.get("task_id") == task and data.get("status") == "PASS"


def freeze(task: str) -> dict:
    snap = candidate_snapshot(task)
    payload = dict(snap)
    payload["frozen_at"] = now()
    write_json_atomic(run_dir(task) / "receipt-frozen.json", payload)
    return payload


def _route_write(task: str, route: dict) -> None:
    write_json_atomic(run_dir(task) / "route.json", route)


def _selected_identity(selection: dict) -> tuple[str | None, str | None, str | None]:
    if selection.get("action") != "use" or selection.get("status") != "selected":
        return None, None, None
    model = str(selection.get("base_model_id") or selection.get("model_id") or "") or None
    family = str(selection.get("model_family") or "") or None
    vendor = str(selection.get("model_vendor") or "") or None
    return model, family, vendor


def _ensure_dynamic_agent_models(task: str, route: dict, added_agents: list[str]) -> list[dict]:
    if not added_agents:
        return []
    models_path = run_dir(task) / "model-selections.json"
    task_path = run_dir(task) / "task.json"
    if not models_path.is_file() or not task_path.is_file():
        raise ValueError("dynamic review/verification routing requires task snapshot and model-selections.json")

    payload = json.loads(models_path.read_text(encoding="utf-8"))
    provider = payload.get("provider") or _active_provider(task)
    if provider not in {"codex", "opencode"}:
        raise ValueError("dynamic review/verification routing requires a supported active provider")
    task_data = json.loads(task_path.read_text(encoding="utf-8"))

    from model_router import load_inventory as load_model_inventory, load_policy as load_model_policy, select_model
    from model_task_profile import profile_task, target_for_agent

    inventory_path = payload.get("inventory_path")
    inventory = None
    if inventory_path:
        inventory, _ = load_model_inventory(provider, str(inventory_path))
    model_policy = load_model_policy()
    manifest = load_manifest()
    task_profile = profile_task(task_data, route.get("risk", "R1"), model_policy)
    selections = list(payload.get("selections") or [])
    by_agent = {str(x.get("agent")): x for x in selections if x.get("agent")}
    created = []

    for agent in added_agents:
        if agent in by_agent:
            continue
        if agent not in manifest.get("agents", {}):
            raise ValueError(f"dynamic route references unknown agent: {agent}")
        independence_cfg = (model_policy.get("independence") or {}).get("roles", {}).get(agent, {})
        previous = [by_agent[x] for x in independence_cfg.get("avoid_agents", []) if x in by_agent]
        identities = [_selected_identity(x) for x in previous]
        avoid_models = {x[0] for x in identities if x[0]}
        avoid_families = {x[1] for x in identities if x[1]}
        avoid_vendors = {x[2] for x in identities if x[2]}
        meta = manifest["agents"][agent]
        target = target_for_agent(task_profile, agent, route.get("risk", "R1"), model_policy)
        selection = select_model(
            task_id=task, provider=provider, agent=agent, model_class=meta["model_class"],
            risk=route.get("risk", "R1"), inventory=inventory, policy=model_policy, target=target,
            avoid_models=avoid_models, avoid_families=avoid_families, avoid_vendors=avoid_vendors,
        )
        selection["task_profile"] = task_profile
        if selection.get("action") == "block":
            raise ValueError(f"dynamic agent {agent} has no eligible model: {selection.get('reason')}")
        selections.append(selection)
        by_agent[agent] = selection
        created.append(selection)

    if not created:
        return []

    payload["selections"] = selections
    write_json_atomic(models_path, payload)
    active_path = ROOT / ".harness" / provider / "active-task.json"
    if active_path.is_file():
        active = json.loads(active_path.read_text(encoding="utf-8"))
        if active.get("task_id") == task:
            active["selections"] = selections
            active["agents"] = route.get("agents", [])
            write_json_atomic(active_path, active)

    if provider == "codex":
        result = subprocess.run([sys.executable, "scripts/compile_harness.py"], cwd=ROOT)
        if result.returncode != 0:
            raise ValueError("failed to regenerate Codex adapters for dynamically routed agent model")
    return created


def prepare(task: str, apply: bool = False) -> dict:
    task = safe_task_id(task)
    mode = mode_status()
    assessment = assess(task)
    route = _task_route(task)
    original_requires_verifier = bool((route.get("requirements") or {}).get("verification")) or "verifier" in route.get("agents", [])
    small, small_reason = _small_implementer(task)

    receipt_on = mode["mode"] == "on"
    unknown = mode["mode"] == "unknown"
    tier = "high" if unknown else assessment["risk"]

    independent = original_requires_verifier
    reasons = []
    if original_requires_verifier:
        reasons.append("existing harness route already requires verifier")
    elif receipt_on:
        independent = False
        reasons.append("Receipt-RDD reviewer is the independent R1 check")
    else:
        if tier == "high":
            independent = True
            reasons.append("Receipt-RDD off/unknown and assess=high")
        elif tier == "medium" and small:
            independent = True
            reasons.append("Receipt-RDD off and medium candidate uses small/low-effort implementer")
        else:
            reasons.append(f"Receipt-RDD {mode['mode']}; {tier} candidate does not require separate verifier")

    original_agents = list(route.get("agents", []))
    agents = list(original_agents)
    requirements = dict(route.get("requirements") or {})
    requires_reviewer = "reviewer" in agents or (receipt_on and tier != "passive")
    if requires_reviewer and "reviewer" not in agents:
        agents.append("reviewer")
    if independent and "verifier" not in agents:
        agents.append("verifier")
    if independent:
        requirements["verification"] = True

    receipt_kind = "none"
    if receipt_on:
        receipt_kind = "review" if requires_reviewer else "structural"

    plan = {
        "schema_version": 1,
        "task_id": task,
        "mode": mode["mode"],
        "mode_source": mode.get("source"),
        "assessment": tier,
        "assessment_subject_hash": assessment["subject_hash"],
        "small_implementer": small,
        "small_implementer_reason": small_reason,
        "independent_verifier": independent,
        "requires_reviewer": requires_reviewer,
        "consent_required": receipt_on and receipt_kind == "review",
        "receipt_required": receipt_on,
        "receipt_kind": receipt_kind,
        "reasons": reasons,
    }
    plan["plan_hash"] = _json_digest(plan)
    write_json_atomic(run_dir(task) / "verification-plan.json", plan)

    if apply:
        route["agents"] = list(dict.fromkeys(agents))
        route["requirements"] = requirements
        skills = list(route.get("skills", []))
        if independent and "verification" not in skills:
            skills.append("verification")
        route["skills"] = list(dict.fromkeys(skills))
        route["receipt_review"] = {
            "mode": plan["mode"],
            "assessment": plan["assessment"],
            "consent_required": plan["consent_required"],
            "receipt_required": plan["receipt_required"],
            "receipt_kind": plan["receipt_kind"],
            "independent_verifier": plan["independent_verifier"],
            "plan_hash": plan["plan_hash"],
        }
        added_agents = [agent for agent in route["agents"] if agent not in original_agents]
        _ensure_dynamic_agent_models(task, route, added_agents)
        _route_write(task, route)

        if receipt_on:
            frozen = freeze(task)
            if receipt_kind == "structural":
                if not _authoritative_checks_pass(task):
                    raise ValueError("passive structural receipt requires authoritative PASS checks")
                issue_structural(task, frozen=frozen)

    return plan


def verification_finish_decision(task: str) -> dict:
    task = safe_task_id(task)
    ap = run_dir(task) / "receipt-assessment.json"
    pp = run_dir(task) / "verification-plan.json"
    if not ap.is_file() or not pp.is_file():
        return {"allow": False, "required": ["receipt-assessment", "verification-plan"], "missing": [
            x for x, p in (("receipt-assessment", ap), ("verification-plan", pp)) if not p.is_file()
        ], "failing": []}
    try:
        assessment = json.loads(ap.read_text(encoding="utf-8"))
        plan = json.loads(pp.read_text(encoding="utf-8"))
        route = _task_route(task)
        snap = candidate_snapshot(task)
    except Exception as exc:
        return {"allow": False, "required": ["receipt-assessment", "verification-plan"], "missing": [], "failing": [str(exc)]}
    failing = []
    if assessment.get("subject_hash") != snap.get("subject_hash"):
        failing.append("candidate_changed_after_assess")
    rr = route.get("receipt_review") or {}
    if rr.get("plan_hash") != plan.get("plan_hash"):
        failing.append("route_verification_plan_mismatch")
    if mode_status().get("mode") != plan.get("mode"):
        failing.append("receipt_mode_changed_reassess")
    return {"allow": not failing, "required": ["receipt-assessment", "verification-plan"], "missing": [], "failing": failing}


def consent_decision(task: str) -> dict:
    route = _task_route(task)
    rr = route.get("receipt_review") or {}
    if not rr.get("consent_required"):
        return {"allow": True, "required": [], "missing": [], "failing": []}
    try:
        status = consent_status(task)
    except Exception as exc:
        return {"allow": False, "required": ["session-review-consent"], "missing": ["session-review-consent"], "failing": [str(exc)]}
    return {
        "allow": bool(status.get("granted")),
        "required": ["session-review-consent"],
        "missing": [] if status.get("granted") else ["session-review-consent"],
        "failing": [],
        "status": status,
    }


def _load_frozen(task: str) -> dict:
    p = run_dir(task) / "receipt-frozen.json"
    if not p.is_file():
        raise ValueError("receipt-frozen.json missing; freeze candidate before review")
    return json.loads(p.read_text(encoding="utf-8"))


def _receipt_payload(task: str, kind: str, handoff: Path | None, actor: str) -> dict:
    frozen = _load_frozen(task)
    current = candidate_snapshot(task)
    if current.get("subject_hash") != frozen.get("subject_hash"):
        raise ValueError("candidate changed after freeze; review/receipt must restart")
    if mode_status().get("mode") != "on":
        raise ValueError("Receipt-RDD is not enabled")

    handoff_hash = None
    if handoff is not None:
        data = json.loads(handoff.read_text(encoding="utf-8"))
        if data.get("task_id") != task or data.get("status") != "PASS":
            raise ValueError("review handoff is not authoritative PASS for this task")
        handoff_hash = hashlib.sha256(handoff.read_bytes()).hexdigest()

    body = {
        "schema_version": 1,
        "task_id": task,
        "subject_hash": frozen["subject_hash"],
        "kind": kind,
        "status": "PASS",
        "actor": actor,
        "review_handoff_sha256": handoff_hash,
        "assessment_path": f".harness/runs/{task}/receipt-assessment.json",
        "frozen_path": f".harness/runs/{task}/receipt-frozen.json",
        "issued_at": now(),
    }
    body["receipt_hash"] = _json_digest(body)
    return body


def issue_review(task: str, handoff: Path) -> dict:
    decision = consent_decision(task)
    if not decision.get("allow"):
        raise ValueError("session review consent missing")
    payload = _receipt_payload(task, "review", handoff, "reviewer")
    write_json_atomic(run_dir(task) / "receipt.json", payload)
    return payload


def issue_structural(task: str, frozen: dict | None = None) -> dict:
    if not _authoritative_checks_pass(task):
        raise ValueError("structural receipt requires authoritative PASS checks")
    assessment_path = run_dir(task) / "receipt-assessment.json"
    if not assessment_path.is_file():
        raise ValueError("receipt assessment missing")
    assessment = json.loads(assessment_path.read_text(encoding="utf-8"))
    if assessment.get("risk") != "passive":
        raise ValueError("structural receipt is only valid for passive candidates")
    if frozen is None:
        frozen = _load_frozen(task)
    payload = _receipt_payload(task, "structural", None, "check-runner")
    write_json_atomic(run_dir(task) / "receipt.json", payload)
    return payload


def receipt_validate(task: str) -> dict:
    task = safe_task_id(task)
    p = run_dir(task) / "receipt.json"
    if not p.is_file():
        return {"allow": False, "required": ["receipt"], "missing": ["receipt"], "failing": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        snap = candidate_snapshot(task)
    except Exception as exc:
        return {"allow": False, "required": ["receipt"], "missing": [], "failing": [str(exc)]}
    failing = []
    saved_hash = data.get("receipt_hash")
    body = dict(data)
    body.pop("receipt_hash", None)
    if saved_hash != _json_digest(body):
        failing.append("receipt_integrity")
    if data.get("subject_hash") != snap.get("subject_hash"):
        failing.append("receipt_subject_changed")
    if data.get("kind") == "review":
        handoff = run_dir(task) / "handoffs" / "reviewer.json"
        if not handoff.is_file():
            failing.append("review_handoff_missing")
        elif data.get("review_handoff_sha256") != hashlib.sha256(handoff.read_bytes()).hexdigest():
            failing.append("review_handoff_changed")
    return {"allow": not failing, "required": ["receipt"], "missing": [], "failing": failing, "receipt": data}


def finish_decision(task: str) -> dict:
    task = safe_task_id(task)
    # Backward compatibility: Receipt-RDD becomes authoritative only after the
    # task route has been explicitly prepared at VERIFY_ASSESS. Historical
    # runs and ordinary unit-test fixtures do not carry `route.receipt_review`
    # and must retain the original harness finish contract. This does not let
    # a real VERIFY_ASSESS stage pass without preparation because that stage
    # calls verification_finish_decision() directly, which remains fail-closed.
    try:
        route = _task_route(task)
    except Exception as exc:
        return {"allow": False, "required": [], "missing": [], "failing": [str(exc)]}
    if not isinstance(route.get("receipt_review"), dict):
        return {"allow": True, "required": [], "missing": [], "failing": []}

    base = verification_finish_decision(task)
    required = list(base.get("required", []))
    missing = list(base.get("missing", []))
    failing = list(base.get("failing", []))
    if not base.get("allow"):
        return {"allow": False, "required": required, "missing": missing, "failing": failing}

    plan = json.loads((run_dir(task) / "verification-plan.json").read_text(encoding="utf-8"))
    if plan.get("mode") == "on":
        receipt = receipt_validate(task)
        required += receipt.get("required", [])
        missing += receipt.get("missing", [])
        failing += receipt.get("failing", [])
    return {"allow": not missing and not failing, "required": required, "missing": missing, "failing": failing}


def _resolve_handoff(value: str) -> Path:
    p = Path(value)
    p = p if p.is_absolute() else ROOT / p
    p = p.resolve()
    p.relative_to(ROOT.resolve())
    if not p.is_file():
        raise ValueError("handoff file not found")
    return p


def main() -> int:
    ap = argparse.ArgumentParser(description="Receipt-Driven Development candidate assessment and receipts.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("mode")
    p.add_argument("action", choices=["status", "enable", "disable"])

    p = sub.add_parser("assess")
    p.add_argument("task")

    p = sub.add_parser("prepare")
    p.add_argument("task")
    p.add_argument("--apply", action="store_true")

    p = sub.add_parser("freeze")
    p.add_argument("task")

    p = sub.add_parser("consent")
    p.add_argument("action", choices=["status", "grant", "clear"])
    p.add_argument("task")
    p.add_argument("--provider", choices=["codex", "opencode"])
    p.add_argument("--session-id")

    p = sub.add_parser("issue")
    p.add_argument("task")
    p.add_argument("--review-handoff")
    p.add_argument("--structural", action="store_true")

    p = sub.add_parser("validate")
    p.add_argument("task")

    p = sub.add_parser("finish")
    p.add_argument("task")

    args = ap.parse_args()
    try:
        if args.cmd == "mode":
            out = mode_status() if args.action == "status" else set_mode(args.action == "enable")
        elif args.cmd == "assess":
            out = assess(args.task)
        elif args.cmd == "prepare":
            out = prepare(args.task, args.apply)
        elif args.cmd == "freeze":
            out = freeze(args.task)
        elif args.cmd == "consent":
            fn = {"status": consent_status, "grant": grant_consent, "clear": clear_consent}[args.action]
            out = fn(args.task, args.provider, args.session_id)
        elif args.cmd == "issue":
            if args.structural:
                out = issue_structural(args.task)
            elif args.review_handoff:
                out = issue_review(args.task, _resolve_handoff(args.review_handoff))
            else:
                raise ValueError("issue requires --review-handoff or --structural")
        elif args.cmd == "validate":
            out = receipt_validate(args.task)
        else:
            out = finish_decision(args.task)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        if args.cmd in {"validate", "finish"} and not out.get("allow"):
            return 2
        if args.cmd == "consent" and args.action == "status" and not out.get("granted"):
            return 2
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}, indent=2, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
