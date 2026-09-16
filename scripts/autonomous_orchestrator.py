#!/usr/bin/env python3
"""Neutral multi-provider runner for the harness control plane.

This module drives scripts/orchestrator.py to completion. It chooses the routed
subscription model for each canonical harness role, invokes official provider
CLIs through subscription_bridge.py, validates typed handoffs, runs deterministic
control-plane stages, and stops only for explicit human gates or real blockers.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from harnesslib import load_json, load_manifest, run_dir, safe_task_id, write_json_atomic  # noqa: E402
from context_compiler import excluded as context_excluded  # noqa: E402
from handoff import validate as validate_handoff  # noqa: E402
from orchestrator import SUBAGENT_STAGES, commit as commit_handoff, load as load_progress, record, reconcile, resume  # noqa: E402
from subscription_bridge import (  # noqa: E402
    ACTIVE_PATH,
    INVENTORY_PATH,
    _default_prompt,
    _load_run_artifacts,
    activate,
    refresh_models,
    run_agent,
)
from model_router import selections_for_task  # noqa: E402
from task_checks import run_checks  # noqa: E402
from worktree import create as create_worktree, publish as publish_worktree, status as worktree_status  # noqa: E402
from impact_analysis import verify as verify_impact  # noqa: E402
from receipt_review import candidate_snapshot, grant_consent, prepare as prepare_receipt_review  # noqa: E402
from evidence import append as append_evidence  # noqa: E402
from tdd_evidence import append as append_tdd, finish_decision as tdd_finish_decision, read as read_tdd  # noqa: E402
from context_condenser import condense_handoffs, estimated_tokens as condensed_tokens  # noqa: E402

POLICY_PATH = ROOT / "harness" / "neutral-chat-policy.json"
PARALLEL_POLICY_PATH = ROOT / "harness" / "parallel-policy.json"
HANDOFF_POLICY = ROOT / "harness" / "handoff-policy.json"

_PROVIDER_SEMAPHORES: dict[tuple[str, int], threading.BoundedSemaphore] = {}
_PROVIDER_SEMAPHORES_LOCK = threading.Lock()

_PROVIDER_COOLDOWNS: dict[str, set[str]] = {}
_PROVIDER_COOLDOWNS_LOCK = threading.Lock()


def _provider_cooldowns(task_id: str) -> set[str]:
    with _PROVIDER_COOLDOWNS_LOCK:
        return set(_PROVIDER_COOLDOWNS.get(task_id, set()))


def _cooldown_provider(task_id: str, provider: str) -> None:
    with _PROVIDER_COOLDOWNS_LOCK:
        _PROVIDER_COOLDOWNS.setdefault(task_id, set()).add(provider)


def _clear_provider_cooldowns(task_id: str) -> None:
    with _PROVIDER_COOLDOWNS_LOCK:
        _PROVIDER_COOLDOWNS.pop(task_id, None)


@dataclass
class RunnerIO:
    emit: Callable[[str], None] = print
    ask_yes_no: Callable[[str, bool], bool] | None = None

    def confirm(self, prompt: str, default: bool = False) -> bool:
        if self.ask_yes_no is not None:
            return bool(self.ask_yes_no(prompt, default))
        suffix = " [Y/n] " if default else " [y/N] "
        try:
            raw = input(prompt + suffix).strip().lower()
        except EOFError:
            return default
        if not raw:
            return default
        return raw in {"y", "yes", "s", "si", "sí"}


def policy() -> dict[str, Any]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def parallel_policy() -> dict[str, Any]:
    if not PARALLEL_POLICY_PATH.is_file():
        return {"enabled": False}
    return json.loads(PARALLEL_POLICY_PATH.read_text(encoding="utf-8"))


def _provider_limit(provider: str) -> int:
    cfg = parallel_policy()
    per = cfg.get("per_provider_max_parallel", {})
    return max(1, int(per.get(provider, cfg.get("default_provider_max_parallel", 1))))


@contextmanager
def _provider_slot(provider: str):
    """Bound simultaneous official-CLI processes per subscription provider."""
    limit = _provider_limit(provider)
    key = (provider, limit)
    with _PROVIDER_SEMAPHORES_LOCK:
        sem = _PROVIDER_SEMAPHORES.get(key)
        if sem is None:
            sem = threading.BoundedSemaphore(limit)
            _PROVIDER_SEMAPHORES[key] = sem
    sem.acquire()
    try:
        yield
    finally:
        sem.release()


def _handoff_schema(role: str) -> dict[str, Any]:
    cfg = json.loads(HANDOFF_POLICY.read_text(encoding="utf-8"))
    rel = cfg.get("contracts", {}).get(role)
    if not rel:
        raise ValueError(f"no typed handoff schema for {role}")
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract one JSON object from provider text without accepting prose as data."""
    stripped = text.strip()
    candidates: list[str] = []
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)
    for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.I | re.S):
        candidates.append(match.group(1))
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[idx:])
        except Exception:
            continue
        if isinstance(value, dict):
            candidates.append(json.dumps(value, ensure_ascii=False))
    for raw in candidates:
        try:
            value = json.loads(raw)
        except Exception:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("provider did not return a JSON object")


