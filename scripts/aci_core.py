#!/usr/bin/env python3
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from codegraph_bridge import explore as codegraph_explore, status as codegraph_status
from context_graph import build_graph
from symbol_context import build_repo_map, build_snippets, query_tokens
from hook_bus import pre_tool as hook_pre_tool, post_tool as hook_post_tool

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "harness" / "aci-policy.json"


class ACIError(RuntimeError):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ACIError(f"missing policy file: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise ACIError(f"invalid JSON policy: {path.relative_to(ROOT)}: {exc}") from exc


POLICY = _load_json(POLICY_PATH)


def _result(tool: str, data: Any, *, truncated: bool = False) -> dict[str, Any]:
    return {
        "ok": True,
        "tool": tool,
        "data": data,
        "error": None,
        "meta": {"root": str(ROOT), "truncated": truncated},
    }


def _failure(tool: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "tool": tool,
        "data": None,
        "error": message,
        "meta": {"root": str(ROOT), "truncated": False},
    }


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _resolve_repo_path(value: str | None, *, must_exist: bool = True) -> Path:
    value = value or "."
    raw = Path(value)
    candidate = raw if raw.is_absolute() else ROOT / raw
    # Resolve without requiring existence first so traversal/outside-root attempts are
    # classified as boundary violations rather than leaking filesystem existence.
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(ROOT)
    except ValueError as exc:
        raise ACIError(f"path resolves outside repository: {value}") from exc
    if must_exist and not resolved.exists():
        raise ACIError(f"path does not exist: {value}")
    if resolved != ROOT and _is_excluded(resolved):
        raise ACIError(f"path is excluded by ACI policy: {_rel(resolved)}")
    return resolved


def _excluded_rel(rel: str) -> bool:
    rel = rel.replace("\\", "/").lstrip("./")
    parts = rel.split("/") if rel else []
    for excluded in POLICY.get("exclude_dirs", []):
        ex = str(excluded).strip("/")
        if rel == ex or rel.startswith(ex + "/"):
            return True
        if ex in parts:
            return True
    name = parts[-1] if parts else rel
    for pattern in POLICY.get("exclude_globs", []):
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel, pattern):
            return True
    return False


def _is_excluded(path: Path) -> bool:
    try:
        return _excluded_rel(_rel(path))
    except ValueError:
        return True


def _is_text_candidate(path: Path) -> bool:
    if not path.is_file() or _is_excluded(path) or path.is_symlink():
        return False
    if path.stat().st_size > int(POLICY.get("max_file_bytes", 120000)):
        return False
    if path.suffix.lower() in set(POLICY.get("text_extensions", [])):
        return True
    return path.name in set(POLICY.get("text_names", []))


def _iter_files(base: Path) -> Iterable[Path]:
    if base.is_file():
        if _is_text_candidate(base):
            yield base
        return
    # Prune excluded and symlinked directories before descent. This keeps search
    # bounded on repositories with large node_modules/build/worktree trees.
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        current = Path(dirpath)
        kept_dirs = []
        for dirname in sorted(dirnames):
            child = current / dirname
            if child.is_symlink() or _is_excluded(child):
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs
        for filename in sorted(filenames):
            path = current / filename
            if _is_text_candidate(path):
                yield path


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ACIError(f"file is not UTF-8 text: {_rel(path)}") from exc


def repo_search(query: str, path: str = ".", regex: bool = False, max_results: int = 40) -> dict[str, Any]:
    tool = "repo_search"
    if not query:
        return _failure(tool, "query must be non-empty")
    try:
        base = _resolve_repo_path(path)
        cap = min(max(1, int(max_results)), int(POLICY.get("max_search_results", 80)))
        matcher = re.compile(query) if regex else None
        items: list[dict[str, Any]] = []
        scanned = 0
        for file_path in _iter_files(base):
            scanned += 1
            try:
                lines = _read_text(file_path).splitlines()
            except ACIError:
                continue
            for line_no, line in enumerate(lines, 1):
                matched = bool(matcher.search(line)) if matcher else query.casefold() in line.casefold()
                if matched:
                    items.append({
                        "path": _rel(file_path),
                        "line": line_no,
                        "text": line[:500],
                    })
                    if len(items) >= cap:
                        return _result(tool, {"query": query, "regex": regex, "matches": items, "files_scanned": scanned}, truncated=True)
        return _result(tool, {"query": query, "regex": regex, "matches": items, "files_scanned": scanned})
    except (ACIError, re.error, ValueError) as exc:
        return _failure(tool, str(exc))


