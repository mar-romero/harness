#!/usr/bin/env python3
"""Generated entrypoint for the project-scoped Harness ACI MCP server."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

if os.environ.get("HARNESS_ACI_DIAGNOSTICS") == "1":
    path = ROOT / ".harness" / "codex" / "aci-mcp-diagnostics.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": "entrypoint_started"}) + "\n")

try:
    from aci_mcp import main
except BaseException as exc:
    if os.environ.get("HARNESS_ACI_DIAGNOSTICS") == "1":
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event": "entrypoint_import_failed", "error": str(exc), "error_type": type(exc).__name__}) + "\n")
    raise

if os.environ.get("HARNESS_ACI_DIAGNOSTICS") == "1":
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": "entrypoint_ready"}) + "\n")

if __name__ == "__main__":
    exit_code = main()
    if os.environ.get("HARNESS_ACI_DIAGNOSTICS") == "1":
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event": "entrypoint_main_returned", "exit_code": exit_code}) + "\n")
    raise SystemExit(exit_code)
