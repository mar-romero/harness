"""Bounded Python snippet extraction with complete-source fallback."""
from __future__ import annotations

import ast
import hashlib
try:
    from .symbol_index import index_source
except ImportError:  # provider scripts are also imported as top-level modules
    from symbol_index import index_source


def _line_slice(source: str, start: int, end: int) -> str:
    lines = source.splitlines(keepends=True)
    return "".join(lines[start - 1:end])


def _module_imports(tree: ast.AST) -> list[ast.AST]:
    """Collect imports in statement-bearing control-flow blocks."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            found.append(node)
    return found


def extract_snippet(source: str, symbol: str, max_chars: int | None = None,
                    occurrence_start: int | None = None) -> dict:
    fallback = {"symbol": symbol, "text": source, "start_line": 1,
                "end_line": max(1, len(source.splitlines())), "fallback": True,
                "reason": "fallback", "sha256": hashlib.sha256(source.encode()).hexdigest()}
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, TypeError):
        return fallback
    target = next((item for item in index_source(source)
                   if item["name"] == symbol and
                   (occurrence_start is None or item["start_line"] == occurrence_start)), None)
    if not target:
        return fallback
    # Include module imports and all decorators/class ancestors to preserve context.
    start = target["start_line"]
    end = target["end_line"]
    lines = source.splitlines(keepends=True)
    imports = _module_imports(tree)
    # Imports nested in control flow cannot be represented safely by a simple
    # contiguous prefix around a symbol; retain the complete admitted source.
    if any(node not in tree.body for node in imports):
        return fallback
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            start = min(start, node.lineno)
        elif isinstance(node, ast.ClassDef) and symbol.startswith(node.name + "."):
            start = min(start, min([getattr(d, "lineno", node.lineno) for d in node.decorator_list] + [node.lineno]))
    # An import after the selected definition cannot be included in a partial
    # prefix without also including intervening statements; use safe fallback.
    if any(node.lineno > end for node in imports):
        return fallback
    text = _line_slice(source, start, end)
    if max_chars is not None and len(text) > max_chars:
        return fallback
    try:
        compile(text, "<context-snippet>", "exec")
    except SyntaxError:
        # Never emit a misleading partial tree; callers retain the bounded
        # complete-file record as the safe fallback.
        return fallback
    return {"symbol": symbol, "text": text, "start_line": start, "end_line": end,
            "fallback": False, "reason": "symbol", "sha256": hashlib.sha256(text.encode()).hexdigest()}