def _read_cache_path() -> Path | None:
    if not POLICY.get("read_once_cache", True):
        return None
    session = os.environ.get("HARNESS_ACI_SESSION_ID")
    if not session or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", session):
        return None
    rel = str(POLICY.get("read_once_cache_dir", ".harness/cache/aci"))
    path = ROOT / rel / f"{session}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_cache_load() -> dict[str, Any]:
    path = _read_cache_path()
    if not path or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_cache_save(data: dict[str, Any]) -> None:
    path = _read_cache_path()
    if not path:
        return
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def repo_read_range(path: str, start_line: int = 1, end_line: int = 200, force: bool = False) -> dict[str, Any]:
    tool = "repo_read_range"
    try:
        file_path = _resolve_repo_path(path)
        if not file_path.is_file() or not _is_text_candidate(file_path):
            raise ACIError(f"not an allowed text file: {path}")
        start = max(1, int(start_line))
        limit = int(POLICY.get("max_read_lines", 400))
        end = max(start, int(end_line))
        if end - start + 1 > limit:
            end = start + limit - 1
        lines = _read_text(file_path).splitlines()
        actual_end = min(end, len(lines))
        digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
        rel = _rel(file_path)
        cache_key = f"{rel}:{start}:{actual_end}:{digest}"
        cache = _read_cache_load()
        if not force and cache_key in cache:
            return _result(tool, {
                "path": rel,
                "start_line": start,
                "end_line": actual_end,
                "total_lines": len(lines),
                "sha256": digest,
                "lines": [],
                "cache_hit": True,
                "instruction": "Exact unchanged range was already delivered in this agent run. Reuse it; call again with force=true only if the content is genuinely no longer available in context.",
            }, truncated=end < int(end_line))
        selected = [
            {"line": idx, "text": lines[idx - 1]}
            for idx in range(start, actual_end + 1)
            if idx <= len(lines)
        ]
        if _read_cache_path():
            cache[cache_key] = {"path": rel, "start_line": start, "end_line": actual_end, "sha256": digest}
            # Keep this tiny and per-run; older keys are harmless but cap them.
            if len(cache) > 256:
                cache = dict(list(cache.items())[-256:])
            _read_cache_save(cache)
        return _result(tool, {
            "path": rel,
            "start_line": start,
            "end_line": actual_end,
            "total_lines": len(lines),
            "sha256": digest,
            "lines": selected,
            "cache_hit": False,
        }, truncated=end < int(end_line))
    except (ACIError, ValueError) as exc:
        return _failure(tool, str(exc))


def _declaration_patterns(name: str) -> list[re.Pattern[str]]:
    escaped = re.escape(name)
    return [
        re.compile(rf"^\s*(?:async\s+)?def\s+{escaped}\b"),
        re.compile(rf"^\s*class\s+{escaped}\b"),
        re.compile(rf"^\s*(?:export\s+)?(?:async\s+)?function\s+{escaped}\b"),
        re.compile(rf"^\s*(?:export\s+)?(?:class|interface|type|enum)\s+{escaped}\b"),
        re.compile(rf"^\s*(?:export\s+)?(?:const|let|var)\s+{escaped}\b"),
        re.compile(rf"^\s*(?:public\s+|private\s+|protected\s+|static\s+|final\s+|abstract\s+)*(?:class|interface|enum|record)\s+{escaped}\b"),
        re.compile(rf"^\s*(?:func|fn)\s+{escaped}\b"),
    ]


