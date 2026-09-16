#!/usr/bin/env python3
"""Route harness roles across subscription-authenticated official coding CLIs.

Supported runtimes: Codex, Claude Code, GitHub Copilot CLI, Cursor CLI,
Grok Build, and Gemini CLI. The bridge does not read browser cookies or provider
credential stores and defaults to removing direct model API credentials from
child environments.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from harnesslib import load_manifest, run_dir, safe_task_id, write_json_atomic  # noqa: E402
from task_router import route  # noqa: E402
from request_normalizer import normalize_task  # noqa: E402
from context_compiler import build as build_context  # noqa: E402
from orchestrator import init_progress  # noqa: E402
from impact_analysis import build_plan as build_impact_plan, capture_baseline as capture_impact_baseline  # noqa: E402
from agent_budget import init as init_agent_budget  # noqa: E402
from model_router import selections_for_task  # noqa: E402
from subscription_runtime import execute, find_executable, provider_config, sanitized_environment  # noqa: E402
from skill_compiler import compile_skill_pack  # noqa: E402
from provider_capabilities import probe as probe_capabilities  # noqa: E402

CONFIG_PATH = ROOT / "harness" / "subscription-providers.json"
INVENTORY_PATH = ROOT / ".harness" / "model-inventories" / "subscriptions.json"
ACTIVE_PATH = ROOT / ".harness" / "subscriptions" / "active-task.json"

PROFILES: dict[str, dict[str, Any]] = {
    "fast": {"capabilities": {"reasoning": 3.2, "coding": 3.2, "tool_use": 4.0, "reliability": 3.5}, "cost": 5.0, "latency": 4.8},
    "fast-coding": {"capabilities": {"reasoning": 3.5, "coding": 4.3, "tool_use": 4.3, "reliability": 3.6}, "cost": 4.8, "latency": 4.6},
    "balanced": {"capabilities": {"reasoning": 4.4, "coding": 4.5, "tool_use": 4.5, "reliability": 4.4}, "cost": 4.2, "latency": 3.9},
    "coding": {"capabilities": {"reasoning": 4.6, "coding": 5.0, "tool_use": 4.9, "reliability": 4.5}, "cost": 4.0, "latency": 3.6},
    "reasoning": {"capabilities": {"reasoning": 5.0, "coding": 4.4, "tool_use": 4.5, "reliability": 4.8}, "cost": 3.6, "latency": 3.2},
    "premium": {"capabilities": {"reasoning": 5.0, "coding": 4.8, "tool_use": 4.7, "reliability": 5.0}, "cost": 3.1, "latency": 2.8},
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _run_status(argv: list[str], provider: str, timeout: int = 12) -> tuple[int, str]:
    env, _ = sanitized_environment(provider)
    try:
        proc = subprocess.run(argv, cwd=ROOT, env=env, text=True, capture_output=True, timeout=timeout, check=False)
        return proc.returncode, (proc.stdout + "\n" + proc.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 124, str(exc)


def doctor_provider(provider: str) -> dict[str, Any]:
    cfg = provider_config(provider)
    executable = find_executable(provider)
    present_api_env = [key for key in cfg.get("forbidden_env", []) if os.getenv(str(key))]
    row: dict[str, Any] = {
        "provider": provider,
        "installed": bool(executable),
        "executable": executable,
        "auth_state": "missing-cli" if not executable else "unknown",
        "direct_api_env_present": present_api_env,
        "direct_api_env_will_be_removed": bool(present_api_env),
    }
    if not executable:
        return row
    code, version = _run_status([executable, "--version"], provider, timeout=6)
    row["version_ok"] = code == 0
    if code == 0:
        row["version"] = version.splitlines()[0][:300] if version else "unknown"
    row["runtime_capabilities"] = probe_capabilities(provider, executable)

    status_cmd = cfg.get("status_command")
    if status_cmd:
        argv = [executable, *[str(x) for x in status_cmd[1:]]]
        code, text = _run_status(argv, provider)
        low = text.lower()
        if code == 0:
            if any(p in low for p in cfg.get("api_status_patterns", [])):
                row["auth_state"] = "api-key"
            elif any(p in low for p in cfg.get("subscription_status_patterns", [])):
                row["auth_state"] = "subscription"
            elif provider == "grok":
                row["auth_state"] = "subscription-or-local-auth"
            else:
                row["auth_state"] = "authenticated-unknown-method"
        else:
            row["auth_state"] = "not-authenticated-or-status-failed"
    elif provider == "claude":
        code, text = _run_status([executable, "auth", "status", "--json"], provider)
        if code == 0:
            low = text.lower()
            row["auth_state"] = "subscription" if any(x in low for x in ("claude.ai", "subscription", "oauth")) else "authenticated-unknown-method"
    elif provider == "copilot":
        # Copilot's secure local OAuth store has no stable non-interactive
        # status verb. Do not inspect it. Runtime invocation is the final check.
        row["auth_state"] = "unknown-run-copilot-login-if-needed"
    elif provider == "gemini":
        row["auth_state"] = "unknown-run-gemini-auth-if-needed"
    return row


def doctor() -> dict[str, Any]:
    cfg = load_config()
    rows = [doctor_provider(p) for p in cfg.get("providers", {})]
    return {
        "schema_version": 1,
        "strict_subscription_auth": bool(cfg.get("strict_subscription_auth", True)),
        "providers": rows,
        "usable_or_unknown": [r["provider"] for r in rows if r["installed"] and r["auth_state"] != "api-key"],
        "note": "The bridge never reads provider credential files; direct model API environment variables are removed from child processes by default.",
    }


def _extract_model_ids(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, dict):
        for key in ("id", "model", "model_id", "name"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                out.append(candidate.strip())
        for child in value.values():
            out.extend(_extract_model_ids(child))
    elif isinstance(value, list):
        for child in value:
            out.extend(_extract_model_ids(child))
    return out


def _discover_codex_models() -> list[dict[str, Any]]:
    try:
        from openrouter_sync import discover_provider, load_provider_config
        rows = discover_provider("codex", load_provider_config("codex"))
        return [{"id": str(x["id"]), "profile": "coding", "vendor": "openai", "supported_efforts": x.get("supported_efforts") or []} for x in rows if x.get("id")]
    except Exception:
        return []


def _discover_grok_models(executable: str) -> list[dict[str, Any]]:
    code, text = _run_status([executable, "models"], "grok", timeout=15)
    if code != 0:
        return []
    ids: list[str] = []
    try:
        ids.extend(_extract_model_ids(json.loads(text)))
    except Exception:
        for line in text.splitlines():
            # Model IDs are printed one per row in current Grok Build. Keep this
            # deliberately conservative to avoid treating headings as models.
            for token in re.findall(r"\b(?:grok[-/:][A-Za-z0-9._:-]+|grok-[A-Za-z0-9._:-]+)\b", line, re.I):
                ids.append(token)
    return [{"id": x, "profile": "balanced", "vendor": "xai"} for x in dict.fromkeys(ids)]


def _model_profile(model_id: str, declared: str | None) -> str:
    if declared in PROFILES:
        return str(declared)
    low = model_id.lower()
    if any(x in low for x in ("haiku", "flash", "mini", "luna")):
        return "fast"
    if "codex" in low or "code" in low:
        return "coding"
    if any(x in low for x in ("opus", "astra", "reason", "pro")):
        return "reasoning"
    return "balanced"


def _inventory_row(provider: str, item: dict[str, Any]) -> dict[str, Any]:
    model = str(item["id"])
    vendor = str(item.get("vendor") or provider_config(provider).get("vendor") or provider)
    profile_name = _model_profile(model, item.get("profile"))
    profile = PROFILES[profile_name]
    efforts = item.get("supported_efforts")
    if not isinstance(efforts, list) or not efforts:
        efforts = ["low", "medium", "high", "xhigh"] if provider in {"codex", "claude", "copilot", "grok"} else []
    return {
        "id": f"{provider}/{model}",
        "runtime_provider": provider,
        "runtime_model": model,
        "vendor": vendor,
        "family": f"{vendor}/{model}",
        "native": True,
        "enabled": True,
        "capabilities": dict(profile["capabilities"]),
        "cost": profile["cost"],
        "latency": profile["latency"],
        "supported_efforts": efforts,
        "subscription_quota_profile": profile_name,
        "availability_source": "official-cli",
        "availability_generated_at": now_iso(),
    }


def build_inventory(providers: list[str] | None = None, *, include_missing: bool = False) -> dict[str, Any]:
    cfg = load_config()
    requested = providers or list(cfg.get("providers", {}).keys())
    provider_status: dict[str, Any] = {}
    models: list[dict[str, Any]] = []
    for provider in requested:
        if provider not in cfg.get("providers", {}):
            raise ValueError(f"unknown subscription provider: {provider}")
        status = doctor_provider(provider)
        provider_status[provider] = status
        if not status["installed"] and not include_missing:
            continue
        if status.get("auth_state") == "api-key":
            # A stored API-key auth mode is explicitly outside the user's goal.
            continue
        pcfg = provider_config(provider)
        declared: list[dict[str, Any]] = []
        if provider == "codex" and status["installed"]:
            declared = _discover_codex_models()
        elif provider == "grok" and status["installed"]:
            declared = _discover_grok_models(status["executable"])
        if not declared:
            declared = list(pcfg.get("models") or pcfg.get("fallback_models") or [])
        for item in declared:
            if isinstance(item, dict) and item.get("id"):
                models.append(_inventory_row(provider, item))
    stamp = now_iso()
    return {
        "schema_version": 1,
        "provider": "subscriptions",
        "generated_at": stamp,
        "availability_generated_at": stamp,
        "requested_providers": list(requested),
        "models": models,
        "providers": provider_status,
        "note": "cost is a relative subscription-quota-efficiency score, not API price",
    }


def save_inventory(payload: dict[str, Any], path: Path = INVENTORY_PATH) -> Path:
    write_json_atomic(path, payload)
    return path

def _provider_scope_for_refresh(providers: list[str] | None) -> list[str] | None:
    """Preserve the user's active subscription-provider scope.

    An explicit provider list always wins. When no list is supplied by the
    caller, reuse the provider scope stored in the current subscriptions
    inventory instead of silently expanding back to every configured runtime.
    """
    if providers is not None:
        return list(dict.fromkeys(str(p) for p in providers if str(p)))

    if not INVENTORY_PATH.is_file():
        return None

    try:
        existing = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None

    requested = existing.get("requested_providers")
    if isinstance(requested, list):
        cleaned = [str(p) for p in requested if isinstance(p, str) and p]
        if cleaned:
            return list(dict.fromkeys(cleaned))

    provider_rows = existing.get("providers")
    if isinstance(provider_rows, dict):
        cleaned = [str(p) for p in provider_rows.keys() if isinstance(p, str) and p]
        if cleaned:
            return list(dict.fromkeys(cleaned))

    return None

def resolve_task(value: str) -> Path:
    p = Path(value)
    p = p if p.is_absolute() else ROOT / p
    p = p.resolve()
    p.relative_to(ROOT.resolve())
    if not p.is_file():
        raise SystemExit(f"task file not found: {p}")
    return p


def activate(task_path: Path, providers: list[str] | None = None, *, create_worktree: bool = False) -> dict[str, Any]:
    task = json.loads(task_path.read_text(encoding="utf-8"))
    if task.get("request"):
        task = normalize_task(task)
    task_id = safe_task_id(task.get("id", ""))
    routed = route(task)
    out_dir = run_dir(task_id)
    write_json_atomic(out_dir / "task.json", task)
    write_json_atomic(out_dir / "route.json", routed)
    progress = init_progress(task_id, routed)
    context = build_context(task, routed)
    write_json_atomic(out_dir / "context.json", context)
    capture_impact_baseline(task_id)
    impact = build_impact_plan(task, routed, context)
    write_json_atomic(out_dir / "impact.json", impact)
    agent_budget = init_agent_budget(task, routed)

    provider_scope = _provider_scope_for_refresh(providers)
    inventory = build_inventory(provider_scope)
    save_inventory(inventory)
    selections = selections_for_task(task, "subscriptions", inventory)
    model_payload = {
        "schema_version": 2,
        "task_id": task_id,
        "provider": "subscriptions",
        "inventory_path": str(INVENTORY_PATH.relative_to(ROOT)),
        "selections": selections,
    }
    write_json_atomic(out_dir / "model-selections.json", model_payload)

    worktree_info = None
    if create_worktree and routed.get("isolation") == "worktree":
        from worktree import create
        worktree_info = create(task_id, execute=True)

    active = {
        "schema_version": 1,
        "activated_at": now_iso(),
        "task_id": task_id,
        "task_path": task_path.relative_to(ROOT).as_posix(),
        "task_snapshot_path": (out_dir / "task.json").relative_to(ROOT).as_posix(),
        "risk": routed["risk"],
        "route_path": (out_dir / "route.json").relative_to(ROOT).as_posix(),
        "context_path": (out_dir / "context.json").relative_to(ROOT).as_posix(),
        "model_selections_path": (out_dir / "model-selections.json").relative_to(ROOT).as_posix(),
        "current_agents": agent_budget.get("current_agents", []),
        "mandatory_gate_agents": agent_budget.get("mandatory_gate_agents", []),
        "progress_state": progress["state"],
        "current_step": progress["current_step"],
        "selections": selections,
        "worktree": worktree_info,
    }
    write_json_atomic(ACTIVE_PATH, active)
    return active


def refresh_models(task_id: str, providers: list[str] | None = None) -> dict[str, Any]:
    """Rebuild subscription inventory/model selections for an already activated task."""
    task_id = safe_task_id(task_id)
    out = run_dir(task_id)
    task_path = out / "task.json"
    if not task_path.is_file():
        raise SystemExit(f"task {task_id} is not activated for subscription routing")
    task = json.loads(task_path.read_text(encoding="utf-8"))
    provider_scope = _provider_scope_for_refresh(providers)
    inventory = build_inventory(provider_scope)
    save_inventory(inventory)
    selections = selections_for_task(task, "subscriptions", inventory)
    payload = {
        "schema_version": 2,
        "task_id": task_id,
        "provider": "subscriptions",
        "inventory_path": str(INVENTORY_PATH.relative_to(ROOT)),
        "selections": selections,
    }
    write_json_atomic(out / "model-selections.json", payload)
    if ACTIVE_PATH.is_file():
        try:
            active = json.loads(ACTIVE_PATH.read_text(encoding="utf-8"))
        except Exception:
            active = {}
        if active.get("task_id") == task_id:
            active["selections"] = selections
            active["model_selections_path"] = (out / "model-selections.json").relative_to(ROOT).as_posix()
            write_json_atomic(ACTIVE_PATH, active)
    return payload


def _load_run_artifacts(task_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    out = run_dir(task_id)
    required = [out / "task.json", out / "route.json", out / "context.json", out / "model-selections.json"]
    if not all(p.is_file() for p in required):
        raise SystemExit(f"task {task_id} is not activated for subscription routing")
    return tuple(json.loads(p.read_text(encoding="utf-8")) for p in required)  # type: ignore[return-value]


def _compact_context(context: dict[str, Any]) -> dict[str, Any]:
    files = []
    for row in context.get("files", [])[:20]:
        if isinstance(row, dict):
            files.append({k: row.get(k) for k in ("path", "reason", "score", "load_policy") if row.get(k) is not None})
    structured = context.get("structured_context", {}) if isinstance(context.get("structured_context"), dict) else {}
    return {
        "files": files,
        "retrieval": context.get("retrieval", {}),
        "repo_map": structured.get("repo_map", {}).get("text", "") if isinstance(structured.get("repo_map"), dict) else "",
        "codegraph_explore": str(structured.get("codegraph_explore") or "")[:18000],
        "symbol_snippets": (structured.get("symbol_snippets") or [])[:12],
        "estimated_tokens": context.get("estimated_tokens"),
    }


def _default_prompt(role: str, task: dict[str, Any], routed: dict[str, Any], context: dict[str, Any]) -> str:
    role_path = ROOT / ".agents" / "roles" / f"{role}.md"
    role_text = role_path.read_text(encoding="utf-8").strip() if role_path.is_file() else ""
    skills = compile_skill_pack(role, routed)
    payload = {
        "task": task,
        "route": {k: routed.get(k) for k in ("risk", "risk_reasons", "tdd", "requirements")},
        "context": _compact_context(context),
        "runtime_skills": {
            "loaded": skills.get("skills", []),
            "omitted_by_budget": skills.get("omitted", []),
            "estimated_tokens": skills.get("estimated_tokens", 0),
        },
    }
    sections = [role_text]
    if skills.get("text"):
        sections += [
            "Canonical harness skills selected for this role/task (authoritative; provider-native skill discovery is only supplemental):",
            str(skills["text"]),
        ]
    sections += ["Task runtime snapshot:", json.dumps(payload, ensure_ascii=False, separators=(",", ":"))]
    return "\n\n".join(x for x in sections if x).strip()


def _selection_for(role: str, models: dict[str, Any]) -> dict[str, Any]:
    for row in models.get("selections", []):
        if row.get("agent") == role:
            return row
    raise SystemExit(f"no model selection for routed role {role}")


def run_agent(task_id: str, role: str, *, prompt: str | None = None, provider_override: str | None = None,
              model_override: str | None = None, effort_override: str | None = None, cwd_override: Path | None = None,
              timeout: int = 900, allow_shell: bool = False, allow_main_worktree: bool = False,
              allow_api_credentials: bool = False) -> dict[str, Any]:
    task_id = safe_task_id(task_id)
    manifest = load_manifest()
    if role not in manifest.get("agents", {}):
        raise SystemExit(f"unknown role: {role}")
    task, routed, context, models = _load_run_artifacts(task_id)
    if role not in routed.get("agents", []):
        raise SystemExit(f"role {role} is not routed for task {task_id}")
    selection = _selection_for(role, models)
    if selection.get("action") != "use" and not (provider_override and model_override):
        raise SystemExit(f"role {role} has no usable subscription model selection: {selection.get('reason')}")

    selected = str(selection.get("base_model_id") or selection.get("model_id") or "")
    if "/" in selected:
        selected_provider, selected_model = selected.split("/", 1)
    else:
        selected_provider, selected_model = "", selected
    provider = provider_override or selected_provider
    model = model_override or selected_model
    if not provider or not model:
        raise SystemExit("runtime provider/model could not be resolved")
    effort = effort_override if effort_override is not None else selection.get("reasoning_effort")
    meta = manifest["agents"][role]
    mode = str(meta.get("mode", "read-only"))
    max_turns = int(meta.get("max_turns", 20))

    if cwd_override:
        cwd = cwd_override.resolve()
    else:
        wt = ROOT / ".worktrees" / task_id
        cwd = wt if wt.is_dir() else ROOT
    if mode == "writer" and cwd == ROOT.resolve() and not allow_main_worktree:
        raise SystemExit(
            "writer role requires its isolated .worktrees/<task> directory; run activate with --create-worktree or pass --allow-main-worktree explicitly"
        )
    actual_prompt = prompt or _default_prompt(role, task, routed, context)
    result = execute(
        provider=provider, prompt=actual_prompt, model=model, effort=effort, role=role,
        mode=mode, cwd=cwd, max_turns=max_turns, timeout=timeout, task_id=task_id,
        allow_shell=allow_shell, allow_api_credentials=allow_api_credentials,
    )
    payload = {
        "schema_version": 1,
        "created_at": now_iso(),
        "task_id": task_id,
        "role": role,
        "runtime_provider": provider,
        "model": model,
        "reasoning_effort": effort,
        "cwd": str(cwd),
        "exit_code": int(result.get("exit_code", 1)),
        "final_text": str(result.get("final_text") or ""),
        "usage": result.get("usage"),
        "integrity": result.get("integrity"),
        "removed_direct_api_env": result.get("removed_env", []),
        "stderr_tail": result.get("stderr_tail", ""),
        "runtime_capabilities": result.get("runtime_capabilities"),
        "hook_bus": result.get("hook_bus"),
        "selection": selection,
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = run_dir(task_id) / "subscription-runs" / f"{stamp}-{role}.json"
    write_json_atomic(dest, payload)
    payload["artifact_path"] = dest.relative_to(ROOT).as_posix()
    return payload


def login(provider: str) -> int:
    cfg = provider_config(provider)
    executable = find_executable(provider)
    if not executable:
        print(f"{provider}: CLI not installed", file=sys.stderr)
        return 127
    note = cfg.get("login_note")
    if note:
        print(f"{provider}: {note}")
    command = list(cfg.get("login_command") or [executable])
    command[0] = executable
    env, removed = sanitized_environment(provider)
    if removed:
        print(f"{provider}: temporarily removing direct API env vars: {', '.join(removed)}")
    return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode


def _providers_arg(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [x.strip() for x in value.split(",") if x.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description="Use official coding CLIs with subscription/OAuth logins as a multi-provider harness runtime.")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    lp = sub.add_parser("login"); lp.add_argument("provider", choices=[*load_config()["providers"].keys(), "all"])
    ip = sub.add_parser("inventory"); ip.add_argument("--providers"); ip.add_argument("--output")
    apx = sub.add_parser("activate"); apx.add_argument("task"); apx.add_argument("--providers"); apx.add_argument("--create-worktree", action="store_true")
    rp = sub.add_parser("run"); rp.add_argument("task_id"); rp.add_argument("role"); rp.add_argument("--prompt"); rp.add_argument("--prompt-file"); rp.add_argument("--provider"); rp.add_argument("--model"); rp.add_argument("--effort"); rp.add_argument("--cwd"); rp.add_argument("--timeout", type=int, default=900); rp.add_argument("--allow-shell", action="store_true"); rp.add_argument("--allow-main-worktree", action="store_true"); rp.add_argument("--allow-api-credentials", action="store_true")
    args = ap.parse_args()

    if args.command == "doctor":
        print(json.dumps(doctor(), indent=2, ensure_ascii=False)); return 0
    if args.command == "login":
        providers = list(load_config()["providers"].keys()) if args.provider == "all" else [args.provider]
        failures = 0
        for provider in providers:
            failures += int(login(provider) != 0)
        return 1 if failures else 0
    if args.command == "inventory":
        payload = build_inventory(_providers_arg(args.providers))
        dest = Path(args.output).expanduser().resolve() if args.output else INVENTORY_PATH
        save_inventory(payload, dest)
        print(json.dumps(payload, indent=2, ensure_ascii=False)); return 0
    if args.command == "activate":
        payload = activate(resolve_task(args.task), _providers_arg(args.providers), create_worktree=args.create_worktree)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 2 if any(x.get("action") == "block" for x in payload["selections"]) else 0
    if args.command == "run":
        prompt = args.prompt
        if args.prompt_file:
            prompt = Path(args.prompt_file).read_text(encoding="utf-8")
        payload = run_agent(
            args.task_id, args.role, prompt=prompt, provider_override=args.provider,
            model_override=args.model, effort_override=args.effort,
            cwd_override=Path(args.cwd).expanduser() if args.cwd else None,
            timeout=args.timeout, allow_shell=args.allow_shell,
            allow_main_worktree=args.allow_main_worktree,
            allow_api_credentials=args.allow_api_credentials,
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return int(payload["exit_code"] != 0)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
