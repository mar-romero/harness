import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import harnesslib  # noqa: E402
import openrouter_sync  # noqa: E402
from model_router import selections_for_task  # noqa: E402

HOST_MODELS = [
    "claude-opus-5-thinking-high",
    "cursor-grok-4.6-high-fast",
    "gemini-3.8-flash-high",
    "gpt-5.6-sol-medium",
    "composer-2.5-fast",
]


def _model(mid, vendor, family, effort, caps, cost=2.0, latency=2.0):
    return {
        "id": mid, "enabled": True, "native": True, "vendor": vendor, "family": family,
        "supported_efforts": [effort] if effort else [],
        "capabilities": dict(zip(("reasoning", "coding", "tool_use", "reliability"), caps)),
        "cost": cost, "latency": latency,
    }


INVENTORY = {
    "schema_version": 3, "provider": "cursor", "generated_at": "2026-01-01T00:00:00Z",
    "models": [
        _model("claude-opus-5-thinking-high", "anthropic", "anthropic/claude-opus-5", "high", (4.9, 4.9, 4.9, 4.8), cost=1.2),
        _model("gpt-5.6-sol-medium", "openai", "openai/gpt-5.6-sol", "medium", (4.8, 4.9, 4.8, 4.7), cost=1.7),
        _model("gemini-3.8-flash-high", "google", "google/gemini-3.8-flash", "high", (4.4, 4.8, 4.4, 4.3), cost=2.3, latency=4.0),
        _model("composer-2.5-fast", "cursor", "cursor/composer-2.5", None, (0.0, 0.0, 3.0, 0.0)),
    ],
}


class CursorModelIdParsingTests(unittest.TestCase):
    def setUp(self):
        self.cfg = openrouter_sync.load_provider_config("cursor")

    def test_effort_and_variants_are_split_from_base(self):
        parsed = openrouter_sync.parse_cursor_model_id("claude-opus-5-thinking-high", self.cfg)
        self.assertEqual(parsed, {"base": "claude-opus-5", "effort": "high", "variants": ["thinking"], "vendor": "anthropic"})

    def test_cursor_prefix_and_trailing_variant(self):
        parsed = openrouter_sync.parse_cursor_model_id("cursor-grok-4.6-high-fast", self.cfg)
        self.assertEqual((parsed["base"], parsed["effort"], parsed["variants"], parsed["vendor"]),
                         ("grok-4.6", "high", ["fast"], "x-ai"))

    def test_model_without_effort_keeps_version(self):
        parsed = openrouter_sync.parse_cursor_model_id("composer-2.5-fast", self.cfg)
        self.assertEqual((parsed["base"], parsed["effort"], parsed["vendor"]), ("composer-2.5", None, "cursor"))

    def test_single_token_id_is_never_emptied(self):
        self.assertEqual(openrouter_sync.parse_cursor_model_id("high", self.cfg)["base"], "high")


class CursorDiscoveryTests(unittest.TestCase):
    def test_allowlist_env_is_the_host_catalog(self):
        cfg = openrouter_sync.load_provider_config("cursor")
        with mock.patch.dict(os.environ, {"HARNESS_CURSOR_MODELS": ",".join(HOST_MODELS + HOST_MODELS[:1])}):
            rows = openrouter_sync.discover_provider("cursor", cfg)
        self.assertEqual([r["id"] for r in rows], HOST_MODELS)
        opus = rows[0]
        self.assertEqual((opus["match_id"], opus["supported_efforts"], opus["vendor"]), ("claude-opus-5", ["high"], "anthropic"))
        self.assertEqual(opus["availability_source"], "cursor-allowlist-env")

    def test_openrouter_resolution_uses_base_model(self):
        cfg = openrouter_sync.load_provider_config("cursor")
        with mock.patch.dict(os.environ, {"HARNESS_CURSOR_MODELS": "gpt-5.6-sol-medium,composer-2.5-fast"}):
            rows = openrouter_sync.discover_provider("cursor", cfg)
        idx = {"openai/gpt-5.6-sol": {"id": "openai/gpt-5.6-sol"}}
        openrouter_sync._resolve_provider_candidates("cursor", rows, idx)
        self.assertEqual(rows[0]["openrouter_id"], "openai/gpt-5.6-sol")
        self.assertIsNone(rows[1]["openrouter_id"])


    def test_empty_host_catalog_never_overwrites_inventory(self):
        with self.assertRaisesRegex(ValueError, "host catalog missing"):
            openrouter_sync.build_provider_inventory_from_scores("cursor", {"models": []}, candidates=[])