def repo_symbol(name: str, path: str = ".", max_results: int = 20) -> dict[str, Any]:
    tool = "repo_symbol"
    if not name:
        return _failure(tool, "name must be non-empty")
    try:
        base = _resolve_repo_path(path)
        cap = min(max(1, int(max_results)), 50)
        patterns = _declaration_patterns(name)
        matches: list[dict[str, Any]] = []
        for file_path in _iter_files(base):
            try:
                lines = _read_text(file_path).splitlines()
            except ACIError:
                continue
            for line_no, line in enumerate(lines, 1):
                if any(p.search(line) for p in patterns):
                    matches.append({"path": _rel(file_path), "line": line_no, "text": line[:500]})
                    if len(matches) >= cap:
                        return _result(tool, {"name": name, "declarations": matches, "mode": "lexical"}, truncated=True)
        return _result(tool, {"name": name, "declarations": matches, "mode": "lexical"})
    except (ACIError, ValueError) as exc:
        return _failure(tool, str(exc))


def repo_callers(symbol: str, path: str = ".", max_results: int = 30) -> dict[str, Any]:
    tool = "repo_callers"
    if not symbol:
        return _failure(tool, "symbol must be non-empty")
    try:
        result = repo_search(symbol, path=path, regex=False, max_results=max_results + 20)
        if not result["ok"]:
            return _failure(tool, str(result["error"]))
        decls = repo_symbol(symbol, path=path, max_results=50)
        declaration_keys = {
            (item["path"], item["line"])
            for item in (decls.get("data") or {}).get("declarations", [])
        }
        refs = [
            item for item in result["data"]["matches"]
            if (item["path"], item["line"]) not in declaration_keys
        ][: max(1, int(max_results))]
        return _result(tool, {
            "symbol": symbol,
            "reference_candidates": refs,
            "mode": "lexical-candidate-only",
            "warning": "These are lexical reference candidates, not a semantic call graph.",
        }, truncated=len(result["data"]["matches"]) > len(refs) + len(declaration_keys))
    except (ACIError, ValueError) as exc:
        return _failure(tool, str(exc))


_IMPORT_PATTERNS = [
    re.compile(r"^\s*from\s+([\w.]+)\s+import\b"),
    re.compile(r"^\s*import\s+([\w.]+)"),
    re.compile(r"^\s*import\s+.*?\s+from\s+['\"]([^'\"]+)['\"]"),
    re.compile(r"require\(\s*['\"]([^'\"]+)['\"]\s*\)"),
    re.compile(r"^\s*use\s+([\w:]+)"),
    re.compile(r"^\s*#include\s*[<\"]([^>\"]+)[>\"]"),
]


def repo_dependencies(path: str) -> dict[str, Any]:
    tool = "repo_dependencies"
    try:
        file_path = _resolve_repo_path(path)
        if not file_path.is_file() or not _is_text_candidate(file_path):
            raise ACIError(f"not an allowed text file: {path}")
        deps: list[dict[str, Any]] = []
        seen: set[tuple[int, str]] = set()
        for line_no, line in enumerate(_read_text(file_path).splitlines(), 1):
            for pattern in _IMPORT_PATTERNS:
                match = pattern.search(line)
                if match:
                    value = match.group(1)
                    key = (line_no, value)
                    if key not in seen:
                        deps.append({"line": line_no, "dependency": value, "text": line[:500]})
                        seen.add(key)
                    break
        return _result(tool, {"path": _rel(file_path), "dependencies": deps, "mode": "lexical"})
    except ACIError as exc:
        return _failure(tool, str(exc))


