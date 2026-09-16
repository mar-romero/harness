#!/usr/bin/env python3
from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from harnesslib import ROOT

CODE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".cs", ".rb", ".php", ".swift", ".c", ".h", ".cc", ".cpp", ".hpp"}

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")

# These patterns intentionally target declarations only. They are a portable
# fallback; CodeGraph/tree-sitter remains the preferred semantic backend.
DECL_PATTERNS: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    ".js": [
        ("class", re.compile(r"^\s*(?:export\s+(?:default\s+)?)?class\s+([A-Za-z_$][\w$]*)")),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)")),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>")),
    ],
    ".ts": [
        ("class", re.compile(r"^\s*(?:export\s+(?:default\s+)?)?class\s+([A-Za-z_$][\w$]*)")),
        ("interface", re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)")),
        ("type", re.compile(r"^\s*(?:export\s+)?type\s+([A-Za-z_$][\w$]*)")),
        ("enum", re.compile(r"^\s*(?:export\s+)?enum\s+([A-Za-z_$][\w$]*)")),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)")),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>")),
    ],
    ".go": [
        ("function", re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_][\w]*)\s*\(")),
        ("type", re.compile(r"^\s*type\s+([A-Za-z_][\w]*)\s+(?:struct|interface|\w+)")),
    ],
    ".rs": [
        ("function", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_][\w]*)\s*\(")),
        ("struct", re.compile(r"^\s*(?:pub\s+)?struct\s+([A-Za-z_][\w]*)")),
        ("enum", re.compile(r"^\s*(?:pub\s+)?enum\s+([A-Za-z_][\w]*)")),
        ("trait", re.compile(r"^\s*(?:pub\s+)?trait\s+([A-Za-z_][\w]*)")),
    ],
    ".java": [
        ("type", re.compile(r"^\s*(?:public\s+|protected\s+|private\s+|abstract\s+|final\s+|static\s+)*(?:class|interface|enum|record)\s+([A-Za-z_][\w]*)")),
    ],
    ".kt": [
        ("type", re.compile(r"^\s*(?:public\s+|private\s+|protected\s+|internal\s+|data\s+|sealed\s+|open\s+)*(?:class|interface|object|enum\s+class)\s+([A-Za-z_][\w]*)")),
        ("function", re.compile(r"^\s*(?:public\s+|private\s+|protected\s+|internal\s+|suspend\s+|inline\s+)*fun\s+([A-Za-z_][\w]*)\s*\(")),
    ],
    ".cs": [
        ("type", re.compile(r"^\s*(?:public\s+|private\s+|protected\s+|internal\s+|abstract\s+|sealed\s+|static\s+|partial\s+)*(?:class|interface|struct|record|enum)\s+([A-Za-z_][\w]*)")),
    ],
    ".rb": [
        ("class", re.compile(r"^\s*class\s+([A-Za-z_][\w:]*)")),
        ("module", re.compile(r"^\s*module\s+([A-Za-z_][\w:]*)")),
        ("function", re.compile(r"^\s*def\s+(?:self\.)?([A-Za-z_][\w!?=]*)")),
    ],
    ".php": [
        ("type", re.compile(r"^\s*(?:abstract\s+|final\s+)?(?:class|interface|trait|enum)\s+([A-Za-z_][\w]*)", re.I)),
        ("function", re.compile(r"^\s*(?:public\s+|private\s+|protected\s+|static\s+|final\s+|abstract\s+)*function\s+([A-Za-z_][\w]*)\s*\(", re.I)),
    ],
    ".swift": [
        ("type", re.compile(r"^\s*(?:public\s+|internal\s+|private\s+|fileprivate\s+|open\s+|final\s+)*(?:class|struct|enum|protocol|actor)\s+([A-Za-z_][\w]*)")),
        ("function", re.compile(r"^\s*(?:public\s+|internal\s+|private\s+|fileprivate\s+|open\s+|static\s+|class\s+|mutating\s+)*(?:async\s+)?func\s+([A-Za-z_][\w]*)\s*\(")),
    ],
    ".c": [
        ("type", re.compile(r"^\s*(?:typedef\s+)?(?:struct|enum|union)\s+([A-Za-z_][\w]*)")),
    ],
    ".cpp": [
        ("type", re.compile(r"^\s*(?:class|struct|enum)\s+([A-Za-z_][\w]*)")),
    ],
    ".cc": [
        ("type", re.compile(r"^\s*(?:class|struct|enum)\s+([A-Za-z_][\w]*)")),
    ],
    ".hpp": [
        ("type", re.compile(r"^\s*(?:class|struct|enum)\s+([A-Za-z_][\w]*)")),
    ],
    ".h": [
        ("type", re.compile(r"^\s*(?:typedef\s+)?(?:struct|enum|union)\s+([A-Za-z_][\w]*)")),
    ],
}
DECL_PATTERNS[".jsx"] = DECL_PATTERNS[".js"]
DECL_PATTERNS[".tsx"] = DECL_PATTERNS[".ts"]


