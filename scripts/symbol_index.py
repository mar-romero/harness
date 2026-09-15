"""Deterministic, fail-closed Python symbol indexing."""
from __future__ import annotations

import ast


def _decorated_start(node: ast.AST) -> int:
    decorators = getattr(node, "decorator_list", ())
    return min([getattr(d, "lineno", node.lineno) for d in decorators] + [node.lineno])


def index_source(source: str) -> list[dict]:
    """Return stable symbol records; syntax errors produce an empty index."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, TypeError):
        return []
    records: list[dict] = []

    def visit(body: list[ast.stmt], parents: tuple[str, ...]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = ".".join((*parents, node.name))
                kind = "method" if parents and any(
                    isinstance(item, ast.ClassDef) for item in []
                ) else "function"
                # The parent tuple encodes class names with a sentinel prefix.
                kind = "method" if parents and parents[-1].startswith("class:") else kind
                display = ".".join(p[6:] if p.startswith("class:") else p for p in (*parents, node.name))
                end = getattr(node, "end_lineno", node.lineno)
                records.append({"kind": kind, "name": display,
                                "start_line": _decorated_start(node), "end_line": end})
                visit(node.body, (*parents, node.name))
            elif isinstance(node, ast.ClassDef):
                display = ".".join(p[6:] if p.startswith("class:") else p for p in (*parents, node.name))
                records.append({"kind": "class", "name": display,
                                "start_line": _decorated_start(node),
                                "end_line": getattr(node, "end_lineno", node.lineno)})
                visit(node.body, (*parents, "class:" + node.name))
            elif isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith)):
                for field in ("body", "orelse", "finalbody"):
                    child = getattr(node, field, None)
                    if child:
                        visit(child, parents)
                for handler in getattr(node, "handlers", ()):
                    visit(handler.body, parents)

    visit(tree.body, ())
    return records


def index_file(path) -> list[dict]:
    try:
        return index_source(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return []