def repo_explore(query: str, max_files: int = 8) -> dict[str, Any]:
    """Return a compact semantic/repository exploration without whole-file dumps.

    Prefer CodeGraph when an index is available. Otherwise use the harness's
    local graph + declaration/snippet fallback, keeping the same bounded ACI
    surface for every provider.
    """
    tool = "repo_explore"
    query = (query or "").strip()
    if not query:
        return _failure(tool, "query must be non-empty")
    try:
        max_files = min(max(1, int(max_files)), int(POLICY.get("repo_explore_max_files", 12)))
        max_tokens = int(POLICY.get("repo_explore_max_tokens", 7000))
        cg = codegraph_explore(query, max_files=max_files, max_chars=max_tokens * 4)
        if cg.get("ok"):
            return _result(tool, {
                "backend": "codegraph",
                "query": query,
                "paths": cg.get("paths") or [],
                "source_and_call_paths": cg.get("text") or "",
                "estimated_tokens": max(1, len(str(cg.get("text") or "")) // 4),
                "instruction": "Treat returned source as already read. Use repo_read_range only for a detail not present here.",
            }, truncated=bool(cg.get("truncated")))

        # Offline/zero-dependency fallback. Localize by query terms in paths and
        # lexical matches, then build a symbol-level map/snippet pack.
        wanted = list(sorted(query_tokens(query), key=lambda x: (-len(x), x)))[:6]
        candidate_paths: list[str] = []
        seen: set[str] = set()
        for token in wanted:
            search = repo_search(token, max_results=max_files * 3)
            if search.get("ok"):
                for item in (search.get("data") or {}).get("matches", []):
                    rel = str(item.get("path") or "")
                    if rel and rel not in seen:
                        seen.add(rel)
                        candidate_paths.append(rel)
                        if len(candidate_paths) >= max_files:
                            break
            if len(candidate_paths) >= max_files:
                break
        graph = build_graph()
        if len(candidate_paths) < max_files:
            for rel in graph:
                low = rel.lower()
                if any(token in low for token in wanted) and rel not in seen:
                    seen.add(rel)
                    candidate_paths.append(rel)
                    if len(candidate_paths) >= max_files:
                        break
        repo_map = build_repo_map(
            candidate_paths, query, [], graph,
            token_budget=min(1400, max_tokens // 4),
        )
        snippets = build_snippets(
            candidate_paths, query, [], graph,
            token_budget=max(1000, max_tokens - int(repo_map.get("estimated_tokens") or 0)),
            max_symbols_per_file=2,
            max_lines_per_symbol=int(POLICY.get("repo_explore_max_lines_per_symbol", 70)),
        )
        return _result(tool, {
            "backend": "builtin-symbol",
            "query": query,
            "codegraph_status": codegraph_status(),
            "paths": sorted(set(repo_map.get("files") or []) | set(snippets.get("files") or [])),
            "repo_map": repo_map.get("text") or "",
            "symbol_snippets": snippets.get("snippets") or [],
            "estimated_tokens": int(repo_map.get("estimated_tokens") or 0) + int(snippets.get("estimated_tokens") or 0),
            "instruction": "Use these snippets first; do not read complete files unless a missing detail requires it.",
        })
    except (ACIError, ValueError, OSError) as exc:
        return _failure(tool, str(exc))


def _run(argv: list[str], *, timeout: int, max_chars: int | None = None) -> dict[str, Any]:
    env = {key: os.environ[key] for key in POLICY.get("pass_env", []) if key in os.environ}
    # Ensure subprocesses can resolve common binaries even in an unusually sparse environment.
    env.setdefault("PATH", os.defpath)
    completed = subprocess.run(
        argv,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
        check=False,
    )
    cap = int(max_chars or POLICY.get("max_output_chars", 30000))
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    truncated = len(stdout) > cap or len(stderr) > cap
    return {
        "argv": argv,
        "exit_code": completed.returncode,
        "stdout": stdout[:cap],
        "stderr": stderr[:cap],
        "truncated": truncated,
    }


def _status_path(line: str) -> str | None:
    if len(line) < 4:
        return None
    value = line[3:]
    # Rename format is usually "old -> new"; classify using the destination.
    if " -> " in value:
        value = value.rsplit(" -> ", 1)[-1]
    return value.strip().strip('"')


def git_status() -> dict[str, Any]:
    tool = "git_status"
    try:
        run = _run(["git", "status", "--porcelain=v1", "--branch"], timeout=30)
        if run["exit_code"] != 0:
            return _failure(tool, run["stderr"] or "git status failed")
        lines = run["stdout"].splitlines()
        branch = lines[0] if lines and lines[0].startswith("##") else None
        raw_changes = lines[1:] if branch else lines
        changes = []
        hidden = 0
        for line in raw_changes:
            rel = _status_path(line)
            if rel and _excluded_rel(rel):
                hidden += 1
                continue
            changes.append(line)
        return _result(tool, {
            "branch": branch,
            "changes": changes,
            "hidden_excluded_changes": hidden,
            "clean": not raw_changes,
        }, truncated=run["truncated"])
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return _failure(tool, f"git status unavailable: {exc}")


def git_diff(path: str | None = None, staged: bool = False, max_chars: int = 30000) -> dict[str, Any]:
    tool = "git_diff"
    try:
        prefix = ["git", "diff", "--no-ext-diff", "--no-color"]
        if staged:
            prefix.append("--cached")
        hidden = 0
        if path:
            checked = _resolve_repo_path(path)
            allowed_paths = [_rel(checked)]
        else:
            names = _run(prefix + ["--name-only"], timeout=30, max_chars=100000)
            if names["exit_code"] != 0:
                return _failure(tool, names["stderr"] or "git diff --name-only failed")
            raw_paths = [line for line in names["stdout"].splitlines() if line]
            allowed_paths = [rel for rel in raw_paths if not _excluded_rel(rel)]
            hidden = len(raw_paths) - len(allowed_paths)
        if not allowed_paths:
            return _result(tool, {"staged": bool(staged), "path": path, "diff": "", "hidden_excluded_files": hidden})
        run = _run(prefix + ["--", *allowed_paths], timeout=60, max_chars=max_chars)
        if run["exit_code"] != 0:
            return _failure(tool, run["stderr"] or "git diff failed")
        return _result(tool, {
            "staged": bool(staged),
            "path": path,
            "diff": run["stdout"],
            "hidden_excluded_files": hidden,
        }, truncated=run["truncated"])
    except (ACIError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return _failure(tool, f"git diff unavailable: {exc}")


def _profile_run(tool: str, kind: str, profile: str) -> dict[str, Any]:
    try:
        profiles = POLICY.get("profiles", {})
        cfg = profiles.get(profile)
        if not isinstance(cfg, dict):
            raise ACIError(f"unknown profile: {profile}")
        if cfg.get("kind") != kind:
            raise ACIError(f"profile {profile} is kind {cfg.get('kind')!r}, expected {kind!r}")
        argv = cfg.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv):
            raise ACIError(f"profile {profile} has invalid argv")
        timeout = min(
            int(cfg.get("timeout_seconds", POLICY.get("command_timeout_seconds", 180))),
            int(POLICY.get("command_timeout_seconds", 180)),
        )
        run = _run(argv, timeout=max(1, timeout))
        return _result(tool, {"profile": profile, "kind": kind, **run}, truncated=run["truncated"])
    except (ACIError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return _failure(tool, str(exc))


def tests_run(profile: str = "tests.unit") -> dict[str, Any]:
    return _profile_run("tests_run", "test", profile)


def lint_run(profile: str = "lint.python-compile") -> dict[str, Any]:
    return _profile_run("lint_run", "lint", profile)


def diagnostics_get(profile: str = "diagnostics.harness") -> dict[str, Any]:
    return _profile_run("diagnostics_get", "diagnostic", profile)


TOOLS = {
    "repo_explore": repo_explore,
    "repo_search": repo_search,
    "repo_read_range": repo_read_range,
    "repo_symbol": repo_symbol,
    "repo_callers": repo_callers,
    "repo_dependencies": repo_dependencies,
    "git_status": git_status,
    "git_diff": git_diff,
    "tests_run": tests_run,
    "lint_run": lint_run,
    "diagnostics_get": diagnostics_get,
}


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    fn = TOOLS.get(name)
    if fn is None:
        raise ACIError(f"unknown ACI tool: {name}")
    args = arguments or {}
    if not isinstance(args, dict):
        raise ACIError("tool arguments must be a JSON object")
    decision = hook_pre_tool(name, args)
    if not decision.get("allow", False):
        result = _failure(name, str(decision.get("reason") or "canonical hook bus denied ACI tool"))
        hook_post_tool(name, result)
        return result
    try:
        result = fn(**args)
    except TypeError as exc:
        result = _failure(name, f"invalid arguments: {exc}")
    hook_post_tool(name, result)
    return result


def tool_definitions() -> list[dict[str, Any]]:
    object_output = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "tool": {"type": "string"},
            "data": {},
            "error": {"type": ["string", "null"]},
            "meta": {"type": "object"},
        },
        "required": ["ok", "tool", "data"],
    }
    read_annotations = {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    exec_annotations = {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    defs = [
        {
            "name": "repo_explore",
            "title": "Explore Repository Semantically",
            "description": "Return a token-bounded repository map plus relevant symbol source/call paths. Uses CodeGraph when indexed and a local symbol-graph fallback otherwise. Prefer this before repeated search/read loops.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "max_files": {"type": "integer", "minimum": 1, "maximum": 12, "default": 8},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            "outputSchema": object_output,
            "annotations": read_annotations,
        },
        {
            "name": "repo_search",
            "title": "Repository Search",
            "description": "Search allowed repository text files with bounded structured output. Prefer this over grep/rg/find.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "path": {"type": "string", "default": "."},
                    "regex": {"type": "boolean", "default": False},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 80, "default": 40},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            "outputSchema": object_output,
            "annotations": read_annotations,
        },
        {
            "name": "repo_read_range",
            "title": "Read Repository Range",
            "description": "Read a bounded line range from one allowed UTF-8 repository file, with SHA-256 metadata.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "start_line": {"type": "integer", "minimum": 1, "default": 1},
                    "end_line": {"type": "integer", "minimum": 1, "default": 200},
                    "force": {"type": "boolean", "default": False, "description": "Re-send an unchanged range that was already delivered in this agent run."},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            "outputSchema": object_output,
            "annotations": read_annotations,
        },
        {
            "name": "repo_symbol",
            "title": "Find Symbol Declarations",
            "description": "Find lexical declaration candidates for a symbol across allowed repository text files.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "path": {"type": "string", "default": "."},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            "outputSchema": object_output,
            "annotations": read_annotations,
        },
        {
            "name": "repo_callers",
            "title": "Find Reference Candidates",
            "description": "Find bounded lexical reference candidates for a symbol. Results are not a semantic call graph.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "minLength": 1},
                    "path": {"type": "string", "default": "."},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 80, "default": 30},
                },
                "required": ["symbol"],
                "additionalProperties": False,
            },
            "outputSchema": object_output,
            "annotations": read_annotations,
        },
        {
            "name": "repo_dependencies",
            "title": "Extract File Dependencies",
            "description": "Extract lexical imports/includes/requires from one allowed repository text file.",
            "inputSchema": {
                "type": "object",
                "properties": {"path": {"type": "string", "minLength": 1}},
                "required": ["path"],
                "additionalProperties": False,
            },
            "outputSchema": object_output,
            "annotations": read_annotations,
        },
        {
            "name": "git_status",
            "title": "Git Status",
            "description": "Return structured repository branch/working-tree status without arbitrary shell execution.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "outputSchema": object_output,
            "annotations": read_annotations,
        },
        {
            "name": "git_diff",
            "title": "Git Diff",
            "description": "Return a bounded Git diff for the repository or one allowed path.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": ["string", "null"], "default": None},
                    "staged": {"type": "boolean", "default": False},
                    "max_chars": {"type": "integer", "minimum": 1000, "maximum": 50000, "default": 30000},
                },
                "additionalProperties": False,
            },
            "outputSchema": object_output,
            "annotations": read_annotations,
        },
        {
            "name": "tests_run",
            "title": "Run Named Test Profile",
            "description": "Run a named test profile from harness/aci-policy.json. Arbitrary commands are not accepted.",
            "inputSchema": {
                "type": "object",
                "properties": {"profile": {"type": "string", "default": "tests.unit"}},
                "additionalProperties": False,
            },
            "outputSchema": object_output,
            "annotations": exec_annotations,
        },
        {
            "name": "lint_run",
            "title": "Run Named Lint Profile",
            "description": "Run a named lint/static profile from harness/aci-policy.json. Arbitrary commands are not accepted.",
            "inputSchema": {
                "type": "object",
                "properties": {"profile": {"type": "string", "default": "lint.python-compile"}},
                "additionalProperties": False,
            },
            "outputSchema": object_output,
            "annotations": exec_annotations,
        },
        {
            "name": "diagnostics_get",
            "title": "Run Named Diagnostic Profile",
            "description": "Run a named diagnostic/check profile from harness/aci-policy.json and return bounded structured output.",
            "inputSchema": {
                "type": "object",
                "properties": {"profile": {"type": "string", "default": "diagnostics.harness"}},
                "additionalProperties": False,
            },
            "outputSchema": object_output,
            "annotations": exec_annotations,
        },
    ]
    return sorted(defs, key=lambda item: item["name"])