@dataclass(frozen=True)
class Symbol:
    path: str
    name: str
    kind: str
    start_line: int
    end_line: int
    signature: str


def estimate_tokens(text_or_chars: str | int) -> int:
    chars = text_or_chars if isinstance(text_or_chars, int) else len(text_or_chars)
    return max(1, math.ceil(chars / 4))


def query_tokens(text: str) -> set[str]:
    stop = {"this", "that", "with", "from", "into", "para", "como", "esta", "este", "what", "how", "does", "where", "when", "change", "update"}
    return {m.group(0).lower() for m in _TOKEN_RE.finditer(text or "") if m.group(0).lower() not in stop}


def _python_symbols(path: Path, rel: str, text: str) -> list[Symbol]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines()
    out: list[Symbol] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            start = int(getattr(node, "lineno", 1))
            end = int(getattr(node, "end_lineno", start))
            signature = lines[start - 1].strip()[:240] if 0 < start <= len(lines) else node.name
            out.append(Symbol(rel, node.name, kind, start, max(start, end), signature))
    return sorted(out, key=lambda s: (s.start_line, s.name))


def extract_symbols(path: Path) -> list[Symbol]:
    try:
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, ValueError):
        return []
    if path.suffix.lower() == ".py":
        return _python_symbols(path, rel, text)
    patterns = DECL_PATTERNS.get(path.suffix.lower(), [])
    if not patterns:
        return []
    lines = text.splitlines()
    found: list[tuple[int, str, str, str]] = []
    for idx, line in enumerate(lines, 1):
        for kind, pattern in patterns:
            match = pattern.search(line)
            if match:
                found.append((idx, match.group(1), kind, line.strip()[:240]))
                break
    out: list[Symbol] = []
    for pos, (start, name, kind, signature) in enumerate(found):
        next_start = found[pos + 1][0] if pos + 1 < len(found) else len(lines) + 1
        # Portable fallback: never infer giant bodies from regex declarations.
        end = min(len(lines), max(start, min(next_start - 1, start + 79)))
        out.append(Symbol(rel, name, kind, start, end, signature))
    return out


def pagerank(graph: dict[str, list[str]], files: Iterable[str], iterations: int = 20, damping: float = 0.85) -> dict[str, float]:
    nodes = sorted(set(files) | set(graph) | {d for deps in graph.values() for d in deps})
    if not nodes:
        return {}
    allowed = set(nodes)
    outgoing = {n: [d for d in graph.get(n, []) if d in allowed and d != n] for n in nodes}
    n = len(nodes)
    rank = {node: 1.0 / n for node in nodes}
    for _ in range(iterations):
        base = (1.0 - damping) / n
        nxt = {node: base for node in nodes}
        dangling = sum(rank[node] for node, deps in outgoing.items() if not deps)
        dangling_share = damping * dangling / n
        for node in nodes:
            nxt[node] += dangling_share
        for src, deps in outgoing.items():
            if not deps:
                continue
            share = damping * rank[src] / len(deps)
            for dst in deps:
                nxt[dst] += share
        rank = nxt
    return rank


def _symbol_score(symbol: Symbol, *, wanted: set[str], explicit: set[str], file_rank: dict[str, float], selected_scores: dict[str, float]) -> float:
    score = file_rank.get(symbol.path, 0.0) * 1000.0
    score += selected_scores.get(symbol.path, 0.0) * 0.5
    if symbol.path in explicit:
        score += 900.0
    low_name = symbol.name.lower()
    low_path = symbol.path.lower()
    for token in wanted:
        if token == low_name:
            score += 500.0
        elif token in low_name:
            score += 160.0
        if token in low_path:
            score += 30.0
    return score


