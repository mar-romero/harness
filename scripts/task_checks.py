#!/usr/bin/env python
from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
import tempfile
import shutil
import uuid
from contextlib import contextmanager
import tomllib
from pathlib import Path

from evidence import append as append_evidence
from evidence import validate as validate_evidence
from harnesslib import (
    ROOT, _artifact_lock, _reject_reparse_components, read_provider_active, run_dir,
    runtime_reference, runtime_root,
    safe_task_id,
    write_json_atomic, ensure_exclusive_lock,
)
from worktree import status as worktree_status, wt as worktree_path
from receipt_review import candidate_snapshot


PROVIDERS = ("codex", "opencode", "subscriptions")


def _load_active_task(task_id: str) -> tuple[dict, Path]:
    bindings = []
    for provider in PROVIDERS:
        active = read_provider_active(provider)
        if active is not None and active.get("task_id") == task_id:
            bindings.append((provider, active))

    if not bindings:
        raise ValueError("no active Codex, OpenCode, or subscription task binding matches this task")
    if len(bindings) != 1:
        providers = ", ".join(provider for provider, _ in bindings)
        raise ValueError(f"ambiguous active task binding for {task_id}: {providers}")

    provider, active = bindings[0]
    snapshot_rel = active.get("task_snapshot_path")
    if not isinstance(snapshot_rel, str) or not snapshot_rel:
        raise ValueError(f"{provider} active task binding has no immutable task snapshot")
    task_path = runtime_reference(snapshot_rel)
    if not task_path.is_file():
        raise ValueError(f"immutable task snapshot not found: {task_path}")
    task = json.loads(task_path.read_text(encoding="utf-8"))
    if task.get("id") != task_id:
        raise ValueError("immutable task snapshot id does not match active task")
    return task, task_path


def _is_within(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base.resolve())
        return True
    except ValueError:
        return False


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


def _validate_runtime_consistency(task_id: str, route: dict) -> None:
    """Reject a stale budget before it can weaken the routed task lifecycle."""
    path = run_dir(task_id) / "agent-budget.json"
    if not path.is_file():
        raise ValueError("agent-budget.json missing")
    budget = json.loads(path.read_text(encoding="utf-8"))
    if budget.get("risk") != route.get("risk"):
        raise ValueError(
            f"agent budget risk {budget.get('risk')} != route risk {route.get('risk')}"
        )
    if budget.get("route_agents") != route.get("agents"):
        raise ValueError("agent budget route_agents do not match route agents")


def _project_root(task: dict) -> Path:
    roots: set[str] = set()

    for raw in task.get("files", []):
        path = raw.replace("\\", "/")

        if path == "src" or path.startswith("src/"):
            roots.add(".")
        elif path == "tests" or path.startswith("tests/"):
            roots.add(".")
        elif "/src/" in path:
            roots.add(path.split("/src/", 1)[0])
        elif "/tests/" in path:
            roots.add(path.split("/tests/", 1)[0])
        elif path == "pyproject.toml":
            roots.add(".")
        elif path.endswith("/pyproject.toml"):
            roots.add(path.rsplit("/", 1)[0])

    if len(roots) == 1:
        return Path(next(iter(roots)))

    # Neutral chat tasks are localized by Explorer and may target a repository
    # whose project root is the Git root (for example src/foo.py or package.json).
    # Fall back to the repository root instead of forcing the user to predeclare
    # a monorepo project prefix.
    return Path(".")


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
    env["HARNESS_ACI_PYTHON"] = sys.executable
    # Checks must not consult the operator's Git configuration.  In
    # particular, HOME can point at a profile containing hooks, includes, or
    # unsafe repository overrides.  The child receives an explicit empty
    # configuration namespace instead.
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    # The isolated child deliberately has no user/system Git config.  On
    # managed Windows hosts Git therefore needs an explicit, child-only
    # safe.directory entry or it refuses to inspect the assigned worktree.
    env["GIT_CONFIG_COUNT"] = "2"
    env["GIT_CONFIG_KEY_0"] = "safe.directory"
    env["GIT_CONFIG_VALUE_0"] = str(project.resolve())
    env["GIT_CONFIG_KEY_1"] = "safe.directory"
    try:
        common_root = runtime_root().resolve()
    except ValueError:
        # Unit tests intentionally exercise _safe_env with a PATH that lacks Git.
        # The real runner has Git and replaces this fallback with the common dir.
        common_root = project.resolve()
    env["GIT_CONFIG_VALUE_1"] = str(common_root)
    return env


