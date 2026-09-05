#!/usr/bin/env python3
"""Record bounded local model outcomes so routing can learn from real harness work."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harnesslib import ROOT, write_json_atomic

HISTORY_DIR = ROOT / ".harness" / "model-history"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"schema_version": 1, "models": {}}


def record(provider: str, model_id: str, passed: bool, quality: float | None = None) -> dict[str, Any]:
    path = HISTORY_DIR / f"{provider}.json"
    payload = _load(path)
    models = payload.setdefault("models", {})
    row = models.setdefault(model_id, {"samples": 0, "passed": 0, "failed": 0, "quality_sum": 0.0, "quality_samples": 0})
    row["samples"] = int(row.get("samples", 0)) + 1
    if passed:
        row["passed"] = int(row.get("passed", 0)) + 1
    else:
        row["failed"] = int(row.get("failed", 0)) + 1
    if quality is not None:
        quality = max(0.0, min(5.0, float(quality)))
        row["quality_sum"] = float(row.get("quality_sum", 0.0)) + quality
        row["quality_samples"] = int(row.get("quality_samples", 0)) + 1

    samples = max(1, int(row["samples"]))
    # Beta(0.5,0.5) smoothed success rate prevents tiny samples from looking certain.
    success_rate = (float(row.get("passed", 0)) + 0.5) / (samples + 1.0)
    q_samples = int(row.get("quality_samples", 0))
    avg_quality = float(row.get("quality_sum", 0.0)) / q_samples if q_samples else 2.5
    row["success_rate"] = round(success_rate, 6)
    row["average_quality"] = round(avg_quality, 6)
    row["score"] = round(5.0 * (0.60 * success_rate + 0.40 * (avg_quality / 5.0)), 6)
    row["updated_at"] = _now()
    payload["updated_at"] = row["updated_at"]
    write_json_atomic(path, payload)
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description="Record one local model outcome for future routing.")
    ap.add_argument("--provider", required=True, choices=["opencode", "codex"])
    ap.add_argument("--model", required=True)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--passed", action="store_true")
    group.add_argument("--failed", action="store_true")
    ap.add_argument("--quality", type=float, help="Optional 0-5 human/eval quality score")
    args = ap.parse_args()
    row = record(args.provider, args.model, args.passed, args.quality)
    print(json.dumps({"provider": args.provider, "model": args.model, "local_evidence": row}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
