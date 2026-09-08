from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("receipt_review_routing", ROOT / "scripts" / "receipt_review.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ReceiptRoutingAndConsentTests(unittest.TestCase):
    def _route(self):
        return {
            "task_id": "TASK-1",
            "risk": "R1",
            "agents": ["implementer", "reviewer"],
            "skills": [],
            "requirements": {"review": True, "verification": False},
        }

    def test_rdd_off_high_candidate_adds_independent_verifier(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td)
            with patch.object(mod, "run_dir", return_value=run), \
                 patch.object(mod, "mode_status", return_value={"mode": "off", "source": "test"}), \
                 patch.object(mod, "assess", return_value={"risk": "high", "subject_hash": "s"}), \
                 patch.object(mod, "_task_route", return_value=self._route()), \
                 patch.object(mod, "_small_implementer", return_value=(False, "full model")):
                plan = mod.prepare("TASK-1", apply=False)
        self.assertTrue(plan["independent_verifier"])
        self.assertIn("assess=high", " ".join(plan["reasons"]))

    def test_rdd_off_medium_small_model_adds_independent_verifier(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td)
            with patch.object(mod, "run_dir", return_value=run), \
                 patch.object(mod, "mode_status", return_value={"mode": "off", "source": "test"}), \
                 patch.object(mod, "assess", return_value={"risk": "medium", "subject_hash": "s"}), \
                 patch.object(mod, "_task_route", return_value=self._route()), \
                 patch.object(mod, "_small_implementer", return_value=(True, "low effort")):
                plan = mod.prepare("TASK-1", apply=False)
        self.assertTrue(plan["independent_verifier"])

    def test_review_consent_is_reused_for_same_provider_session(self):
        with tempfile.TemporaryDirectory() as td:
            temp_root = Path(td)
            runtime = temp_root / ".harness" / "receipt-review"
            with patch.object(mod, "ROOT", temp_root), patch.object(mod, "RUNTIME", runtime):
                granted = mod.grant_consent("TASK-1", "opencode", "SESSION-A")
                reused = mod.consent_status("TASK-2", "opencode", "SESSION-A")
                other = mod.consent_status("TASK-2", "opencode", "SESSION-B")
        self.assertTrue(granted["granted"])
        self.assertTrue(reused["granted"])
        self.assertFalse(other["granted"])

    def test_dynamic_verifier_gets_independent_model_selection(self):
        import json
        import model_router
        import model_task_profile

        with tempfile.TemporaryDirectory() as td:
            temp_root = Path(td)
            run = temp_root / ".harness" / "runs" / "TASK-1"
            run.mkdir(parents=True)
            (run / "task.json").write_text(json.dumps({"id": "TASK-1", "description": "change code"}), encoding="utf-8")
            (run / "model-selections.json").write_text(json.dumps({
                "provider": "opencode",
                "inventory_path": None,
                "selections": [{
                    "agent": "implementer",
                    "status": "selected",
                    "action": "use",
                    "model_id": "vendor/impl",
                    "base_model_id": "vendor/impl",
                    "model_family": "impl-family",
                    "model_vendor": "vendor",
                }],
            }), encoding="utf-8")
            route = self._route()
            route["agents"].append("verifier")
            captured = {}

            def fake_select_model(**kwargs):
                captured.update(kwargs)
                return {
                    "agent": "verifier",
                    "status": "selected",
                    "action": "use",
                    "model_id": "other/verifier",
                    "base_model_id": "other/verifier",
                    "model_family": "verify-family",
                    "model_vendor": "other",
                }

            manifest = {"agents": {"verifier": {"model_class": "reasoning"}}}
            policy = {"independence": {"roles": {"verifier": {"avoid_agents": ["implementer"]}}}}
            with patch.object(mod, "ROOT", temp_root), \
                 patch.object(mod, "run_dir", return_value=run), \
                 patch.object(mod, "load_manifest", return_value=manifest), \
                 patch.object(model_router, "load_policy", return_value=policy), \
                 patch.object(model_router, "select_model", side_effect=fake_select_model), \
                 patch.object(model_task_profile, "profile_task", return_value={}), \
                 patch.object(model_task_profile, "target_for_agent", return_value={}):
                created = mod._ensure_dynamic_agent_models("TASK-1", route, ["verifier"])

            self.assertEqual(len(created), 1)
            self.assertIn("vendor/impl", captured["avoid_models"])
            self.assertIn("impl-family", captured["avoid_families"])
            self.assertIn("vendor", captured["avoid_vendors"])
            saved = json.loads((run / "model-selections.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["selections"][-1]["agent"], "verifier")


if __name__ == "__main__":
    unittest.main()
