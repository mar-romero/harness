import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import openrouter_sync


class UnmatchedModelOverrideTests(unittest.TestCase):
    def _write_provider(self, root: Path, provider: str) -> None:
        path = root / "harness" / "model-providers" / f"{provider}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "provider": provider,
                    "enriched_inventory": f".harness/model-inventories/{provider}.json",
                    "raw_inventory": f".harness/{provider}/model-inventory.json",
                    "openrouter_aliases": {},
                    "openrouter": {"fetch_endpoint_health": False},
                }
            ),
            encoding="utf-8",
        )

    def _scores(self):
        return {
            "schema_version": 1,
            "provider": "openrouter",
            "models": [
                {
                    "id": "openai/gpt-5.6-luna",
                    "openrouter_benchmark_id": "openai/gpt-5.6-luna",
                    "openrouter_benchmark_match": "exact",
                    "capabilities": {
                        "reasoning": 4.1,
                        "coding": 4.4,
                        "tool_use": 4.5,
                        "reliability": 3.5,
                    },
                    "cost": 3.0,
                    "latency": 2.0,
                    "context_window": 272000,
                    "raw_metrics": {},
                    "provenance": {"quality": "benchmark", "pricing": "models"},
                },
                {
                    "id": "openai/gpt-5.4",
                    "openrouter_benchmark_id": "openai/gpt-5.4",
                    "openrouter_benchmark_match": "exact",
                    "capabilities": {
                        "reasoning": 3.8,
                        "coding": 4.0,
                        "tool_use": 4.1,
                        "reliability": 4.0,
                    },
                    "cost": 2.5,
                    "latency": 3.5,
                    "context_window": 200000,
                    "raw_metrics": {},
                    "provenance": {"quality": "benchmark", "pricing": "models"},
                },
            ],
        }

    def _candidate(self, model_id: str, provider: str):
        return {
            "id": model_id,
            "enabled": True,
            "native": True,
            "vendor": provider,
            "supports_tools": True,
            "supports_reasoning": True,
            "context_window": 272000,
            "supported_efforts": [],
        }

    def _build(self, provider, candidate, override):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_provider(root, provider)
            path = root / ".harness" / "model-overrides" / "unmatched-models.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"models": {candidate["id"]: override}}), encoding="utf-8")
            score_path = root / ".harness" / "openrouter" / "model-scores.json"
            with patch.object(openrouter_sync, "ROOT", root), patch.object(
                openrouter_sync, "HISTORY_DIR", root / ".harness" / "model-history"
            ), patch.object(
                openrouter_sync, "OPENROUTER_SCORES_PATH", score_path
            ):
                payload, _ = openrouter_sync.build_provider_inventory_from_scores(
                    provider, self._scores(), candidates=[candidate]
                )
        return payload["models"][0]

    def test_verified_alias_inherits_full_central_scores(self):
        row = self._build(
            "codex",
            self._candidate("gpt-reserve", "openai"),
            {
                "strategy": "score_alias",
                "score_model_id": "openai/gpt-5.6-luna",
                "confidence": "high",
            },
        )
        self.assertEqual(row["openrouter_match"], "unmatched")
        self.assertIsNone(row["openrouter_id"])
        self.assertEqual(row["capabilities"]["reasoning"], 4.1)
        self.assertEqual(row["cost"], 3.0)
        self.assertEqual(row["score_override"]["strategy"], "score_alias")

    def test_proxy_inherits_quality_but_not_commercial_or_health_metrics(self):
        row = self._build(
            "codex",
            self._candidate("codex-auto-review", "openai"),
            {
                "strategy": "score_proxy",
                "score_model_id": "openai/gpt-5.4",
                "confidence": "low",
            },
        )
        self.assertEqual(row["capabilities"]["reasoning"], 3.8)
        self.assertEqual(row["capabilities"]["coding"], 4.0)
        self.assertEqual(row["capabilities"]["reliability"], 0.0)
        self.assertEqual(row["cost"], 0.0)
        self.assertEqual(row["latency"], 0.0)
        self.assertTrue(row["score_override"]["estimated"])

    def test_direct_override_supplies_big_pickle_scores(self):
        row = self._build(
            "opencode",
            self._candidate("opencode/big-pickle", "opencode"),
            {
                "strategy": "direct",
                "confidence": "medium",
                "scores": {
                    "capabilities": {
                        "reasoning": 3.391,
                        "coding": 3.391,
                        "tool_use": 3.0,
                        "reliability": 0.0,
                    },
                    "cost": 5.0,
                    "latency": 0.0,
                    "context_window": 200000,
                    "raw_metrics": {"orpt_composite": 0.615},
                },
            },
        )
        self.assertEqual(row["capabilities"]["reasoning"], 3.391)
        self.assertEqual(row["cost"], 5.0)
        self.assertEqual(row["context_window"], 272000)  # provider-declared context wins
        self.assertEqual(row["score_override"]["strategy"], "direct")


if __name__ == "__main__":
    unittest.main()
