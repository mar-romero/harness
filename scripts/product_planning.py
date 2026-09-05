#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "harness" / "product-discovery-policy.json"
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RISK_KEYS = (
    "touches_auth", "touches_production", "uses_secrets", "destructive_operation",
    "irreversible_change", "security_boundary", "schema_change", "database_change",
    "concurrency", "financial", "external_contract", "migration", "persistence",
    "important_calculation", "bug_fix",
)
DIR_FOR_TYPE = {"PROJECT": "projects", "EPIC": "epics", "FEATURE": "features", "SPIKE": "spikes"}


class PlanningError(ValueError):
    pass


def load_policy(root: Path = ROOT) -> dict[str, Any]:
    return json.loads((root / "harness" / "product-discovery-policy.json").read_text(encoding="utf-8"))


def safe_id(value: Any, field: str = "id") -> str:
    text = str(value or "")
    if not ID_RE.fullmatch(text):
        raise PlanningError(f"invalid {field}: {text!r}")
    return text


def _list_of_strings(value: Any, field: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
        raise PlanningError(f"{field} must be a list of non-empty strings")
    if nonempty and not value:
        raise PlanningError(f"{field} must not be empty")
    return value


def _score_1_5(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PlanningError(f"{field} must be numeric") from exc
    if not 1 <= number <= 5:
        raise PlanningError(f"{field} must be between 1 and 5")
    return number


def sprint_priority(task: dict[str, Any], policy: dict[str, Any] | None = None) -> float:
    policy = policy or load_policy()
    weights = policy["sprint"]["priority_weights"]
    planning = task.get("planning") or task
    total = 0.0
    for key, weight in weights.items():
        total += float(weight) * _score_1_5(planning.get(key, 3), f"planning.{key}")
    return round(total, 4)


def validate_bundle(bundle: dict[str, Any], policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or load_policy()
    if bundle.get("schema_version") != 1:
        raise PlanningError("schema_version must be 1")
    discovery_id = safe_id(bundle.get("discovery_id"), "discovery_id")
    status = bundle.get("status")
    if status not in {"draft", "ready_for_approval", "approved", "archived"}:
        raise PlanningError(f"invalid status: {status!r}")
    if bundle.get("classification") not in {"SPIKE", "TASK", "FEATURE", "EPIC", "PROJECT"}:
        raise PlanningError("classification must be SPIKE/TASK/FEATURE/EPIC/PROJECT")
    for field in ("title", "refined_problem"):
        if not isinstance(bundle.get(field), str) or not bundle[field].strip():
            raise PlanningError(f"{field} must be a non-empty string")
    original = bundle.get("original_request")
    if not isinstance(original, dict) or not str(original.get("text", "")).strip() or not str(original.get("language", "")).strip():
        raise PlanningError("original_request must contain text and language")
    _list_of_strings(bundle.get("target_users"), "target_users")
    _list_of_strings(bundle.get("outcomes"), "outcomes")
    scope = bundle.get("scope")
    if not isinstance(scope, dict):
        raise PlanningError("scope must be an object")
    _list_of_strings(scope.get("in"), "scope.in")
    _list_of_strings(scope.get("out"), "scope.out")
    mvp = bundle.get("mvp")
    if not isinstance(mvp, dict) or not str(mvp.get("goal", "")).strip():
        raise PlanningError("mvp.goal is required")
    _list_of_strings(mvp.get("includes"), "mvp.includes")
    _list_of_strings(mvp.get("excludes"), "mvp.excludes")
    for field in ("success_metrics", "assumptions", "open_questions", "challenges", "work_items", "tasks"):
        if not isinstance(bundle.get(field), list):
            raise PlanningError(f"{field} must be a list")

    blocking = []
    for idx, question in enumerate(bundle["open_questions"]):
        if not isinstance(question, dict) or not isinstance(question.get("question"), str) or not isinstance(question.get("blocking"), bool):
            raise PlanningError(f"open_questions[{idx}] must contain question and boolean blocking")
        if question["blocking"]:
            blocking.append(question["question"])
    if status == "approved" and blocking and policy["discovery"].get("approved_requires_no_blocking_questions", True):
        raise PlanningError("approved discovery still has blocking questions: " + "; ".join(blocking))

    seen: set[str] = set()
    for idx, item in enumerate(bundle["work_items"]):
        if not isinstance(item, dict):
            raise PlanningError(f"work_items[{idx}] must be an object")
        item_id = safe_id(item.get("id"), f"work_items[{idx}].id")
        if item_id in seen:
            raise PlanningError(f"duplicate work item/task id: {item_id}")
        seen.add(item_id)
        if item.get("type") not in DIR_FOR_TYPE:
            raise PlanningError(f"invalid work item type for {item_id}")
        if not str(item.get("title", "")).strip() or not str(item.get("description", "")).strip():
            raise PlanningError(f"work item {item_id} requires title and description")

    sizes = policy["sizes"]
    for idx, task in enumerate(bundle["tasks"]):
        if not isinstance(task, dict):
            raise PlanningError(f"tasks[{idx}] must be an object")
        task_id = safe_id(task.get("id"), f"tasks[{idx}].id")
        if task_id in seen:
            raise PlanningError(f"duplicate work item/task id: {task_id}")
        seen.add(task_id)
        if not str(task.get("title", "")).strip() or not str(task.get("description", "")).strip():
            raise PlanningError(f"task {task_id} requires title and canonical-English description")
        _list_of_strings(task.get("acceptance_criteria"), f"task {task_id}.acceptance_criteria", nonempty=True)
        _list_of_strings(task.get("dependencies", []), f"task {task_id}.dependencies")
        size = task.get("size", "M")
        if size not in sizes:
            raise PlanningError(f"task {task_id} has invalid size {size!r}")
        if task.get("risk_factors") is not None and not isinstance(task.get("risk_factors"), dict):
            raise PlanningError(f"task {task_id}.risk_factors must be an object")
        for score in ("value", "dependency_unlock", "risk_reduction", "urgency", "confidence"):
            if score in task:
                _score_1_5(task[score], f"task {task_id}.{score}")

    sprint = bundle.get("proposed_sprint")
    if sprint is not None:
        if not isinstance(sprint, dict):
            raise PlanningError("proposed_sprint must be an object")
        safe_id(sprint.get("id"), "proposed_sprint.id")
        if not str(sprint.get("goal", "")).strip():
            raise PlanningError("proposed_sprint.goal is required")
        capacity = int(sprint.get("capacity_units", policy["sprint"]["default_capacity_units"]))
        if capacity < 1:
            raise PlanningError("proposed_sprint.capacity_units must be positive")
        task_ids = _list_of_strings(sprint.get("task_ids", []), "proposed_sprint.task_ids")
        known_tasks = {t["id"]: t for t in bundle["tasks"]}
        missing = [task_id for task_id in task_ids if task_id not in known_tasks]
        if missing:
            raise PlanningError("proposed_sprint references unknown tasks: " + ", ".join(missing))
        used = sum(int(sizes[known_tasks[t].get("size", "M")]) for t in task_ids)
        if used > capacity and not sprint.get("allow_over_capacity", False):
            raise PlanningError(f"proposed_sprint uses {used} units but capacity is {capacity}")
        xl_count = sum(1 for t in task_ids if known_tasks[t].get("size", "M") == "XL")
        if xl_count > int(policy["sprint"].get("max_xl_default", 1)) and not sprint.get("allow_multiple_xl", False):
            raise PlanningError("proposed_sprint contains too many XL tasks for default policy")

    return {
        "ok": True,
        "discovery_id": discovery_id,
        "status": status,
        "work_items": len(bundle["work_items"]),
        "tasks": len(bundle["tasks"]),
        "blocking_questions": len(blocking),
    }


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _risk_factors(raw: dict[str, Any] | None) -> dict[str, bool]:
    raw = raw or {}
    return {key: bool(raw.get(key, False)) for key in RISK_KEYS}


def _task_payload(task: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    project_ids = [x["id"] for x in bundle["work_items"] if x.get("type") == "PROJECT"]
    return {
        "id": task["id"],
        "description": task["description"],
        "acceptance_criteria": list(task["acceptance_criteria"]),
        "risk_factors": _risk_factors(task.get("risk_factors")),
        "files": list(task.get("files", [])),
        "tags": list(dict.fromkeys([*task.get("tags", []), "derived-work", "product-discovery"])),
        "origin": {
            "kind": "derived_from_approved_discovery",
            "discovery_id": bundle["discovery_id"],
            "project_id": project_ids[0] if project_ids else None,
            "parent_id": task.get("parent_id"),
            "source_language": bundle["original_request"].get("language"),
            "source_request_preserved_at": f"planning/discovery/{bundle['discovery_id']}.json",
        },
        "planning": {
            "title": task["title"],
            "size": task.get("size", "M"),
            "dependencies": list(task.get("dependencies", [])),
            "priority_score": sprint_priority(task),
        },
    }


def materialize(bundle: dict[str, Any], *, root: Path = ROOT, force_tasks: bool = False) -> dict[str, Any]:
    policy = json.loads((root / "harness" / "product-discovery-policy.json").read_text(encoding="utf-8"))
    summary = validate_bundle(bundle, policy)
    discovery_id = bundle["discovery_id"]
    written: list[str] = []

    discovery_path = root / "planning" / "discovery" / f"{discovery_id}.json"
    _atomic_json(discovery_path, bundle)
    written.append(discovery_path.relative_to(root).as_posix())

    for item in bundle["work_items"]:
        target = root / "planning" / DIR_FOR_TYPE[item["type"]] / f"{item['id']}.json"
        record = dict(item)
        record["discovery_id"] = discovery_id
        _atomic_json(target, record)
        written.append(target.relative_to(root).as_posix())

    if bundle["status"] == "approved":
        for task in bundle["tasks"]:
            target = root / "tasks" / f"{task['id']}.json"
            payload = _task_payload(task, bundle)
            if target.exists() and not force_tasks:
                existing = json.loads(target.read_text(encoding="utf-8"))
                if existing != payload:
                    raise PlanningError(f"refusing to overwrite existing task without --force-tasks: {target.relative_to(root)}")
            _atomic_json(target, payload)
            written.append(target.relative_to(root).as_posix())

        sprint = bundle.get("proposed_sprint")
        if sprint:
            target = root / "planning" / "sprints" / f"{sprint['id']}.json"
            record = dict(sprint)
            record.setdefault("capacity_units", policy["sprint"]["default_capacity_units"])
            record["discovery_id"] = discovery_id
            sizes = policy["sizes"]
            by_id = {t["id"]: t for t in bundle["tasks"]}
            record["used_capacity_units"] = sum(int(sizes[by_id[t].get("size", "M")]) for t in record.get("task_ids", []))
            _atomic_json(target, record)
            written.append(target.relative_to(root).as_posix())

    return {**summary, "written": written, "tasks_materialized": len(bundle["tasks"]) if bundle["status"] == "approved" else 0}


def _resolve_inside_root(value: str, root: Path = ROOT) -> Path:
    path = Path(value)
    path = path if path.is_absolute() else root / path
    path = path.resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise PlanningError("input path must stay inside repository") from exc
    if not path.is_file():
        raise PlanningError(f"input file not found: {path}")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate and materialize product-discovery planning artifacts into bounded harness tasks.")
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("validate", "materialize"):
        p = sub.add_parser(name)
        p.add_argument("bundle")
        if name == "materialize":
            p.add_argument("--force-tasks", action="store_true")
    args = ap.parse_args()
    try:
        path = _resolve_inside_root(args.bundle)
        bundle = json.loads(path.read_text(encoding="utf-8"))
        result = validate_bundle(bundle) if args.command == "validate" else materialize(bundle, force_tasks=args.force_tasks)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (PlanningError, json.JSONDecodeError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
