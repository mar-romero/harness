#!/usr/bin/env python3
"""One-request worker used by the Node stdio MCP bridge on Windows."""
from __future__ import annotations

import json
import sys

from aci_core import ACIError, call_tool, tool_definitions


def main() -> int:
    request = json.load(sys.stdin)
    if request.get("operation") == "tools/list":
        result = {"tools": tool_definitions()}
    elif request.get("operation") == "tools/call":
        try:
            value = call_tool(request["name"], request.get("arguments") or {})
            result = {
                "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, separators=(",", ":"))}],
                "structuredContent": value,
                "isError": not bool(value.get("ok")),
            }
        except (ACIError, KeyError, TypeError) as exc:
            result = {"error": str(exc)}
    else:
        result = {"error": "unsupported worker operation"}
    json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