def _previous_handoffs(task_id: str) -> dict[str, Any]:
    folder = run_dir(task_id) / "handoffs"
    out: dict[str, Any] = {}
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("*.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        out[path.stem] = row
    return condense_handoffs(out)


def _support_artifacts(task_id: str) -> list[dict[str, Any]]:
    folder = run_dir(task_id) / "support"
    if not folder.is_dir():
        return []
    rows = []
    for path in sorted(folder.glob("*.json"))[-6:]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows.append({
            "role": data.get("role"),
            "final_text": str(data.get("final_text") or "")[-8000:],
            "artifact": path.relative_to(ROOT).as_posix(),
        })
    return rows


def _required_tdd(task_id: str) -> list[str]:
    route_path = run_dir(task_id) / "route.json"
    if not route_path.is_file():
        return []
    route = json.loads(route_path.read_text(encoding="utf-8"))
    return list((route.get("tdd") or {}).get("required_evidence") or [])


def _base_stage_prompt(task_id: str, role: str, extra: str = "") -> str:
    task, routed, context, _models = _load_run_artifacts(task_id)
    schema = _handoff_schema(role)
    root_literal = str(ROOT)
    prompt = [
        _default_prompt(role, task, routed, context),
        "",
        "Authoritative prior handoffs, loss-aware condensed for working context (full artifacts remain on disk; do not request full prior transcripts unless a concrete missing fact requires it):",
        json.dumps(_previous_handoffs(task_id), ensure_ascii=False, separators=(",", ":")),
    ]
    support = _support_artifacts(task_id)
    if support:
        prompt += ["", "Recent support-agent findings:", json.dumps(support, ensure_ascii=False, separators=(",", ":"))]
    if role == "implementer":
        required = _required_tdd(task_id)
        prompt += [
            "",
            "Single-writer/TDD execution contract:",
            "- Work only in the assigned task worktree.",
            "- You may run local tests/checks; never push, deploy, publish, or touch external systems.",
            f"- Required TDD evidence phases for this task: {json.dumps(required)}",
            f"- Canonical evidence command lives at: {root_literal}/scripts/tdd_evidence.py",
            "- For red/characterization/green/mutation phases, execute the real command first and then record the actual command and exit code with that canonical script.",
            "- Never invent a red/green result. If evidence cannot be produced truthfully, return BLOCKED.",
        ]
    if extra:
        prompt += ["", "Additional control-plane instruction:", extra]
    prompt += [
        "",
        "FINAL HANDOFF CONTRACT:",
        "Return ONLY one JSON object. No markdown fence, no commentary before or after it.",
        "The JSON MUST validate against this exact schema:",
        json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
        f'It MUST contain task_id="{task_id}" and producer="{role}".',
    ]
    return "\n".join(prompt)


def _runtime_selection(task_id: str, role: str) -> dict[str, Any]:
    models = json.loads((run_dir(task_id) / "model-selections.json").read_text(encoding="utf-8"))
    for row in models.get("selections", []):
        if row.get("agent") == role:
            return row
    raise ValueError(f"no model selection for {role}")


def _filtered_alternative(task_id: str, role: str, excluded_providers: set[str]) -> dict[str, Any] | None:
    task = json.loads((run_dir(task_id) / "task.json").read_text(encoding="utf-8"))
    inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    clone = dict(inventory)
    clone["models"] = [
        row for row in inventory.get("models", [])
        if str(row.get("id") or "").split("/", 1)[0] not in excluded_providers
    ]
    if not clone["models"]:
        return None
    selection = selections_for_task(task, "subscriptions", clone, role)[0]
    return selection if selection.get("action") == "use" else None


def _auth_or_quota_text(text: str) -> bool:
    low = text.lower()
    terms = (
        "not logged in", "login required", "please login", "please log in", "authentication required",
        "unauthorized", "forbidden", "rate limit", "rate-limit", "quota", "usage limit", "limit reached",
        "subscription required", "upgrade your plan", "too many requests",
    )
    return any(term in low for term in terms)


def run_role_with_failover(task_id: str, role: str, prompt: str, *, io: RunnerIO, allow_shell: bool = False) -> dict[str, Any]:
    cfg = policy()
    max_failovers = int(cfg.get("max_runtime_failovers", 2))
    selected = _runtime_selection(task_id, role)
    attempted_providers: set[str] = set()
    cooldowns = _provider_cooldowns(task_id)

    selection = selected

    selected_model_id = str(
        selection.get("base_model_id")
        or selection.get("model_id")
        or ""
    )
    selected_provider = (
        selected_model_id.split("/", 1)[0]
        if "/" in selected_model_id
        else ""
    )

    # A provider that already produced an auth/quota/rate-limit failure for
    # this task should not be retried by later roles/protocol retries.
    if selected_provider in cooldowns:
        alternative = _filtered_alternative(
            task_id,
            role,
            cooldowns,
        )
        if alternative:
            selection = alternative
        else:
            return {
                "exit_code": 75,
                "final_text": "",
                "stderr_tail": (
                    f"all eligible runtimes unavailable; "
                    f"{selected_provider} is cooled down for this task"
                ),
            }

    last: dict[str, Any] | None = None

    for attempt in range(max_failovers + 1):
        model_id = str(selection.get("base_model_id") or selection.get("model_id") or "")
        provider, model = model_id.split("/", 1) if "/" in model_id else ("", model_id)
        if not provider:
            raise ValueError(f"selection for {role} has no runtime provider: {model_id}")
        attempted_providers.add(provider)
        io.emit(f"  ↳ {role}: {provider}/{model} ({selection.get('reasoning_effort') or 'default'} effort)")
        with _provider_slot(provider):
            result = run_agent(
                task_id,
                role,
                prompt=prompt,
                provider_override=provider,
                model_override=model,
                effort_override=selection.get("reasoning_effort"),
                allow_shell=allow_shell,
            )
        last = result
        combined = (
            str(result.get("final_text") or "")
            + "\n"
            + str(result.get("stderr_tail") or "")
        )

        limited = _auth_or_quota_text(combined)

        if int(result.get("exit_code", 1)) == 0 and not limited:
            return result

        if limited:
            _cooldown_provider(task_id, provider)
        if int(result.get("exit_code", 1)) == 73:
            # Read-only integrity violations are agent failures, not provider availability failures.
            return result
        if attempt >= max_failovers:
            break
        excluded = attempted_providers | _provider_cooldowns(task_id)

        alternative = _filtered_alternative(
            task_id,
            role,
            excluded,
        )
        if not alternative:
            break
        if provider in _provider_cooldowns(task_id):
            io.emit(
                f"    runtime auth/quota limited; cooling down {provider} "
                f"for this task and failing over"
            )
        else:
            io.emit(
                f"    runtime unavailable; failing over from {provider}"
            )
        selection = alternative
    return last or {"exit_code": 1, "final_text": "no runtime attempt", "stderr_tail": ""}


def _save_protocol_handoff(task_id: str, role: str, step: str, payload: dict[str, Any], attempt: int) -> Path:
    folder = run_dir(task_id) / "autonomous"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{step.lower()}-{role}-attempt-{attempt}.json"
    write_json_atomic(path, payload)
    return path


def _ensure_contract_phase(task_id: str, handoff_path: Path) -> None:
    required = set(_required_tdd(task_id))
    if "contract" not in required:
        return
    latest = {row.get("phase"): row for row in read_tdd(task_id)}
    if latest.get("contract", {}).get("status") == "PASS":
        return
    append_tdd(
        task_id,
        "contract",
        "PASS",
        "test-designer",
        "Validated test-design handoff records the contract/oracle used for spike-then-TDD.",
        artifact=handoff_path.relative_to(ROOT).as_posix(),
    )


def prepare_typed_stage(task_id: str, step: str, role: str, *, io: RunnerIO, extra: str = "") -> dict[str, Any]:
    """Execute and validate a typed worker without advancing control-plane state.

    This separation lets independent read-only gates run concurrently against
    the same frozen candidate.  Their outputs are merely pending artifacts until
    the canonical orchestrator commits them in workflow order.
    """
    cfg = policy()
    retries = int(cfg.get("protocol_retries", 1))
    last_error = ""
    for attempt in range(1, retries + 2):
        repair = extra
        if last_error:
            repair = (repair + "\n" if repair else "") + f"Your previous final response failed the typed handoff protocol: {last_error}. Correct it."
        prompt = _base_stage_prompt(task_id, role, repair)
        result = run_role_with_failover(task_id, role, prompt, io=io, allow_shell=(role == "implementer"))
        if int(result.get("exit_code", 1)) != 0:
            last_error = f"runtime exit_code={result.get('exit_code')}: {result.get('stderr_tail') or result.get('final_text')}"
            continue
        try:
            data = extract_json_object(str(result.get("final_text") or ""))
            validate_handoff(role, data)
            if data.get("task_id") != task_id:
                raise ValueError(f"task_id must be {task_id}")
            path = _save_protocol_handoff(task_id, role, step, data, attempt)
            if role == "implementer":
                tdd = tdd_finish_decision(task_id)
                if data.get("status") == "PASS" and not tdd.get("allow"):
                    raise ValueError("required TDD evidence missing/failing: " + json.dumps(tdd, ensure_ascii=False))
            return {"handoff_path": path, "handoff": data, "runtime": result}
        except Exception as exc:
            last_error = str(exc)
    raise RuntimeError(f"{step}/{role} could not produce a valid pending handoff: {last_error}")


def commit_prepared_typed_stage(task_id: str, step: str, role: str, prepared: dict[str, Any]) -> dict[str, Any]:
    result = prepared["runtime"]
    path = Path(prepared["handoff_path"])
    data = prepared["handoff"]
    state = commit_handoff(task_id, role, path, note=f"neutral orchestrator via {result.get('runtime_provider')}/{result.get('model')}")
    if step == "TEST_DESIGN" and data.get("status") == "PASS":
        _ensure_contract_phase(task_id, run_dir(task_id) / "handoffs" / "test-designer.json")
    return {"state": state, "handoff": data, "runtime": result}


def run_typed_stage(task_id: str, step: str, role: str, *, io: RunnerIO, extra: str = "") -> dict[str, Any]:
    prepared = prepare_typed_stage(task_id, step, role, io=io, extra=extra)
    return commit_prepared_typed_stage(task_id, step, role, prepared)


def _parallel_review_steps(state: dict[str, Any]) -> list[str]:
    cfg = parallel_policy()
    if not cfg.get("enabled", True):
        return []
    current = str(state.get("current_step") or "")
    configured = [str(x) for x in cfg.get("review_gate_stages", ["REVIEW", "TEST_AUDIT", "VERIFY"])]
    if current not in configured:
        return []
    steps = list(state.get("steps") or [])
    try:
        idx = steps.index(current)
    except ValueError:
        return []
    group: list[str] = []
    for step in steps[idx:]:
        if step not in configured:
            break
        if step not in SUBAGENT_STAGES:
            break
        group.append(step)
    minimum = max(2, int(cfg.get("minimum_batch_size", 2)))
    return group if len(group) >= minimum else []


def _write_parallel_batch(task_id: str, payload: dict[str, Any]) -> Path:
    folder = run_dir(task_id) / "parallel-batches"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = re.sub(r"[^0-9]", "", str(payload.get("started_at") or ""))[:14] or "batch"
    path = folder / f"{stamp}-{payload.get('candidate_subject_hash', 'unknown')[:12]}.json"
    write_json_atomic(path, payload)
    return path


def run_parallel_review_batch(task_id: str, state: dict[str, Any], *, io: RunnerIO) -> dict[str, Any]:
    """Fan out independent review gates, then fan in/commit in canonical order."""
    import datetime as _dt

    steps = _parallel_review_steps(state)
    if not steps:
        raise ValueError("no eligible parallel review batch at current step")
    cfg = parallel_policy()
    max_workers = max(1, min(int(cfg.get("max_parallel_agents", 4)), len(steps)))
    before = candidate_snapshot(task_id)
    subject_hash = str(before.get("subject_hash") or "")
    io.emit(f"[PARALLEL {' + '.join(steps)}] candidate={subject_hash[:12]} workers={max_workers}")

    prepared: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=f"harness-{task_id}") as pool:
        futures = {}
        for step in steps:
            role = SUBAGENT_STAGES[step][0]
            extra = (
                f"Parallel independent gate. Review candidate subject_hash={subject_hash}. "
                "Do not rely on sibling gate outputs; evaluate independently against the task, candidate, tests, and acceptance criteria."
            )
            futures[pool.submit(prepare_typed_stage, task_id, step, role, io=io, extra=extra)] = (step, role)
        for future in as_completed(futures):
            step, _role = futures[future]
            try:
                prepared[step] = future.result()
            except Exception as exc:
                errors[step] = str(exc)

    after = candidate_snapshot(task_id)
    candidate_changed = str(after.get("subject_hash") or "") != subject_hash
    started_at = _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")
    manifest = {
        "schema_version": 1,
        "task_id": task_id,
        "started_at": started_at,
        "candidate_subject_hash": subject_hash,
        "candidate_unchanged": not candidate_changed,
        "steps": steps,
        "max_parallel_agents": max_workers,
        "results": {
            step: {
                "status": (prepared.get(step, {}).get("handoff") or {}).get("status"),
                "provider": (prepared.get(step, {}).get("runtime") or {}).get("runtime_provider"),
                "model": (prepared.get(step, {}).get("runtime") or {}).get("model"),
                "error": errors.get(step),
            } for step in steps
        },
    }
    batch_path = _write_parallel_batch(task_id, manifest)

    if candidate_changed:
        first = steps[0]
        state2 = record(task_id, "FAIL", step=first, note=f"parallel gate candidate changed during read-only fan-out; batch={batch_path.relative_to(ROOT).as_posix()}")
        return {"ok": False, "failed_step": first, "status": "FAIL", "state": state2, "reason": "candidate changed during parallel gate batch", "batch": manifest}

    # Commit in canonical workflow order. Prepared later-stage results remain
    # non-authoritative if an earlier gate fails; a repaired candidate must be
    # reviewed again rather than reusing stale parallel output.
    for step in steps:
        role = SUBAGENT_STAGES[step][0]
        if step in errors:
            state2 = record(task_id, "FAIL", step=step, note=f"parallel worker failed: {errors[step]}; batch={batch_path.relative_to(ROOT).as_posix()}")
            return {"ok": False, "failed_step": step, "status": "FAIL", "state": state2, "reason": errors[step], "batch": manifest}
        outcome = commit_prepared_typed_stage(task_id, step, role, prepared[step])
        status = str(outcome["handoff"].get("status"))
        if status != "PASS":
            return {"ok": False, "failed_step": step, "status": status, "state": outcome["state"], "reason": f"{step} returned {status}", "batch": manifest}
    return {"ok": True, "steps": steps, "state": load_progress(task_id), "batch": manifest}


