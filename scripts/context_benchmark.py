#!/usr/bin/env python
"""Deterministic, offline measurement of file and symbol context material."""

from __future__ import annotations

import argparse
import copy
import json
import stat
import sys
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

import context_compiler
import context_graph
from symbol_index import index_source


class BenchmarkError(ValueError):
    """A fixture or context-pack contract is invalid."""


class BenchmarkRegressionError(BenchmarkError):
    """Required context was omitted or a repeated measurement changed."""

    def __init__(self, message: str, report: dict):
        super().__init__(message)
        self.report = report


def canonical_bytes(value: object) -> bytes:
    """Return the comparison representation used for deterministic reports."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _safe_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise BenchmarkError("fixture paths must be nonempty strings")
    if "\\" in value or ":" in value or value.startswith("/"):
        raise BenchmarkError(f"fixture path is not relative POSIX: {value!r}")
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise BenchmarkError(f"fixture path is unsafe: {value!r}")
    if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
        raise BenchmarkError(f"fixture path has control characters: {value!r}")
    return path.as_posix()


def _is_link_or_reparse(path: Path) -> bool:
    """Treat Windows reparse points like links before resolving benchmark roots."""
    metadata = path.lstat()
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(metadata, "st_file_attributes", 0) or 0
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse)


def _strict_descendant(path: Path, parent: Path, label: str) -> None:
    try:
        relative = path.relative_to(parent)
    except ValueError as exc:
        raise BenchmarkError(f"resolved {label} must be a strict descendant of fixture directory") from exc
    if not relative.parts:
        raise BenchmarkError(f"resolved {label} must be a strict descendant of fixture directory")


def _admitted_directory(path: Path, label: str, parent: Path | None = None) -> Path:
    try:
        if _is_link_or_reparse(path):
            raise BenchmarkError(f"{label} must not be a symlink or reparse point")
        resolved = path.resolve(strict=True)
    except BenchmarkError:
        raise
    except OSError as exc:
        raise BenchmarkError(f"{label} does not exist or cannot be resolved") from exc
    if not resolved.is_dir():
        raise BenchmarkError(f"{label} must be a directory")
    if parent is not None:
        _strict_descendant(resolved, parent, label)
    return resolved


def _admitted_file(path: Path, label: str, parent: Path) -> Path:
    try:
        if _is_link_or_reparse(path):
            raise BenchmarkError(f"{label} must not be a symlink or reparse point")
        resolved = path.resolve(strict=True)
    except BenchmarkError:
        raise
    except OSError as exc:
        raise BenchmarkError(f"{label} does not exist or cannot be resolved") from exc
    if not resolved.is_file():
        raise BenchmarkError(f"{label} must be a regular file")
    _strict_descendant(resolved, parent, label)
    return resolved


def _fixture_file(root: Path, value: str) -> Path:
    path = root / value
    try:
        current = root
        for component in PurePosixPath(value).parts:
            current = current / component
            if _is_link_or_reparse(current):
                raise BenchmarkError(f"fixture path is a symlink or reparse point: {value}")
        resolved = path.resolve(strict=True)
        resolved.relative_to(root.resolve())
    except BenchmarkError:
        raise
    except (OSError, ValueError) as exc:
        raise BenchmarkError(f"fixture path is missing or escapes repo: {value}") from exc
    if not path.is_file() or path.is_symlink():
        raise BenchmarkError(f"fixture path is not an admitted regular file: {value}")
    return path


def _validate_declared_symbols(repo: Path, required_symbols: dict[str, list[str]]) -> None:
    for path, symbols in required_symbols.items():
        if not path.endswith(".py"):
            raise BenchmarkError(f"required symbols must refer to Python files: {path}")
        source_path = _fixture_file(repo, path)
        try:
            available = {record["name"] for record in index_source(source_path.read_text(encoding="utf-8"))}
        except OSError as exc:
            raise BenchmarkError(f"required symbol source cannot be read: {path}") from exc
        for symbol in symbols:
            if symbol not in available:
                raise BenchmarkError(f"required symbol not found in fixture source: {path}:{symbol}")


def _required_paths(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        raise BenchmarkError("required_paths must be a nonempty list")
    paths = [_safe_relative_path(item) for item in value]
    if len(paths) != len(set(paths)):
        raise BenchmarkError("required_paths must not contain duplicates")
    return sorted(paths)


def _required_symbols(value: object) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        raise BenchmarkError("required_symbols must be an object")
    result = {}
    for raw_path, raw_symbols in value.items():
        path = _safe_relative_path(raw_path)
        if not isinstance(raw_symbols, list) or not raw_symbols:
            raise BenchmarkError(f"required_symbols[{path!r}] must be a nonempty list")
        if any(not isinstance(symbol, str) or not symbol for symbol in raw_symbols):
            raise BenchmarkError(f"required_symbols[{path!r}] has an invalid symbol")
        symbols = sorted(set(raw_symbols))
        if len(symbols) != len(raw_symbols):
            raise BenchmarkError(f"required_symbols[{path!r}] must not contain duplicates")
        result[path] = symbols
    return dict(sorted(result.items()))


def _load_fixture(fixture: Path) -> tuple[Path, dict, dict, list[str], dict[str, list[str]], dict]:
    fixture = _admitted_directory(Path(fixture), "fixture directory")
    case_path = _admitted_file(fixture / "case.json", "fixture case.json", fixture)
    try:
        case = json.loads(case_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkError("fixture case.json is unreadable or invalid JSON") from exc
    if not isinstance(case, dict):
        raise BenchmarkError("fixture case.json must be an object")
    task = case.get("task")
    policy = case.get("policy")
    if not isinstance(task, dict) or not isinstance(policy, dict):
        raise BenchmarkError("fixture requires task and policy objects")
    task = copy.deepcopy(task)
    policy = copy.deepcopy(policy)
    files = task.get("files")
    if not isinstance(files, list) or not files:
        raise BenchmarkError("fixture task.files must be a nonempty list")
    task["files"] = [_safe_relative_path(item) for item in files]
    if len(task["files"]) != len(set(task["files"])):
        raise BenchmarkError("fixture task.files must not contain duplicates")

    required_paths = _required_paths(case.get("required_paths"))
    required_symbols = _required_symbols(case.get("required_symbols", {}))
    repo = _admitted_directory(fixture / "repo", "fixture repo directory", fixture)
    for path in sorted(set(task["files"]) | set(required_paths) | set(required_symbols)):
        _fixture_file(repo, path)
    if not set(task["files"]).issubset(required_paths):
        raise BenchmarkError("required_paths must include every explicit task file")
    if policy.get("graph_backend") != "lexical":
        raise BenchmarkError("fixture policy must force the lexical graph backend")
    if policy.get("max_memory_items") != 0:
        raise BenchmarkError("fixture policy must disable memory retrieval")
    always_include = policy.get("always_include")
    if not isinstance(always_include, list):
        raise BenchmarkError("fixture policy always_include must be a list")
    always_include = [_safe_relative_path(item) for item in always_include]
    if not set(always_include).issubset(required_paths):
        raise BenchmarkError("required_paths must audit every policy always_include file")
    with _fixture_environment(repo, policy):
        validated_policy = context_compiler.context_policy()
    for path in task["files"]:
        source_path = _fixture_file(repo, path)
        try:
            source_size = source_path.stat(follow_symlinks=False).st_size
        except OSError as exc:
            raise BenchmarkError(f"explicit task file cannot be statted: {path}") from exc
        if source_size > validated_policy["max_file_bytes"]:
            raise BenchmarkError(f"explicit task file exceeds policy max_file_bytes: {path}")
    for path in sorted(set(task["files"]) | set(required_paths) | set(required_symbols)):
        if context_compiler.excluded(PurePosixPath(path), validated_policy):
            raise BenchmarkError(f"fixture path is excluded by policy: {path}")
    _validate_declared_symbols(repo, required_symbols)
    return repo, task, policy, required_paths, required_symbols, case


@contextmanager
def _fixture_environment(repo: Path, policy: dict):
    """Run the existing compiler against only a fixed, lexical fixture root."""
    original = {
        "compiler_root": context_compiler.ROOT,
        "graph_root": context_graph.ROOT,
        "compiler_load_json": context_compiler.load_json,
        "graph_load_json": context_graph.load_json,
        "search_memory": context_compiler.search_memory,
    }

    def fixture_policy(_: str) -> dict:
        return copy.deepcopy(policy)

    def prohibited_memory(*_args, **_kwargs):
        raise BenchmarkError("benchmark fixture unexpectedly invoked memory retrieval")

    context_compiler.ROOT = repo
    context_graph.ROOT = repo
    context_compiler.load_json = fixture_policy
    context_graph.load_json = fixture_policy
    context_compiler.search_memory = prohibited_memory
    try:
        yield
    finally:
        context_compiler.ROOT = original["compiler_root"]
        context_graph.ROOT = original["graph_root"]
        context_compiler.load_json = original["compiler_load_json"]
        context_graph.load_json = original["graph_load_json"]
        context_compiler.search_memory = original["search_memory"]


def _compile_fixture(repo: Path, task: dict, policy: dict) -> dict:
    with _fixture_environment(repo, policy):
        pack = context_compiler.build(task)
    backend = pack.get("graph_backend")
    if backend != {"requested": "lexical", "selected": "lexical", "fallback_reason": None}:
        raise BenchmarkError("benchmark fixture did not use the lexical graph backend")
    return pack


def _token_count(record: object, label: str) -> int:
    if not isinstance(record, dict):
        raise BenchmarkError(f"{label} record must be an object")
    value = record.get("estimated_tokens")
    if type(value) is not int or value <= 0:
        raise BenchmarkError(f"{label} estimated_tokens must be a positive integer")
    return value


def _rollout_recommendation() -> dict:
    return {
        "automatic_enable": False,
        "decision": "keep-file-level-default",
        "reason": "A benchmark report is evidence only; human approval is required before delivery changes.",
    }


def measure_context_pack(
    pack: dict, *, required_paths: list[str], required_symbols: dict[str, list[str]]
) -> dict:
    """Measure effective delivery, retaining complete files unless safe replacement proves coverage."""
    if not isinstance(pack, dict) or not isinstance(pack.get("files"), list):
        raise BenchmarkError("context pack must contain a files list")
    files: dict[str, dict] = {}
    for record in pack["files"]:
        if not isinstance(record, dict):
            raise BenchmarkError("context pack file record must be an object")
        path = _safe_relative_path(record.get("path"))
        if path in files:
            raise BenchmarkError(f"context pack has duplicate file record: {path}")
        _token_count(record, f"file {path}")
        files[path] = record
    if not files:
        raise BenchmarkError("context pack baseline has no files")

    snippets_by_path: dict[str, list[dict]] = {path: [] for path in files}
    fallback_count = 0
    for record in pack.get("snippets", []):
        if not isinstance(record, dict):
            raise BenchmarkError("context pack snippet record must be an object")
        path = _safe_relative_path(record.get("path"))
        if path not in files:
            raise BenchmarkError(f"snippet has no complete-file fallback: {path}")
        symbol = record.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise BenchmarkError(f"snippet symbol is invalid for {path}")
        if type(record.get("fallback")) is not bool:
            raise BenchmarkError(f"snippet fallback flag is invalid for {path}:{symbol}")
        _token_count(record, f"snippet {path}:{symbol}")
        snippets_by_path[path].append(record)
        fallback_count += int(record["fallback"])

    baseline_tokens = sum(_token_count(record, f"file {path}") for path, record in files.items())
    if not baseline_tokens:
        raise BenchmarkError("context pack baseline token total must be nonzero")
    candidate_tokens = 0
    complete_paths: list[str] = []
    replaced_paths: list[str] = []
    selected_symbols: list[dict] = []
    for path in sorted(files):
        snippets = snippets_by_path[path]
        required = required_symbols.get(path, [])
        non_fallback = [record for record in snippets if not record["fallback"]]
        covered = {record["symbol"] for record in non_fallback}
        selected = [record for record in non_fallback if record["symbol"] in required]
        replaceable = (
            path.endswith(".py")
            and bool(required)
            and not any(record["fallback"] for record in snippets)
            and set(required).issubset(covered)
        )
        if replaceable:
            replaced_paths.append(path)
            # The candidate delivers the declared relevant surface, not every
            # additive snippet emitted by the current compiler. This avoids
            # treating overlapping class and method snippets as required output.
            candidate_tokens += sum(_token_count(record, f"snippet {path}") for record in selected)
            selected_symbols.extend(
                {"path": path, "symbol": record["symbol"], "start_line": record.get("start_line")}
                for record in selected
            )
        else:
            complete_paths.append(path)
            candidate_tokens += _token_count(files[path], f"file {path}")

    omissions: list[str] = []
    for path in required_paths:
        if path not in files:
            omissions.append(f"path:{path}")
    delivered_symbols = {(record["path"], record["symbol"]) for record in selected_symbols}
    for path, symbols in required_symbols.items():
        for symbol in symbols:
            if path not in files:
                omissions.append(f"symbol:{path}:{symbol}")
            elif (path, symbol) not in delivered_symbols:
                omissions.append(f"symbol:{path}:{symbol}")
    required_surface = len(required_paths) + sum(len(symbols) for symbols in required_symbols.values())
    recall = (required_surface - len(omissions)) / required_surface
    report = {
        "baseline_tokens": baseline_tokens,
        "candidate_tokens": candidate_tokens,
        "token_reduction_percent": round((baseline_tokens - candidate_tokens) * 100 / baseline_tokens, 2),
        "selected_files": {
            "baseline": sorted(files),
            "candidate_complete": complete_paths,
            "candidate_replaced_by_snippets": replaced_paths,
        },
        "selected_symbols": sorted(
            selected_symbols, key=lambda item: (item["path"], item["symbol"], item["start_line"] or 0)
        ),
        "fallback_count": fallback_count,
        "relevant_surface_recall": round(recall, 6),
        "omissions": sorted(omissions),
        "determinism": {"runs": 1, "status": "NOT_RUN"},
        "rollout_recommendation": _rollout_recommendation(),
    }
    return report


def _require_safe_report(report: dict) -> None:
    if report["omissions"]:
        raise BenchmarkRegressionError(
            "benchmark omitted required context: " + ", ".join(report["omissions"]), report
        )


def run_fixture(fixture: Path) -> dict:
    """Run the same fixed fixture twice and fail closed on omitted surface or variance."""
    repo, task, policy, required_paths, required_symbols, _case = _load_fixture(fixture)
    first = measure_context_pack(
        _compile_fixture(repo, task, policy),
        required_paths=required_paths,
        required_symbols=required_symbols,
    )
    second = measure_context_pack(
        _compile_fixture(repo, task, policy),
        required_paths=required_paths,
        required_symbols=required_symbols,
    )
    if canonical_bytes(first) != canonical_bytes(second):
        first["determinism"] = {"runs": 2, "status": "FAIL"}
        raise BenchmarkRegressionError("benchmark report is not byte-stable across repeated runs", first)
    first["determinism"] = {"runs": 2, "status": "PASS"}
    _require_safe_report(first)
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = run_fixture(args.fixture)
    except BenchmarkRegressionError as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc), "report": exc.report}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2
    except BenchmarkError as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
