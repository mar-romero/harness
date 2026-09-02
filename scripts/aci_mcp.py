#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from typing import Any

from aci_core import ACIError, call_tool, tool_definitions

MODERN_VERSION = "2026-07-28"
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18")
SERVER_INFO = {"name": "portable-harness-aci", "version": "1.0.0"}
SERVER_INFO_META_KEY = "io.modelcontextprotocol/serverInfo"
PROTOCOL_META_KEY = "io.modelcontextprotocol/protocolVersion"

legacy_initialized = False


def _server_meta() -> dict[str, Any]:
    return {SERVER_INFO_META_KEY: SERVER_INFO}


def _response(req_id: Any, result: dict[str, Any], *, modern: bool = False) -> dict[str, Any]:
    payload = dict(result)
    if modern:
        payload.setdefault("resultType", "complete")
        meta = dict(payload.get("_meta") or {})
        meta.update(_server_meta())
        payload["_meta"] = meta
    return {"jsonrpc": "2.0", "id": req_id, "result": payload}


def _error(req_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def _modern_request(message: dict[str, Any]) -> bool:
    params = message.get("params")
    if not isinstance(params, dict):
        return False
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return False
    return meta.get(PROTOCOL_META_KEY) == MODERN_VERSION


def _handle(message: dict[str, Any]) -> dict[str, Any] | None:
    global legacy_initialized
    method = message.get("method")
    req_id = message.get("id")
    params = message.get("params") or {}

    if method == "notifications/initialized":
        return None

    if method == "server/discover":
        return _response(req_id, {
            "supportedVersions": [MODERN_VERSION, *LEGACY_VERSIONS],
            "capabilities": {"tools": {"listChanged": False}},
            "instructions": "Use these bounded repository/git/check tools before generic shell commands. Source writes remain provider-native and gated.",
            "ttlMs": 300000,
            "cacheScope": "private",
        }, modern=True)

    if method == "initialize":
        legacy_initialized = True
        requested = params.get("protocolVersion")
        selected = requested if requested in LEGACY_VERSIONS else LEGACY_VERSIONS[0]
        return _response(req_id, {
            "protocolVersion": selected,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": "Use Harness ACI tools before raw shell for matching repository inspection/check operations.",
        })

    modern = _modern_request(message) and not legacy_initialized

    if method == "ping":
        return _response(req_id, {}, modern=modern)

    if method == "tools/list":
        result: dict[str, Any] = {"tools": tool_definitions()}
        if modern:
            result.update({"ttlMs": 300000, "cacheScope": "private"})
        return _response(req_id, result, modern=modern)

    if method == "tools/call":
        if not isinstance(params, dict):
            return _error(req_id, -32602, "Invalid params")
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not name:
            return _error(req_id, -32602, "Tool name must be a non-empty string")
        try:
            result = call_tool(name, arguments)
        except ACIError as exc:
            return _error(req_id, -32602, str(exc))
        text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        tool_result: dict[str, Any] = {
            "content": [{"type": "text", "text": text}],
            "structuredContent": result,
            "isError": not bool(result.get("ok")),
        }
        return _response(req_id, tool_result, modern=modern)

    # Notifications with no id do not receive a response.
    if "id" not in message:
        return None
    return _error(req_id, -32601, f"Method not found: {method}")


def main() -> int:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                raise ValueError("expected JSON-RPC 2.0 object")
            response = _handle(message)
        except json.JSONDecodeError as exc:
            response = _error(None, -32700, f"Parse error: {exc}")
        except Exception as exc:  # fail closed at the protocol boundary
            print(f"harness-aci internal error: {exc}", file=sys.stderr)
            response = _error(None, -32603, "Internal error")
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