def ranked_symbols(paths: Iterable[str], query: str, explicit: Iterable[str], graph: dict[str, list[str]], selected_scores: dict[str, float] | None = None) -> list[tuple[float, Symbol]]:
    path_list = [p for p in dict.fromkeys(paths) if (ROOT / p).suffix.lower() in CODE_EXTS]
    wanted = query_tokens(query)
    explicit_set = {Path(p).as_posix() for p in explicit}
    ranks = pagerank(graph, path_list)
    selected_scores = selected_scores or {}
    out: list[tuple[float, Symbol]] = []
    for rel in path_list:
        for symbol in extract_symbols(ROOT / rel):
            score = _symbol_score(symbol, wanted=wanted, explicit=explicit_set, file_rank=ranks, selected_scores=selected_scores)
            out.append((score, symbol))
    out.sort(key=lambda item: (-item[0], item[1].path, item[1].start_line, item[1].name))
    return out


def build_repo_map(paths: Iterable[str], query: str, explicit: Iterable[str], graph: dict[str, list[str]], *, token_budget: int = 1600, selected_scores: dict[str, float] | None = None) -> dict:
    budget_chars = max(400, int(token_budget) * 4)
    grouped: dict[str, list[Symbol]] = {}
    used = 0
    lines: list[str] = []
    for _, symbol in ranked_symbols(paths, query, explicit, graph, selected_scores):
        entry = f"  L{symbol.start_line}-{symbol.end_line} {symbol.kind} {symbol.name}: {symbol.signature}\n"
        header = ""
        if symbol.path not in grouped:
            header = f"{symbol.path}:\n"
        if used + len(header) + len(entry) > budget_chars:
            continue
        if header:
            lines.append(header)
            grouped[symbol.path] = []
            used += len(header)
        lines.append(entry)
        grouped[symbol.path].append(symbol)
        used += len(entry)
    text = "".join(lines).rstrip()
    return {
        "text": text,
        "estimated_tokens": estimate_tokens(text) if text else 0,
        "files": sorted(grouped),
        "symbol_count": sum(len(v) for v in grouped.values()),
    }


def _numbered_slice(lines: list[str], start: int, end: int) -> str:
    return "\n".join(f"{idx:>5} | {lines[idx - 1]}" for idx in range(start, min(end, len(lines)) + 1))


def build_snippets(paths: Iterable[str], query: str, explicit: Iterable[str], graph: dict[str, list[str]], *, token_budget: int = 6500, max_symbols_per_file: int = 3, max_lines_per_symbol: int = 80, selected_scores: dict[str, float] | None = None) -> dict:
    budget_chars = max(1000, int(token_budget) * 4)
    per_file: dict[str, int] = {}
    seen_ranges: set[tuple[str, int, int]] = set()
    snippets: list[dict] = []
    used = 0
    for score, symbol in ranked_symbols(paths, query, explicit, graph, selected_scores):
        if per_file.get(symbol.path, 0) >= max_symbols_per_file:
            continue
        start = max(1, symbol.start_line)
        end = min(symbol.end_line, start + max(1, int(max_lines_per_symbol)) - 1)
        key = (symbol.path, start, end)
        if key in seen_ranges:
            continue
        try:
            source_lines = (ROOT / symbol.path).read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        body = _numbered_slice(source_lines, start, end)
        rendered = f"# {symbol.path}:{start}-{end} — {symbol.kind} {symbol.name}\n{body}\n"
        if used + len(rendered) > budget_chars:
            continue
        token_est = estimate_tokens(rendered)
        snippets.append({
            "path": symbol.path,
            "symbol": symbol.name,
            "kind": symbol.kind,
            "start_line": start,
            "end_line": end,
            "score": round(score, 4),
            "estimated_tokens": token_est,
            "text": body,
        })
        used += len(rendered)
        per_file[symbol.path] = per_file.get(symbol.path, 0) + 1
        seen_ranges.add(key)
    return {
        "snippets": snippets,
        "estimated_tokens": sum(x["estimated_tokens"] for x in snippets),
        "files": sorted({x["path"] for x in snippets}),
    }