def _task_source_path(task_id: str) -> Path | None:
    if not ACTIVE_PATH.is_file():
        return None
    try:
        active = json.loads(ACTIVE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    if active.get("task_id") != task_id:
        return None
    raw = active.get("task_path")
    if not isinstance(raw, str) or not raw:
        return None
    p = (ROOT / raw).resolve()
    try:
        p.relative_to(ROOT.resolve())
    except ValueError:
        return None
    return p


def _update_task(task_id: str, mutator: Callable[[dict[str, Any]], bool], *, reactivate: bool) -> bool:
    snapshot = run_dir(task_id) / "task.json"
    task = json.loads(snapshot.read_text(encoding="utf-8"))
    changed = bool(mutator(task))
    if not changed:
        return False
    write_json_atomic(snapshot, task)
    source = _task_source_path(task_id)
    if source:
        write_json_atomic(source, task)
    if reactivate and source:
        activate(source, create_worktree=False)
    elif reactivate:
        # In normal neutral-chat operation there is always a source path, but
        # preserve deterministic model refresh for imported/legacy active tasks.
        refresh_models(task_id)
    return True


def localize_from_explorer(
    task_id: str,
    handoff: dict[str, Any],
    *,
    io: RunnerIO,
) -> None:
    files = [
        str(x)
        for x in handoff.get("relevant_files", [])
        if isinstance(x, str)
    ]

    localized = []
    root = ROOT.resolve()
    context_policy = load_json("harness/context-policy.json")

    for rel in files:
        candidate = Path(rel)

        if candidate.is_absolute():
            continue

        try:
            resolved = (ROOT / candidate).resolve()
            resolved.relative_to(root)
        except Exception:
            continue

        normalized = Path(candidate.as_posix())

        if context_excluded(normalized, context_policy):
            continue

        if resolved.exists() and not resolved.is_file():
            continue

        localized.append(normalized.as_posix())

    if not localized:
        return

    def mutate(task: dict[str, Any]) -> bool:
        current = list(task.get("files") or [])
        merged = list(
            dict.fromkeys(current + localized)
        )[: int(policy().get("max_localized_files", 24))]

        if merged == current:
            return False

        task["files"] = merged
        return True

    if _update_task(task_id, mutate, reactivate=True):
        io.emit(
            f"    localized task surface: {len(localized)} file(s); "
            "route/context/models refreshed"
        )

def acceptance_from_planner(task_id: str, handoff: dict[str, Any]) -> None:
    criteria = [str(x) for x in handoff.get("acceptance_criteria", []) if str(x).strip()]
    if not criteria:
        return

    def mutate(task: dict[str, Any]) -> bool:
        old = list(task.get("acceptance_criteria") or [])
        if old == criteria:
            return False
        task["acceptance_criteria"] = criteria
        return True

    _update_task(task_id, mutate, reactivate=False)


def run_support_agent(task_id: str, role: str, instruction: str, *, io: RunnerIO) -> dict[str, Any] | None:
    task, route, context, _ = _load_run_artifacts(task_id)
    if role not in route.get("agents", []):
        return None
    prompt = _default_prompt(role, task, route, context) + "\n\nSupport instruction:\n" + instruction + "\nReturn a concise evidence-backed diagnosis/findings summary; do not edit files."
    result = run_role_with_failover(task_id, role, prompt, io=io, allow_shell=False)
    folder = run_dir(task_id) / "support"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{role}-{len(list(folder.glob(role + '-*.json'))) + 1}.json"
    write_json_atomic(path, result)
    return result


def _repair_after_failure(task_id: str, failed_step: str, details: str, *, io: RunnerIO, repairs: int) -> bool:
    cfg = policy()
    if repairs >= int(cfg.get("max_repair_cycles", 2)):
        return False
    route = json.loads((run_dir(task_id) / "route.json").read_text(encoding="utf-8"))
    if "debugger" in route.get("agents", []):
        run_support_agent(task_id, "debugger", f"Diagnose failure at {failed_step}. Evidence/details:\n{details[-12000:]}", io=io)
    elif "planner" in route.get("agents", []):
        run_support_agent(task_id, "planner", f"Reassess implementation after failure at {failed_step}. Do not rewrite the whole plan; identify the minimal corrective action.\n{details[-12000:]}", io=io)
    resume(task_id, step="IMPLEMENT", note=f"automatic repair after {failed_step}")
    return True


def _verify_assessment(task_id: str, *, io: RunnerIO) -> None:
    prepare_receipt_review(task_id, apply=True)
    reconcile(task_id)
    # Dynamic receipt routing can add reviewer/verifier. The subscription bridge
    # uses one normalized inventory, so rebuild selections after route mutation.
    refresh_models(task_id)
    record(task_id, "PASS", step="VERIFY_ASSESS", note="neutral orchestrator prepared verification/receipt plan")


def _review_consent(task_id: str, *, io: RunnerIO) -> bool:
    if not io.confirm("La política Receipt-RDD requiere tu consentimiento para que un reviewer emita un receipt en esta sesión. ¿Aprobar?", False):
        record(task_id, "BLOCKED", step="REVIEW_CONSENT", note="human declined receipt review consent")
        return False
    session = ROOT / ".harness" / "subscriptions" / "session.json"
    sid = None
    if session.is_file():
        try:
            sid = json.loads(session.read_text(encoding="utf-8")).get("session_id")
        except Exception:
            sid = None
    grant_consent(task_id, provider="subscriptions", session_id=sid)
    record(task_id, "PASS", step="REVIEW_CONSENT", note="explicit human session consent")
    return True


def _impact_stage(task_id: str, *, io: RunnerIO) -> bool:
    result = verify_impact(task_id)
    if result.get("status") == "PASS":
        record(task_id, "PASS", step="IMPACT_VERIFY", note="task-relative impact verification passed")
        return True
    unexpected = result.get("unexpected_changed_files") or []
    if not unexpected:
        record(task_id, "FAIL", step="IMPACT_VERIFY", note=json.dumps(result, ensure_ascii=False))
        return False
    # Ask the routed verifier to explicitly assess the expansion before allowing
    # impact_analysis's reviewed-expansion escape hatch.
    route = json.loads((run_dir(task_id) / "route.json").read_text(encoding="utf-8"))
    if "verifier" not in route.get("agents", []):
        record(task_id, "FAIL", step="IMPACT_VERIFY", note="unexpected changed files without routed verifier")
        return False
    prompt = _base_stage_prompt(
        task_id,
        "verifier",
        "Impact expansion review only. Explicitly inspect these unexpected changed files and return PASS only if they are necessary, safe, and consistent with acceptance criteria: " + json.dumps(unexpected),
    )
    result2 = run_role_with_failover(task_id, "verifier", prompt, io=io)
    try:
        data = extract_json_object(str(result2.get("final_text") or ""))
        validate_handoff("verifier", data)
    except Exception as exc:
        record(task_id, "FAIL", step="IMPACT_VERIFY", note=f"expansion verifier protocol failed: {exc}")
        return False
    if data.get("status") != "PASS":
        record(task_id, "FAIL", step="IMPACT_VERIFY", note="verifier rejected unexpected changed surface")
        return False
    reviewed = verify_impact(
        task_id,
        reviewed_expansion=True,
        actor="verifier",
        reason="Neutral orchestrator obtained an explicit PASS verifier handoff for the unexpected changed surface.",
    )
    if reviewed.get("status") != "PASS":
        record(task_id, "FAIL", step="IMPACT_VERIFY", note=json.dumps(reviewed, ensure_ascii=False))
        return False
    record(task_id, "PASS", step="IMPACT_VERIFY", note="unexpected impact surface independently reviewed by verifier")
    return True


def _human_gate(task_id: str, *, io: RunnerIO) -> bool:
    route = json.loads((run_dir(task_id) / "route.json").read_text(encoding="utf-8"))
    summary = {
        "risk": route.get("risk"),
        "files": json.loads((run_dir(task_id) / "task.json").read_text(encoding="utf-8")).get("files", []),
        "current_step": "HUMAN_GATE",
    }
    io.emit("Human gate requerido: " + json.dumps(summary, ensure_ascii=False))
    if not io.confirm("¿Aprobar explícitamente este cambio para continuar al cierre/publicación local?", False):
        record(task_id, "BLOCKED", step="HUMAN_GATE", note="human declined R3 gate")
        return False
    append_evidence(
        task_id,
        "human_approval",
        "DETERMINISTIC",
        "Human explicitly approved the task at the neutral orchestrator gate.",
        "PASS",
        "human",
        notes="interactive terminal approval",
    )
    record(task_id, "PASS", step="HUMAN_GATE", note="explicit human approval")
    return True


def run_to_completion(task_path: Path, *, io: RunnerIO | None = None, providers: list[str] | None = None) -> dict[str, Any]:
    io = io or RunnerIO()
    task_path = task_path.resolve()
    task_path.relative_to(ROOT.resolve())
    active = activate(task_path, providers, create_worktree=False)
    task_id = safe_task_id(active["task_id"])
    repair_cycles = 0
    io.emit(f"Task {task_id}: risk {active['risk']}")

    # Optional support research is delegated once when routing explicitly asks
    # for it. It is not a workflow gate and its output is fed into later prompts.
    route = json.loads((run_dir(task_id) / "route.json").read_text(encoding="utf-8"))
    if "docs-researcher" in route.get("agents", []):
        run_support_agent(task_id, "docs-researcher", "Resolve external contract/version uncertainty needed for this task. Prefer authoritative sources exposed by the runtime; if unavailable, state the limitation.", io=io)

    while True:
        state = load_progress(task_id)
        if state.get("state") == "DONE":
            return {"task_id": task_id, "status": "DONE", "progress": state}
        step = str(state.get("current_step"))
        io.emit(f"[{step}]")

        parallel_steps = _parallel_review_steps(state)
        if parallel_steps:
            batch = run_parallel_review_batch(task_id, state, io=io)
            if batch.get("ok"):
                continue
            failed_step = str(batch.get("failed_step") or step)
            failed_status = str(batch.get("status") or "FAIL")
            if failed_status in {"BLOCKED", "INSUFFICIENT"}:
                return {"task_id": task_id, "status": load_progress(task_id).get("state"), "progress": load_progress(task_id), "reason": str(batch.get("reason") or failed_status)}
            if not _repair_after_failure(task_id, failed_step, str(batch.get("reason") or "parallel gate failed"), io=io, repairs=repair_cycles):
                return {"task_id": task_id, "status": load_progress(task_id).get("state"), "progress": load_progress(task_id), "reason": f"repair budget exhausted after {failed_step}"}
            repair_cycles += 1
            continue

        if step in SUBAGENT_STAGES:
            role = SUBAGENT_STAGES[step][0]
            try:
                outcome = run_typed_stage(task_id, step, role, io=io)
            except Exception as exc:
                # Runtime/protocol failures are real failures. Record once and use
                # the same repair policy as semantic FAIL handoffs when possible.
                try:
                    state2 = record(task_id, "FAIL", step=step, note=str(exc))
                except Exception:
                    raise
                if step == "SECURITY_REVIEW" or not _repair_after_failure(task_id, step, str(exc), io=io, repairs=repair_cycles):
                    return {"task_id": task_id, "status": state2.get("state"), "progress": state2, "reason": str(exc)}
                repair_cycles += 1
                continue

            handoff = outcome["handoff"]
            status = handoff.get("status")
            if step == "EXPLORE" and status == "PASS":
                localize_from_explorer(task_id, handoff, io=io)
            if step == "PLAN" and status == "PASS":
                acceptance_from_planner(task_id, handoff)
            if status == "PASS":
                continue
            if step == "SECURITY_REVIEW" or status in {"BLOCKED", "INSUFFICIENT"}:
                return {"task_id": task_id, "status": load_progress(task_id).get("state"), "progress": load_progress(task_id), "reason": f"{step} returned {status}"}
            details = json.dumps(handoff, ensure_ascii=False)
            if not _repair_after_failure(task_id, step, details, io=io, repairs=repair_cycles):
                return {"task_id": task_id, "status": load_progress(task_id).get("state"), "progress": load_progress(task_id), "reason": f"repair budget exhausted after {step}"}
            repair_cycles += 1
            continue

        if step == "WORKTREE":
            wt = worktree_status(task_id)

            if not wt.get("exists") or not wt.get("lock"):
                try:
                    create_worktree(task_id, execute=True)
                except Exception as exc:
                    state2 = record(
                        task_id,
                        "BLOCKED",
                        step="WORKTREE",
                        note=f"failed to create isolated writer worktree: {exc}",
                    )
                    return {
                        "task_id": task_id,
                        "status": state2.get("state"),
                        "progress": state2,
                        "reason": str(exc),
                    }

                wt = worktree_status(task_id)

            if not wt.get("exists") or not wt.get("lock"):
                reason = (
                    "isolated writer worktree validation failed after creation: "
                    f"exists={wt.get('exists')} lock={wt.get('lock')}"
                )
                state2 = record(
                    task_id,
                    "BLOCKED",
                    step="WORKTREE",
                    note=reason,
                )
                return {
                    "task_id": task_id,
                    "status": state2.get("state"),
                    "progress": state2,
                    "reason": reason,
                }

            record(
                task_id,
                "PASS",
                step="WORKTREE",
                note="neutral orchestrator verified isolated writer worktree",
            )
            continue

        if step == "CHECKS":
            report = run_checks(task_id)
            if report.get("status") == "PASS":
                record(task_id, "PASS", step="CHECKS", note="authoritative deterministic checks passed")
                continue
            state2 = record(task_id, "FAIL", step="CHECKS", note=json.dumps(report, ensure_ascii=False)[-12000:])
            if not _repair_after_failure(task_id, "CHECKS", json.dumps(report, ensure_ascii=False), io=io, repairs=repair_cycles):
                return {"task_id": task_id, "status": state2.get("state"), "progress": state2, "reason": "checks failed and repair budget exhausted"}
            repair_cycles += 1
            continue

        if step == "VERIFY_ASSESS":
            try:
                _verify_assessment(task_id, io=io)
            except Exception as exc:
                state2 = record(task_id, "FAIL", step="VERIFY_ASSESS", note=str(exc))
                return {"task_id": task_id, "status": state2.get("state"), "progress": state2, "reason": str(exc)}
            continue

        if step == "REVIEW_CONSENT":
            if not _review_consent(task_id, io=io):
                return {"task_id": task_id, "status": "BLOCKED", "progress": load_progress(task_id), "reason": "review consent declined"}
            continue

        if step == "IMPACT_VERIFY":
            if not _impact_stage(task_id, io=io):
                state2 = load_progress(task_id)
                if _repair_after_failure(task_id, "IMPACT_VERIFY", "impact verification failed", io=io, repairs=repair_cycles):
                    repair_cycles += 1
                    continue
                return {"task_id": task_id, "status": state2.get("state"), "progress": state2, "reason": "impact verification failed"}
            continue

        if step == "HUMAN_GATE":
            if not _human_gate(task_id, io=io):
                return {"task_id": task_id, "status": "BLOCKED", "progress": load_progress(task_id), "reason": "human gate declined"}
            continue

        if step == "CLOSE":
            route = json.loads((run_dir(task_id) / "route.json").read_text(encoding="utf-8"))
            if route.get("isolation") == "worktree":
                try:
                    publish_worktree(task_id, execute=True)
                except Exception as exc:
                    state2 = record(task_id, "BLOCKED", step="CLOSE", note=f"local publication blocked: {exc}")
                    return {"task_id": task_id, "status": state2.get("state"), "progress": state2, "reason": str(exc)}
            try:
                state2 = record(task_id, "PASS", step="CLOSE", note="finish gate passed; task closed")
            except Exception as exc:
                state2 = record(task_id, "BLOCKED", step="CLOSE", note=f"finish gate blocked: {exc}")
                return {"task_id": task_id, "status": state2.get("state"), "progress": state2, "reason": str(exc)}
            return {"task_id": task_id, "status": "DONE", "progress": state2}

        return {"task_id": task_id, "status": "BLOCKED", "progress": state, "reason": f"unsupported control-plane step: {step}"}


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Drive one task through the canonical harness workflow using subscription runtimes.")
    ap.add_argument("task")
    args = ap.parse_args()
    p = Path(args.task)
    p = p if p.is_absolute() else ROOT / p
    result = run_to_completion(p)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("status") == "DONE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
