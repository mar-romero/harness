import json, sys, unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from model_router import select_model, load_policy


class ManualModelInventorySyncTests(unittest.TestCase):
    def test_task_activators_do_not_refresh_openrouter(self):
        for rel in ("scripts/providers/opencode_activate_task.py", "scripts/providers/codex_activate_task.py"):
            text = (ROOT / rel).read_text()
            self.assertNotIn("refresh_provider_inventory", text)
            self.assertIn("network_refresh", text)

    def test_policy_disables_inventory_age_enforcement(self):
        policy = load_policy()
        self.assertEqual(policy["inventory"]["refresh_mode"], "manual")
        self.assertFalse(policy["inventory"]["enforce_max_age"])

    def test_old_manual_inventory_remains_usable(self):
        policy = load_policy()
        inventory = {
            "provider": "codex",
            "generated_at": "2020-01-01T00:00:00Z",
            "models": [{
                "id": "old-but-operator-approved", "enabled": True, "native": True,
                "family": "f", "vendor": "openai", "supported_efforts": ["low", "medium", "high"],
                "capabilities": {"reasoning": 5, "coding": 5, "tool_use": 5, "reliability": 5},
                "cost": 3, "latency": 3
            }]
        }
        target = {"reasoning": 4.5, "coding": 4.5, "tool_use": 4.5, "reliability": 4.5}
        result = select_model(task_id="T", provider="codex", agent="implementer", model_class="coding", risk="R3", inventory=inventory, policy=policy, now=datetime(2099,1,1,tzinfo=timezone.utc), target=target)
        self.assertEqual(result["status"], "selected")
        self.assertFalse(result["inventory_stale"])


if __name__ == "__main__": unittest.main()
