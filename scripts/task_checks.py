#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

from evidence import append as append_evidence
from evidence import validate as validate_evidence
from harnesslib import ROOT, load_json, run_dir, safe_task_id, write_json_atomic
from worktree import status as worktree_status, wt as worktree_path


PROVIDERS = ("codex", "opencode")


def _load_active_task(task_id: str) -> tuple[dict, Path]:
    bindings = []
    for provider in PROVIDERS:
        active_path = ROOT / ".harness" / provider / "active-task.json"
        if not active_path.is_file():
            continue
        active = json.loads(active_path.read_text(encoding="utf-8"))
        if active.get("task_id") == task_id:
            bindings.append((provider, active, active_path))

    if not bindings:
        raise ValueError("no active Codex or OpenCode task binding matches this task")
    if len(bindings) != 1:
        providers = ", ".join(provider for provider, _, _ in bindings)
        raise ValueError(f"ambiguous active task binding for {task_id}: {providers}")

    provider, active, _ = bindings[0]
    snapshot_rel = active.get("task_snapshot_path")
    if not isinstance(snapshot_rel, str) or not snapshot_rel:
        raise ValueError(f"{provider} active task binding has no immutable task snapshot")
    task_path = (ROOT / snapshot_rel).resolve()
    task_path.relative_to(ROOT.resolve())
    if not task_path.is_file():
        raise ValueError(f"immutable task snapshot not found: {task_path}")
    task = json.loads(task_path.read_text(encoding="utf-8"))
    if task.get("id") != task_id:
        raise ValueError("immutable task snapshot id does not match active task")
    return task, task_path


def _require_checks_step(task_id: str) -> dict:
    progress_path = run_dir(task_id) / "progress.json"
    if not progress_path.is_file():
        raise ValueError("progress.json missing")

    progress = json.loads(progress_path.read_text(encoding="utf-8"))

    if progress.get("current_step") != "CHECKS":
        raise ValueError(
            f"task checks may run only at CHECKS; "
            f"current_step={progress.get('current_step')}"
        )

    return progress


def _route(task_id: str) -> dict:
    path = run_dir(task_id) / "route.json"
    if not path.is_file():
        raise ValueError("route.json missing")
    return json.loads(path.read_text(encoding="utf-8"))


def _project_root(task: dict) -> Path:
    roots: set[str] = set()

    for raw in task.get("files", []):
        path = raw.replace("\\", "/")

        if "/src/" in path:
            roots.add(path.split("/src/", 1)[0])
        elif "/tests/" in path:
            roots.add(path.split("/tests/", 1)[0])
        elif path.endswith("/pyproject.toml"):
            roots.add(path.rsplit("/", 1)[0])

    if len(roots) != 1:
        raise ValueError(
            "cannot infer one project root from task.files: "
            + ", ".join(sorted(roots))
        )

    return Path(next(iter(roots)))


def _module_name(task: dict) -> str | None:
    modules: set[str] = set()

    for raw in task.get("files", []):
        parts = raw.replace("\\", "/").split("/")
        try:
            idx = parts.index("src")
        except ValueError:
            continue

        if idx + 1 < len(parts):
            modules.add(parts[idx + 1])

    if len(modules) > 1:
        raise ValueError(
            "multiple Python modules inferred: " + ", ".join(sorted(modules))
        )

    return next(iter(modules), None)


def _execution_root(task_id: str, route: dict) -> Path:
    if route.get("isolation") == "worktree":
        state = worktree_status(task_id)

        if not state.get("exists"):
            raise ValueError("assigned worktree does not exist")

        if not state.get("lock"):
            raise ValueError("writer lock does not exist")

        return worktree_path(task_id).resolve()

    return ROOT.resolve()


def _safe_env(project: Path) -> dict[str, str]:
    allowed = (
        "PATH",
        "HOME",
        "TMPDIR",
        "TMP",
        "TEMP",
        "LANG",
        "LC_ALL",
        "CI",
        "SYSTEMROOT",
        "WINDIR",
        "PATHEXT",
    )

    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env["PYTHONPATH"] = str(project / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run(argv: list[str], cwd: Path, env: dict[str, str]) -> dict:
    proc = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
        shell=False,
    )

    return {
        "argv": argv,
        "cwd": str(cwd),
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-12000:],
        "stderr": proc.stderr[-12000:],
    }



