#!/usr/bin/env python3
"""Loss-aware condenser for durable handoffs used as working context.

Full handoff artifacts remain authoritative on disk. This module only creates a
bounded working-memory representation for the next model, avoiding transcript
and handoff accumulation across multi-provider workflows.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harnesslib import ROOT

POLICY_PATH = ROOT / "harness" / "context-condense-policy.json"


def _policy() -> dict[str, Any]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def _compact(value: Any, cfg: dict[str, Any]) -> Any:
    if isinstance(value, str):
        cap = int(cfg.get("max_string_chars", 1400))
        return value if len(value) <= cap else value[:cap].rstrip() + "…"
    if isinstance(value, list):
        cap = int(cfg.get("max_list_items", 14))
        return [_compact(x, cfg) for x in value[:cap]]
    if isinstance(value, dict):
        preserve = [str(x) for x in cfg.get("preserve_keys", [])]
        keys = [k for k in preserve if k in value]
        for k in value:
            if k not in keys:
                keys.append(k)
        keys = keys[: int(cfg.get("max_dict_items", 24))]
        return {k: _compact(value[k], cfg) for k in keys}
    return value


def condense_handoffs(handoffs: dict[str, Any]) -> dict[str, Any]:
    cfg = _policy()
    cap = int(cfg.get("max_prior_handoff_chars", 18000))
    out: dict[str, Any] = {}
    used = 2
    # Preserve workflow order supplied by caller. Stop before exceeding budget;
    # latest stages are normally appended later, so compact each aggressively.
    for name, payload in handoffs.items():
        compacted = _compact(payload, cfg)
        rendered = json.dumps({name: compacted}, ensure_ascii=False, separators=(",", ":"))
        if out and used + len(rendered) > cap:
            # Keep a tiny marker instead of silently pretending the stage did not exist.
            out[name] = {"status": payload.get("status") if isinstance(payload, dict) else None, "condensed_out": True}
            continue
        out[name] = compacted
        used += len(rendered)
    return out


def estimated_tokens(value: Any) -> int:
    return max(1, len(json.dumps(value, ensure_ascii=False, separators=(",", ":"))) // 4)
