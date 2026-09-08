#!/usr/bin/env python3
"""Inspect or refresh the Codex runtime model inventory."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from openrouter_sync import refresh_provider_inventory  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Discover Codex-visible models and enrich them with OpenRouter.")
    ap.add_argument("--no-endpoints", action="store_true")
    args = ap.parse_args()
    payload, dest = refresh_provider_inventory("codex", fetch_endpoints=not args.no_endpoints)
    print(json.dumps({
        "provider": "codex",
        "models": len(payload.get("models", [])),
        "output": str(dest.relative_to(ROOT)),
        "openrouter_fetch_error": payload.get("openrouter_fetch_error"),
        "rows": payload.get("models", []),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