def _python_syntax_check(src: Path) -> dict:
    files = sorted(src.rglob("*.py"))
    failures = []
    for path in files:
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except Exception as exc:
            failures.append({"path": str(path), "error": str(exc)})
    return {
        "check": "python-ast-parse",
        "status": "PASS" if not failures else "FAIL",
        "files_checked": len(files),
        "failures": failures,
    }

def _validate_planned_files(task: dict, execution_root: Path) -> list[dict]:
    results = []

    for raw in task.get("files", []):
        candidate = (execution_root / raw).resolve()
        candidate.relative_to(execution_root)

        results.append(
            {
                "path": raw,
                "exists": candidate.is_file(),
            }
        )

    return results


def run_checks(task_id: str) -> dict:
    task_id = safe_task_id(task_id)

    _require_checks_step(task_id)
    task, _ = _load_active_task(task_id)
    route = _route(task_id)

    execution_root = _execution_root(task_id, route)
    project_rel = _project_root(task)
    project = (execution_root / project_rel).resolve()
    project.relative_to(execution_root)

    if not project.is_dir():
        raise ValueError(f"project directory missing: {project_rel.as_posix()}")

    file_results = _validate_planned_files(task, execution_root)
    missing = [x["path"] for x in file_results if not x["exists"]]

    static_checks: list[dict] = []

    pyproject = project / "pyproject.toml"
    if pyproject.is_file():
        try:
            with pyproject.open("rb") as handle:
                tomllib.load(handle)
            static_checks.append(
                {
                    "check": "pyproject-parse",
                    "status": "PASS",
                }
            )
        except Exception as exc:
            static_checks.append(
                {
                    "check": "pyproject-parse",
                    "status": "FAIL",
                    "error": str(exc),
                }
            )

    env = _safe_env(project)
    commands: list[dict] = []

    src = project / "src"
    tests = project / "tests"

    if src.is_dir():
        static_checks.append(_python_syntax_check(src))

    module = _module_name(task)

    # Project/setup smoke test.
    if module and (src / module / "__main__.py").is_file():
        commands.append(
            _run(
                [sys.executable, "-m", module, "--help"],
                project,
                env,
            )
        )

    # Implementation tasks with tests use the project's real unit suite.
    if tests.is_dir():
        commands.append(
            _run(
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-v",
                ],
                project,
                env,
            )
        )

    command_failures = [
        row for row in commands if row["exit_code"] != 0
    ]

    static_failures = [
        row for row in static_checks if row.get("status") != "PASS"
    ]

    passed = (
        not missing
        and not command_failures
        and not static_failures
        and bool(commands or static_checks)
    )

    report = {
        "schema_version": 1,
        "task_id": task_id,
        "execution_root": str(execution_root),
        "project_root": project_rel.as_posix(),
        "isolation": route.get("isolation"),
        "planned_files": file_results,
        "static_checks": static_checks,
        "commands": commands,
        "status": "PASS" if passed else "FAIL",
    }

    report_path = run_dir(task_id) / "checks-report.json"
    write_json_atomic(report_path, report)

    evidence = append_evidence(
        task_id,
        "checks",
        "DETERMINISTIC",
        "Allowlisted task check suite executed in authoritative task workspace",
        "PASS" if passed else "FAIL",
        "check-runner",
        command=f"python3 scripts/task_checks.py run {task_id}",
        exit_code=0 if passed else 1,
        artifact=report_path.relative_to(ROOT).as_posix(),
    )

    chain = validate_evidence(task_id)
    if not chain.get("valid"):
        raise ValueError(
            "evidence chain invalid after task checks: "
            + str(chain.get("reason"))
        )

    report["evidence_record_hash"] = evidence["record_hash"]
    report["evidence_head_hash"] = chain["head_hash"]

    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run")
    run_parser.add_argument("task")

    args = parser.parse_args()

    try:
        result = run_checks(args.task)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 2

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
