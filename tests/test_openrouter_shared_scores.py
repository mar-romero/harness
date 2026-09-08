import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import openrouter_sync


class SharedOpenRouterScoreTests(unittest.TestCase):
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

    def test_same_openrouter_model_produces_same_scores_for_both_providers(self):
        models = {
            "data": [
                {
                    "id": "openai/gpt-example",
                    "supported_parameters": ["tools"],
                    "context_length": 100000,
                    "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                },
                {
                    "id": "vendor/other",
                    "supported_parameters": [],
                    "context_length": 50000,
                    "pricing": {"prompt": "0.00001", "completion": "0.00002"},
                },
            ]
        }
        benchmarks = {
            "data": [
                {
                    "model_id": "openai/gpt-example",
                    "intelligence_index": 80,
                    "coding_index": 75,
                    "agentic_index": 70,
                },
                {
                    "model_id": "vendor/other",
                    "intelligence_index": 40,
                    "coding_index": 35,
                    "agentic_index": 30,
                },
            ]
        }
        scores = openrouter_sync._build_openrouter_scores_payload(models, benchmarks)

        codex_candidate = {
            "id": "gpt-example",
            "enabled": True,
            "native": True,
            "vendor": "openai",
            "supports_tools": True,
            "supports_reasoning": True,
            "supported_efforts": [],
        }
        opencode_candidate = {
            "id": "opencode/gpt-example",
            "enabled": True,
            "native": True,
            "vendor": "opencode",
            "supports_tools": True,
            "supports_reasoning": True,
            "supported_efforts": [],
        }

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_provider(root, "codex")
            self._write_provider(root, "opencode")
            score_path = root / ".harness" / "openrouter" / "model-scores.json"
            with patch.object(openrouter_sync, "ROOT", root), patch.object(
                openrouter_sync, "HISTORY_DIR", root / ".harness" / "model-history"
            ), patch.object(openrouter_sync, "OPENROUTER_SCORES_PATH", score_path):
                codex, _ = openrouter_sync.build_provider_inventory_from_scores(
                    "codex", scores, candidates=[codex_candidate]
                )
                opencode, _ = openrouter_sync.build_provider_inventory_from_scores(
                    "opencode", scores, candidates=[opencode_candidate]
                )

        c = codex["models"][0]
        o = opencode["models"][0]
        self.assertEqual(c["openrouter_id"], "openai/gpt-example")
        self.assertEqual(o["openrouter_id"], "openai/gpt-example")
        self.assertEqual(c["capabilities"], o["capabilities"])
        self.assertEqual(c["cost"], o["cost"])
        self.assertEqual(c["latency"], o["latency"])
        self.assertGreater(c["capabilities"]["reasoning"], 0)
        self.assertGreater(c["capabilities"]["coding"], 0)

    def test_unmatched_model_uses_local_history_fallback(self):
        scores = {
            "schema_version": 1,
            "provider": "openrouter",
            "generated_at": "2026-09-07T00:00:00Z",
            "models": [{"id": "vendor/unrelated", "capabilities": {}}],
        }
        candidate = {
            "id": "opencode/big-pickle",
            "enabled": True,
            "native": True,
            "vendor": "opencode",
            "supports_tools": True,
            "supports_reasoning": True,
            "supported_efforts": [],
        }
        history = {
            "models": {
                "opencode/big-pickle": {
                    "benchmarks": {
                        "orpt_bench": {"composite": 0.6, "success_rate": 0.7},
                        "swe_atlas_codebase_qna": {"resolve_rate": 0.5},
                    },
                    "capabilities": {"reasoning": True, "tool_call": True},
                    "limits": {"context": 200000},
                }
            }
        }

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_provider(root, "opencode")
            history_path = root / ".harness" / "model-history" / "opencode.json"
            history_path.parent.mkdir(parents=True, exist_ok=True)
            history_path.write_text(json.dumps(history), encoding="utf-8")
            score_path = root / ".harness" / "openrouter" / "model-scores.json"
            with patch.object(openrouter_sync, "ROOT", root), patch.object(
                openrouter_sync, "HISTORY_DIR", root / ".harness" / "model-history"
            ), patch.object(openrouter_sync, "OPENROUTER_SCORES_PATH", score_path):
                payload, _ = openrouter_sync.build_provider_inventory_from_scores(
                    "opencode", scores, candidates=[candidate]
                )

        row = payload["models"][0]
        self.assertIsNone(row["openrouter_id"])
        self.assertTrue(row["local_fallback_used"])
        self.assertIn("reasoning", row["local_fallback_fields"])
        self.assertIn("coding", row["local_fallback_fields"])
        self.assertGreater(row["capabilities"]["reasoning"], 0)
        self.assertGreater(row["capabilities"]["coding"], 0)
        self.assertEqual(row["context_window"], 200000)

    def test_endpoint_health_failure_is_nonfatal(self):
        scores = {
            "schema_version": 1,
            "provider": "openrouter",
            "models": [
                {
                    "id": "openai/gpt-example",
                    "capabilities": {
                        "reasoning": 4.0,
                        "coding": 4.0,
                        "tool_use": 4.0,
                        "reliability": 0.0,
                    },
                    "cost": 3.0,
                    "latency": 0.0,
                    "raw_metrics": {},
                    "provenance": {},
                }
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            score_path = Path(td) / "model-scores.json"
            with patch.object(openrouter_sync, "OPENROUTER_SCORES_PATH", score_path), patch.object(
                openrouter_sync, "_http_json", side_effect=TimeoutError("timeout")
            ):
                result = openrouter_sync.enrich_openrouter_endpoint_health(
                    scores,
                    ["openai/gpt-example"],
                    api_key="test-key",
                    timeout=1,
                )
        row = result["models"][0]
        self.assertEqual(row["capabilities"]["reliability"], 0.0)
        self.assertEqual(row["latency"], 0.0)


if __name__ == "__main__":
    unittest.main()
