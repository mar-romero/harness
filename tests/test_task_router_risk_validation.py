import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import task_router as tr


class TaskRouterRiskValidationTests(unittest.TestCase):
    def test_router_rejects_truthy_string_risk_factor(self):
        task = {"id": "TASK-RISK", "risk_factors": {"persistence": "none"}}
        with self.assertRaisesRegex(ValueError, "values must be booleans"):
            tr._validate_risk_factors(task)

    def test_router_rejects_unknown_risk_factor(self):
        task = {"id": "TASK-RISK", "risk_factors": {"correctness": "high"}}
        with self.assertRaisesRegex(ValueError, "contains unknown keys: correctness"):
            tr._validate_risk_factors(task)

    def test_router_accepts_canonical_boolean_risk_factors(self):
        task = {
            "id": "TASK-RISK",
            "risk_factors": {"persistence": False, "important_calculation": True},
        }
        out = tr._validate_risk_factors(task)
        self.assertFalse(out["persistence"])
        self.assertTrue(out["important_calculation"])


if __name__ == "__main__":
    unittest.main()
