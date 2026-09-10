#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from aci_core import call_tool, tool_definitions


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Portable Harness Agent-Computer Interface")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list-tools")
    call = sub.add_parser("call")
    call.add_argument("tool")
    call.add_argument("--arguments", default="{}", help="JSON object")
    ns = parser.parse_args()
    if ns.command == "list-tools":
        emit({"tools": tool_definitions()})
        return 0
    try:
        args = json.loads(ns.arguments)
    except json.JSONDecodeError as exc:
        emit({"ok": False, "tool": ns.tool, "data": None, "error": f"invalid JSON arguments: {exc}", "meta": {}})
        return 2
    result = call_tool(ns.tool, args)
    emit(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
