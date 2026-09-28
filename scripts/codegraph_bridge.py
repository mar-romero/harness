#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from harnesslib import ROOT

CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt",
    ".cs", ".rb", ".php", ".swift", ".c", ".h", ".cc", ".cpp", ".hpp",
}

_PATH_RE = re.compile(
    r"(?P<path>(?:[A-Za-z0-9_.@+-]+/)+[A-Za-z0-9_.@+()\[\]-]+"
    r"\.(?:py|js|ts|tsx|jsx|go|rs|java|kt|cs|rb|php|swift|c|h|cc|cpp|hpp))"
    r"(?::\d+(?:-\d+)?)?"
)


def _binary() -> str | None:
    return shutil.which("codegraph")


def _command_prefix() -> list[str] | None:
    """Return a shell-free CodeGraph command prefix, including npm Windows shims."""
    binary = _binary()
    if not binary:
        return None
    path = Path(binary)
    if sys.platform != "win32" or path.suffix.lower() not in {".cmd", ".bat"}:
        return [binary]
    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        return None
    pkg = path.parent / "node_modules" / "@colbymchenry" / "codegraph" / "package.json"
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
        bin_value = data.get("bin")
        target = bin_value if isinstance(bin_value, str) else (bin_value or {}).get("codegraph")
        if not isinstance(target, str) or not target:
            return None
        script = (pkg.parent / target).resolve(strict=True)
        if not script.is_file():
            return None
        return [node, str(script)]
    except (OSError, ValueError, json.JSONDecodeError):
        return None

def _safe_index_dir() -> tuple[bool, str | None]:
    index = ROOT / ".codegraph"
    if not index.exists():
        return False, "index-missing"
    if index.is_symlink() or not index.is_dir():
        return False, "unsafe-index-path"
    return True, None


def _read_bounded(stream, max_chars: int, overflow: threading.Event) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(min(4096, max_chars + 1 - total))
        if not chunk:
            return b''.join(chunks)
        if total < max_chars:
            chunks.append(chunk[:max_chars - total])
        total += len(chunk)
        if total > max_chars:
            overflow.set()
            return b''.join(chunks)


def _run(args: list[str], *, timeout: int = 45, max_chars: int = 32000) -> dict[str, Any]:
    prefix = _command_prefix()
    if not prefix:
        return {
            "ok": False,
            "status": "unavailable",
            "reason": "codegraph binary not found on PATH",
            "argv": args,
        }
    env = os.environ.copy()
    env.setdefault("PATH", os.defpath)
    limit = max(1, int(max_chars))
    try:
        process = subprocess.Popen(
            [*prefix, *args],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
    except OSError as exc:
        return {
            "ok": False,
            "status": "failed",
            "reason": str(exc),
            "argv": args,
        }

    overflow = threading.Event()
    captured: dict[str, bytes] = {}
    readers = []
    for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
        reader = threading.Thread(
            target=lambda key=name, source=stream: captured.__setitem__(
                key, _read_bounded(source, limit, overflow)
            ),
            daemon=True,
        )
        reader.start()
        readers.append(reader)

    deadline = time.monotonic() + max(1, int(timeout))
    timed_out = False
    while process.poll() is None:
        if overflow.is_set():
            process.kill()
            break
        if time.monotonic() >= deadline:
            process.kill()
            timed_out = True
            break
        time.sleep(0.01)
    returncode = process.wait()
    for reader in readers:
        reader.join(timeout=1)

    stdout = captured.get("stdout", b"").decode("utf-8", errors="replace")
    stderr = captured.get("stderr", b"").decode("utf-8", errors="replace")
    if timed_out:
        return {
            "ok": False,
            "status": "failed",
            "reason": "process timeout",
            "stdout": stdout,
            "stderr": stderr,
            "truncated": False,
            "argv": args,
        }
    if overflow.is_set():
        return {
            "ok": False,
            "status": "failed",
            "reason": "process output limit exceeded",
            "stdout": stdout,
            "stderr": stderr,
            "truncated": True,
            "argv": args,
        }
    return {
        "ok": returncode == 0,
        "status": "ready" if returncode == 0 else "failed",
        "exit_code": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": False,
        "argv": args,
    }


def status() -> dict[str, Any]:
    binary = _binary()
    indexed, index_reason = _safe_index_dir()
    out: dict[str, Any] = {
        "available": bool(binary),
        "binary": binary,
        "indexed": indexed,
        "root": str(ROOT),
        "status": "ready" if binary and indexed else ("unindexed" if binary else "unavailable"),
    }
    if index_reason:
        out["reason"] = index_reason
    if binary and indexed:
        probe = _run(["status", "--json"], timeout=20, max_chars=12000)
        out["probe_ok"] = probe.get("ok", False)
        if probe.get("ok"):
            try:
                out["details"] = json.loads(str(probe.get("stdout") or "{}"))
            except json.JSONDecodeError:
                out["details"] = {"raw": probe.get("stdout", "")}
        else:
            out["status"] = "failed"
            out["reason"] = probe.get("stderr") or probe.get("reason") or "codegraph status failed"
    return out


def init_index() -> dict[str, Any]:
    binary = _binary()
    if not binary or not _command_prefix():
        return {
            "ok": False,
            "status": "unavailable",
            "reason": "Install CodeGraph (and Node for npm Windows installs) first; the harness never downloads third-party executables silently.",
        }
    indexed, reason = _safe_index_dir()
    if indexed:
        return {"ok": True, "status": "already-indexed", "root": str(ROOT)}
    if reason == "unsafe-index-path":
        return {"ok": False, "status": "unsafe-index-path", "reason": ".codegraph must be a real directory"}
    run = _run(["init", str(ROOT)], timeout=180, max_chars=40000)
    return {**run, "root": str(ROOT)}


def _extract_paths(text: str, limit: int = 20) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in _PATH_RE.finditer(text):
        rel = match.group("path").replace("\\", "/")
        if rel in seen:
            continue
        candidate = (ROOT / rel).resolve(strict=False)
        try:
            candidate.relative_to(ROOT.resolve())
        except ValueError:
            continue
        if candidate.is_file() and candidate.suffix.lower() in CODE_EXTENSIONS:
            seen.add(rel)
            found.append(rel)
            if len(found) >= limit:
                break
    return found


def explore(query: str, *, max_files: int = 8, max_chars: int = 28000) -> dict[str, Any]:
    query = (query or "").strip()
    if not query:
        return {"ok": False, "status": "invalid", "reason": "query must be non-empty"}
    s = status()
    if s.get("status") != "ready":
        return {
            "ok": False,
            "status": s.get("status", "unavailable"),
            "reason": s.get("reason") or "CodeGraph is not ready",
            "codegraph": s,
        }
    limit = min(max(1, int(max_files)), 20)
    # --path/--max-files are supported by the CLI surface used by agent integrations.
    run = _run(
        ["explore", "--path", str(ROOT), "--max-files", str(limit), "--", query],
        timeout=60,
        max_chars=max_chars,
    )
    text = str(run.get("stdout") or "")
    return {
        **run,
        "query": query,
        "paths": _extract_paths(text, limit=limit),
        "text": text,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Safe optional bridge to the external CodeGraph CLI.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("init")
    p = sub.add_parser("explore")
    p.add_argument("query")
    p.add_argument("--max-files", type=int, default=8)
    p.add_argument("--max-chars", type=int, default=28000)
    args = parser.parse_args()
    if args.command == "status":
        out = status()
    elif args.command == "init":
        out = init_index()
    else:
        out = explore(args.query, max_files=args.max_files, max_chars=args.max_chars)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok", out.get("status") == "ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