class CursorBindingTests(unittest.TestCase):
    def test_cursor_is_a_native_binding_provider(self):
        self.assertIn("cursor", harnesslib.BINDING_PROVIDERS)
        self.assertEqual(harnesslib.provider_inventory_binding_path("cursor"),
                         ROOT / "harness" / "model-inventories" / "cursor.json")

    def test_activator_never_refreshes_openrouter(self):
        text = (ROOT / "scripts/providers/cursor_activate_task.py").read_text(encoding="utf-8")
        self.assertNotIn("refresh_provider_inventory", text)
        self.assertIn("network_refresh", text)


class CursorSelectionTests(unittest.TestCase):
    def _select(self, task):
        return {s["agent"]: s for s in selections_for_task(task, "cursor", INVENTORY)}

    def test_selected_ids_are_host_model_ids_with_fixed_effort(self):
        sel = self._select({"id": "CURSOR-R1", "description": "Add a flag to the router output",
                            "files": ["scripts/task_router.py"], "acceptance": ["flag works"]})
        impl = sel["implementer"]
        self.assertEqual(impl["action"], "use")
        self.assertIn(impl["model_id"], HOST_MODELS)
        self.assertNotIn("#", impl["model_id"])

    def test_unscored_model_is_never_selected_for_writes_or_review(self):
        sel = self._select({"id": "CURSOR-R2", "description": "Change the ledger format",
                            "files": ["scripts/evidence.py"], "acceptance": ["ok"],
                            "risk_factors": {"persistence": True}})
        for agent in ("implementer", "reviewer", "verifier"):
            self.assertNotEqual(sel[agent]["model_id"], "composer-2.5-fast", agent)

    def test_reviewer_prefers_a_model_independent_from_the_implementer(self):
        sel = self._select({"id": "CURSOR-R2B", "description": "Change the ledger format",
                            "files": ["scripts/evidence.py"], "acceptance": ["ok"],
                            "risk_factors": {"persistence": True}})
        self.assertNotEqual(sel["reviewer"]["base_model_id"], sel["implementer"]["base_model_id"])
        self.assertIn(sel["reviewer"]["independence"]["strength"],
                      {"different_model", "different_family", "different_vendor"})


class CursorOrchestratorAdapterTests(unittest.TestCase):
    def test_rule_is_derived_from_canonical_role_for_cursor(self):
        from compile_harness import generated
        rule = generated()[Path(".cursor/rules/harness-orchestrator.mdc")]
        self.assertIn("primary Cursor orchestrator", rule)
        self.assertIn("scripts/providers/cursor_activate_task.py", rule)
        self.assertNotIn("codex_activate_task", rule)
        self.assertIn("delegation[].model", rule)
        self.assertIn("alwaysApply: false", rule)

    def test_canonical_drift_fails_instead_of_emitting_codex_steps(self):
        from compile_harness import cursor_orchestrator_body
        with self.assertRaisesRegex(ValueError, "update the Cursor adapter"):
            cursor_orchestrator_body("You are an orchestrator.")


class CursorInventoryArtifactTests(unittest.TestCase):
    def test_committed_inventory_matches_binding_contract(self):
        path = ROOT / "harness" / "model-inventories" / "cursor.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((data["schema_version"], data["provider"]), (3, "cursor"))
        self.assertTrue(data["models"])
        for model in data["models"]:
            self.assertLessEqual(len(model.get("supported_efforts") or []), 1, model["id"])


if __name__ == "__main__":
    unittest.main()
