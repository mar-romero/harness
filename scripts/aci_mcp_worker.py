#!/usr/bin/env python
"""Persistent worker used by the Node stdio MCP bridge on Windows."""
from __future__ import annotations

import json
import sys

from aci_core import ACIError, call_tool, tool_definitions


def handle(request: dict) -> dict:
    if request.get("operation") == "tools/list":
        return {"tools": tool_definitions()}
    if request.get("operation") == "tools/call":
        try:
            value = call_tool(request["name"], request.get("arguments") or {})
            return {
                "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, separators=(",", ":"))}],
                "structuredContent": value,
                "isError": not bool(value.get("ok")),
            }
        except (ACIError, KeyError, TypeError) as exc:
            return {"error": str(exc)}
    return {"error": "unsupported worker operation"}


def main() -> int:
    for raw in sys.stdin:
        if not raw.strip():
            continue
        try:
            request = json.loads(raw)
            result = handle(request)
        except (json.JSONDecodeError, TypeError) as exc:
            result = {"error": str(exc)}
        json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))
        sys.stdout.write("\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
