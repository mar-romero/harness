#!/usr/bin/env python
"""Record deterministic harness checks plus optional benchmark metrics.

The recorder is read-only with respect to the project control plane. It writes
only an append-style run record under .harness/evolution/history (or an explicit
output path) for the evolution engine to analyze later.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harnesslib import ROOT, git, write_json_atomic

CONTROL_PATHS = (
    "AGENTS.md", "AI_POLICY.md", "harness", ".agents/roles", ".agents/skills", "scripts"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def snapshot_hash() -> str:
    h = hashlib.sha256()
    files: list[Path] = []
    for rel in CONTROL_PATHS:
        p = ROOT / rel
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files.extend(x for x in p.rglob("*") if x.is_file() and "__pycache__" not in x.parts)
    for p in sorted(files, key=lambda x: x.as_posix()):
        rel = p.relative_to(ROOT).as_posix()
        h.update(rel.encode("utf-8") + b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def run_check(cmd: list[str]) -> dict[str, Any]:
    start = time.monotonic()
    cp = subprocess.run(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    duration = time.monotonic() - start
    return {"returncode": cp.returncode, "duration_seconds": round(duration, 6), "output": cp.stdout[-8000:]}


def load_metrics(path: str | None) -> dict[str, float]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("metrics file must contain a JSON object")
    out: dict[str, float] = {}
    for key, value in data.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"metric {key!r} must be numeric")
        out[str(key)] = float(value)
    return out


def git_commit() -> str | None:
    cp = git("rev-parse", "HEAD", check=False)
    return cp.stdout.strip() if cp.returncode == 0 else None


def main() -> int:
    ap = argparse.ArgumentParser(description="Capture a versioned harness evaluation record without modifying harness files.")
    ap.add_argument("--label", default="manual")
    ap.add_argument("--metrics", help="Optional JSON object of benchmark metrics (task_success_rate, tokens, cost_usd, etc.)")
    ap.add_argument("--output", help="Optional output path")
    args = ap.parse_args()

    created = now_iso()
    safe_stamp = created.replace(":", "").replace("-", "").replace(".", "")
    safe_label = "".join(c if c.isalnum() or c in "._-" else "-" for c in args.label)[:64] or "run"
    run_id = f"{safe_stamp}-{safe_label}"
    payload = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": created,
        "label": args.label,
        "git_commit": git_commit(),
        "snapshot_sha256": snapshot_hash(),
        "deterministic": {
            "unit_tests": run_check([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"]),
            "evals": run_check([sys.executable, "scripts/run_evals.py"]),
        },
        "metrics": load_metrics(args.metrics),
    }
    dest = Path(args.output) if args.output else ROOT / ".harness" / "evolution" / "history" / f"{run_id}.json"
    if not dest.is_absolute():
        dest = ROOT / dest
    write_json_atomic(dest, payload)
    print(json.dumps({"run_id": run_id, "output": str(dest), "deterministic_pass": all(v["returncode"] == 0 for v in payload["deterministic"].values())}, indent=2))
    return 0 if all(v["returncode"] == 0 for v in payload["deterministic"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
