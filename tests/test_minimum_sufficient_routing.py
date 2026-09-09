from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from model_router import load_policy, select_effort, select_model


def model(mid: str, *, reasoning: float, coding: float, tool: float, reliability: float, cost: float, latency: float, vendor: str = "v"):
    return {
        "id": mid,
        "enabled": True,
        "vendor": vendor,
        "family": mid,
        "capabilities": {
            "reasoning": reasoning,
            "coding": coding,
            "tool_use": tool,
            "reliability": reliability,
        },
        "cost": cost,
        "latency": latency,
        "supported_efforts": ["low", "medium", "high", "xhigh"],
    }


class MinimumSufficientRoutingTests(unittest.TestCase):
    def setUp(self):
        self.policy = copy.deepcopy(load_policy())
        self.policy["inventory"]["enforce_max_age"] = False
        self.policy["selection"]["strategy"] = "minimum_sufficient"

    def test_selects_smallest_model_that_meets_dynamic_target(self):
        inventory = {
            "provider": "subscriptions",
            "models": [
                model("claude/small", reasoning=3.4, coding=4.1, tool=4.1, reliability=3.6, cost=5.0, latency=5.0),
                model("codex/large", reasoning=5.0, coding=5.0, tool=5.0, reliability=5.0, cost=3.0, latency=3.0),
            ],
        }
        result = select_model(
            task_id="MIN-1", provider="subscriptions", agent="implementer",
            model_class="coding", risk="R1", inventory=inventory, policy=self.policy,
            target={"reasoning": 3.2, "coding": 4.0, "tool_use": 4.0, "reliability": 3.5},
        )
        self.assertEqual(result["base_model_id"], "claude/small")
        self.assertEqual(result["selection_strategy"], "minimum_sufficient")
        self.assertFalse(result["sufficiency_degraded"])
        self.assertIsNotNone(result["resource_burden"])

    def test_does_not_choose_cheap_model_below_required_target(self):
        inventory = {
            "provider": "subscriptions",
            "models": [
                model("claude/too-small", reasoning=3.1, coding=4.0, tool=4.0, reliability=3.4, cost=5.0, latency=5.0),
                model("codex/adequate", reasoning=4.0, coding=4.4, tool=4.5, reliability=4.0, cost=3.8, latency=3.8),
            ],
        }
        result = select_model(
            task_id="MIN-2", provider="subscriptions", agent="implementer",
            model_class="coding", risk="R1", inventory=inventory, policy=self.policy,
            target={"reasoning": 3.5, "coding": 4.2, "tool_use": 4.2, "reliability": 3.8},
        )
        self.assertEqual(result["base_model_id"], "codex/adequate")
        self.assertFalse(result["sufficiency_degraded"])

    def test_r3_blocks_when_no_model_meets_dynamic_sufficiency(self):
        inventory = {
            "provider": "subscriptions",
            "models": [
                model("weak", reasoning=4.0, coding=4.0, tool=3.0, reliability=4.0, cost=5.0, latency=5.0),
            ],
        }
        result = select_model(
            task_id="MIN-3", provider="subscriptions", agent="security-reviewer",
            model_class="reasoning", risk="R3", inventory=inventory, policy=self.policy,
            target={"reasoning": 4.8, "coding": 2.0, "tool_use": 4.0, "reliability": 4.8},
        )
        self.assertEqual(result["action"], "block")
        self.assertIn("sufficiency", result["reason"])

    def test_effort_fallback_uses_next_higher_tier(self):
        policy = copy.deepcopy(self.policy)
        policy["effort"]["fallback"] = "nearest-higher-then-lower"
        effort, _pressure = select_effort(
            {"reasoning": 4.3, "coding": 4.0, "tool_use": 4.0, "reliability": 4.2},
            "reviewer", "R2", ["medium", "xhigh"], policy,
        )
        self.assertEqual(effort, "xhigh")


if __name__ == "__main__":
    unittest.main()