def _move_overlay_to_recovery(overlay: Path, destination: Path) -> None:
    """Remove a failed overlay from the active path without merging trees."""
    if not overlay.exists():
        return
    if destination.exists():
        destination = destination.with_name(destination.name + "-remainder")
    # shutil.move is the normal path; os.replace is an atomic directory
    # fallback when a mocked or interrupted cleanup reports a move failure.
    try:
        shutil.move(str(overlay), str(destination))
    except Exception as move_error:
        if overlay.exists():
            try:
                os.replace(str(overlay), str(destination))
            except Exception as replace_error:
                raise RuntimeError(
                    f'overlay recovery move failed: {move_error}; '
                    f'atomic fallback failed: {replace_error}'
                ) from replace_error


def _remove_overlay_without_tree_move(overlay: Path) -> None:
    """Remove a failed overlay without relying on rmtree or directory moves.

    This is a last-resort cleanup path used only after the overlay has been
    copied to durable recovery evidence.  It deliberately walks deepest-first
    and does not follow symlinks, so a mocked/partial tree cleanup cannot leave
    generated provider state active while the backup is restored.
    """
    if not overlay.exists():
        return
    for path in sorted(overlay.rglob('*'), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
    overlay.rmdir()


def _restore_legacy_exclusive(held: Path, destination: Path) -> bool:
    """Restore one held legacy file without replacing concurrent state."""
    _reject_reparse_components(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = held.read_bytes()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    if hasattr(os, 'O_BINARY'):
        flags |= os.O_BINARY
    try:
        fd = os.open(destination, flags, 0o600)
    except FileExistsError:
        return False
    created = os.fstat(fd)
    handed_to_file = False
    try:
        with os.fdopen(fd, 'wb') as handle:
            handed_to_file = True
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        # Do not leave a corrupt empty/partial destination behind when the
        # descriptor wrapper or write fails.  Remove only the inode we opened;
        # if a non-cooperating writer replaced it, fail closed and preserve it.
        if not handed_to_file:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            current = os.stat(destination)
            if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                destination.unlink()
        except OSError:
            pass
        raise
    return True


def _preserve_legacy_exclusive(source: Path, destination: Path) -> None:
    """Move a legacy file without replacing an existing preservation copy."""
    _reject_reparse_components(source)
    _reject_reparse_components(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        # Windows rename is atomic and fails when the destination already
        # exists; unlike link-then-unlink it cannot delete a replacement at
        # the source pathname after an identity check.
        os.rename(source, destination)
        return
    try:
        os.link(source, destination)
    except FileExistsError:
        raise
    try:
        _reject_reparse_components(source)
        source_stat = os.stat(source)
        destination_stat = os.stat(destination)
        if (source_stat.st_dev, source_stat.st_ino) != (
            destination_stat.st_dev, destination_stat.st_ino
        ):
            raise RuntimeError('legacy source changed during exclusive preservation')
        # POSIX has no portable Python primitive that unlinks the directory
        # entry only if it is still the inode just checked. Keep the source
        # and fail closed; the hard link remains durable evidence and the
        # caller will restore only if the source is absent.
        return
    except BaseException:
        # The preservation copy is evidence, so keep it if source removal is
        # no longer safe.  The caller records the error and fails the guard.
        raise


def _foreign_active_state(overlay: Path, backup: Path, owner: str) -> str | None:
    """Detect an active binding changed by a writer outside this check run."""
    for provider in PROVIDERS:
        current = overlay / provider / "active-task.json"
        original = backup / provider / "active-task.json"
        current_bytes = current.read_bytes() if current.is_file() else None
        original_bytes = original.read_bytes() if original.is_file() else None
        if current_bytes == original_bytes:
            continue
        if current_bytes is None:
            return f"{provider} active binding disappeared during checks"
        try:
            payload = json.loads(current_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return f"{provider} active binding changed to non-JSON state during checks"
        if payload.get("task_checks_owner") != owner:
            return f"{provider} active binding changed by an unrelated writer during checks"
    return None


@contextmanager
def _provider_runtime_guard(execution_root: Path, child_env: dict[str, str] | None = None):
    """Keep provider-local and legacy fixtures from mutating active state."""
    overlay = execution_root / ".harness" / "overlays"
    migration_lock = run_dir("HARNESS-WORKTREE-MIGRATION-001") / "migration" / "migration.lock.json"
    ensure_exclusive_lock(migration_lock)
    with tempfile.TemporaryDirectory(prefix="harness-check-runtime-") as td:
        migration_guard = _artifact_lock(migration_lock)
        migration_guard.__enter__()
        lock_held = True
        guard_owner = uuid.uuid4().hex
        if child_env is not None:
            child_env["HARNESS_TASK_CHECKS_OWNER"] = guard_owner
        backup = Path(td) / "overlays"
        if overlay.is_dir():
            shutil.copytree(overlay, backup, symlinks=True)
        legacy_backup = Path(td) / "legacy"
        legacy_backup.mkdir()
        legacy_names = (
            "active-task.json", "session.json", "permission-audit.jsonl",
            "catalog-snapshot.json", "model-inventory.json",
            "enriched-inventory.json", "model-selections.json",
        )
        legacy_records = []
        legacy_roots = list(dict.fromkeys((execution_root.resolve(), runtime_root().resolve())))
        setup_complete = False
        try:
            for provider in PROVIDERS:
                for base in legacy_roots:
                    for name in legacy_names:
                        path = base / ".harness" / provider / name
                        _reject_reparse_components(path)
                        if path.is_file():
                            held = legacy_backup / str(len(legacy_records))
                            path.replace(held)
                            legacy_records.append((held, path))
            setup_complete = True
            # Provider activation is itself serialized by the same shared
            # lock. Release it while the test subprocess runs so activation
            # cannot deadlock; reacquire before cleanup/restoration.
            migration_guard.__exit__(None, None, None)
            lock_held = False
            yield
        finally:
            if lock_held:
                migration_guard.__exit__(None, None, None)
                lock_held = False
            migration_guard = _artifact_lock(migration_lock)
            migration_guard.__enter__()
            lock_held = True
            cleanup_errors = []
            restore_overlay = True
            recovery_dir = None
            try:
                foreign_state = _foreign_active_state(overlay, backup, guard_owner) if overlay.exists() else None
                if foreign_state:
                    # Keep the live overlay in place. Moving it to recovery and
                    # restoring the stale backup would silently erase another
                    # session's active task.
                    restore_overlay = False
                    cleanup_errors.append(f"provider overlay ownership collision: {foreign_state}")
                elif overlay.exists():
                    _reject_reparse_components(overlay)
                    recovery_root = (
                        runtime_root() / ".harness" / "legacy-preserved" /
                        "task-checks-overlay-recovery"
                    )
                    recovery_root.mkdir(parents=True, exist_ok=True)
                    recovery_dir = Path(tempfile.mkdtemp(prefix=f"{os.getpid()}-", dir=recovery_root))
                    recovery_overlay = recovery_dir / "generated-overlays"
                    _move_overlay_to_recovery(overlay, recovery_overlay)
            except Exception as exc:
                cleanup_errors.append(f"overlay recovery move failed: {exc}")
                try:
                    if recovery_dir is None:
                        recovery_root = (
                            runtime_root() / ".harness" / "legacy-preserved" /
                            "task-checks-overlay-recovery"
                        )
                        recovery_root.mkdir(parents=True, exist_ok=True)
                        recovery_dir = Path(tempfile.mkdtemp(prefix=f"{os.getpid()}-", dir=recovery_root))
                    if backup.is_dir():
                        shutil.copytree(backup, recovery_dir / "original-overlays", symlinks=True)
                    if overlay.exists():
                        failed_overlay = recovery_dir / "failed-overlays"
                        shutil.copytree(overlay, failed_overlay, symlinks=True)
                        _remove_overlay_without_tree_move(overlay)
                    cleanup_errors.append(f"overlay recovery evidence: {recovery_dir}")
                except Exception as recovery_exc:
                    cleanup_errors.append(f"overlay recovery evidence failed: {recovery_exc}")
                    restore_overlay = False
            try:
                if restore_overlay and backup.is_dir():
                    overlay.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(backup, overlay, symlinks=True)
            except Exception as exc:
                cleanup_errors.append(f"overlay restore failed: {exc}")
            preserved = runtime_root() / ".harness" / "legacy-preserved" / "task-checks" / str(os.getpid())
            generated_index = 0
            for provider in PROVIDERS:
                for base in legacy_roots:
                    for name in legacy_names:
                        if not setup_complete:
                            continue
                        path = base / ".harness" / provider / name
                        try:
                            _reject_reparse_components(path)
                            present = path.is_file()
                        except (OSError, ValueError) as exc:
                            cleanup_errors.append(f"legacy preservation probe failed for {path}: {exc}")
                            continue
                        if present:
                            try:
                                _reject_reparse_components(path)
                                preserved.mkdir(parents=True, exist_ok=True)
                                _preserve_legacy_exclusive(
                                    path, preserved / f"{provider}-{generated_index}-{name}"
                                )
                                generated_index += 1
                            except (OSError, ValueError, RuntimeError) as exc:
                                cleanup_errors.append(f"legacy preservation failed for {path}: {exc}")
            for index, (held, path) in enumerate(legacy_records):
                try:
                    restored = _restore_legacy_exclusive(held, path)
                    if not restored:
                        raise FileExistsError(f"concurrent legacy state already exists: {path}")
                except (OSError, ValueError, RuntimeError) as exc:
                    # Keep a durable recovery copy before TemporaryDirectory cleans
                    # up the held file; never silently lose legacy state.
                    recovery = (
                        runtime_root() / ".harness" / "legacy-preserved" /
                        "task-checks-recovery" / str(os.getpid()) /
                        f"{index}-{path.name}"
                    )
                    try:
                        recovery.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(held, recovery)
                        cleanup_errors.append(
                            f"legacy restore failed for {path}: {exc}; recovery={recovery}"
                        )
                    except (OSError, ValueError) as recovery_exc:
                        cleanup_errors.append(
                            f"legacy restore failed for {path}: {exc}; "
                            f"recovery failed: {recovery_exc}"
                        )
            if cleanup_errors:
                try:
                    raise RuntimeError("; ".join(cleanup_errors))
                finally:
                    migration_guard.__exit__(None, None, None)
                    lock_held = False
            migration_guard.__exit__(None, None, None)
            lock_held = False


def _clear_python_caches(project: Path) -> None:
    """Do not execute stale bytecode after a candidate source update."""
    for base in (project / "tests", project / "scripts"):
        if not base.is_dir():
            continue
        for cache in base.rglob("__pycache__"):
            if cache.is_dir():
                shutil.rmtree(cache)


@contextmanager
def _isolated_child_env(env: dict[str, str]):
    """Add a disposable Windows profile without widening ``_safe_env``.

    ``_safe_env`` intentionally forwards only its historical allowlist.  The
    task runner nevertheless needs Git to see a profile while checking an
    isolated worktree, so this child-only seam supplies an empty profile and
    removes it immediately after the subprocess exits.
    """
    if os.name != "nt":
        yield env
        return

    # Windows can keep a child-created Git handle alive briefly after the
    # subprocess has returned.  Cleanup must not turn a completed check into a
    # runner failure; the profile contains no durable state or secrets.
    with tempfile.TemporaryDirectory(
        prefix="harness-task-check-profile-",
        ignore_cleanup_errors=(os.name == "nt"),
    ) as raw:
        profile = Path(raw)
        child = dict(env)
        child["HOME"] = str(profile)
        child["USERPROFILE"] = str(profile)
        child["APPDATA"] = str(profile / "AppData" / "Roaming")
        child["LOCALAPPDATA"] = str(profile / "AppData" / "Local")
        child["GIT_CONFIG_NOSYSTEM"] = "1"
        child["GIT_CONFIG_GLOBAL"] = os.devnull
        child["GIT_CONFIG_SYSTEM"] = os.devnull
        yield child


def _run(
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout: int = 600,
) -> dict:
    """Run one allowlisted command; a timeout is a recorded FAIL, never an abort."""
    with _isolated_child_env(env) as child_env:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=child_env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        timed_out = False
        exit_code: int
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired as exc:
            # Best-effort Windows tree-kill: the direct child may leave
            # grandchildren (git, unittest children) holding pipes, so kill
            # the whole tree before reaping.
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                    check=False,
                )
            else:
                proc.kill()
            try:
                stdout, stderr = proc.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                stdout, stderr = exc.stdout, exc.stderr
            # Sentinel exit code plus explicit marker: timeout counts as a
            # command failure so the partial checks-report.json still gets
            # persisted with status FAIL instead of aborting run_checks.
            timed_out = True
            exit_code = -1

    result = {
        "argv": argv,
        "cwd": str(cwd),
        "exit_code": exit_code,
        "stdout": (stdout or "")[-12000:],
        "stderr": (stderr or "")[-12000:],
    }
    if timed_out:
        result["timed_out"] = True
    return result



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
                # Task file surfaces may legitimately name directories
                # (e.g. "harness/schema", "tests"). Existence means the
                # planned path is present as a file OR a directory; the
                # containment (reparse-point/escape) check above is unchanged.
                "exists": candidate.exists(),
            }
        )

    return results


def run_checks(task_id: str) -> dict:
    task_id = safe_task_id(task_id)

    _require_checks_step(task_id)
    task, _ = _load_active_task(task_id)
    route = _route(task_id)
    _validate_runtime_consistency(task_id, route)

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

    candidate_before = candidate_snapshot(task_id)
    env = _safe_env(project)
    commands: list[dict] = []

    # Language-agnostic safety check. This is intentionally deterministic and
    # does not execute project-defined scripts.
    commands.append(
        _run(
            ["git", "-c", "core.whitespace=cr-at-eol", "diff", "--check"],
            execution_root,
            env,
        )
    )

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
    # Timeout budget evidence (measured, Windows dev host): the full suite
    # finishes in ~483s when healthy, but ~600-900s+ in the isolated child
    # env on slower/disc-defended hosts, with high run-to-run variance.
    # Single bounded unittest-discover command (coverage must not be split)
    # gets 1200s headroom; other commands keep their existing timeouts.
    if tests.is_dir():
        _clear_python_caches(project)
        with _provider_runtime_guard(execution_root, env):
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
                    timeout=1200,
                )
            )

    command_failures = [
        row for row in commands if row["exit_code"] != 0
    ]

    static_failures = [
        row for row in static_checks if row.get("status") != "PASS"
    ]

    candidate = candidate_snapshot(task_id)
    candidate_mutated = candidate_before["subject_hash"] != candidate["subject_hash"]
    if candidate_mutated:
        static_checks.append(
            {
                "check": "candidate-binding",
                "status": "FAIL",
                "before": candidate_before["subject_hash"],
                "after": candidate["subject_hash"],
                "error": "candidate changed while authoritative checks were running",
            }
        )
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
        "candidate_subject_hash": candidate["subject_hash"],
        "candidate_before_subject_hash": candidate_before["subject_hash"],
        "candidate_mutated_during_checks": candidate_mutated,
        "candidate_base_commit": candidate["base_commit"],
        "scope_expansion_sha256": candidate.get("scope_expansion_sha256"),
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
        command=f"python scripts/task_checks.py run {task_id}",
        exit_code=0 if passed else 1,
        artifact=report_path.relative_to(runtime_root()).as_posix(),
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
