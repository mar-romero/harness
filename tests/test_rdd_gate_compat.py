from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import gate  # noqa: E402
import receipt_review  # noqa: E402


class RDDGateCompatibilityTests(unittest.TestCase):
    def test_r0_acceptance_uses_authoritative_checks_without_inventing_reviewer(self):
        route = {"risk": "R0", "requirements": {"verification": False}, "agents": []}
        with patch.object(gate, "_authoritative_checks_decision", return_value=(True, None)):
            ok, reason = gate._acceptance_decision("TASK-1", route, {})
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_finish_gate_skips_receipt_requirements_until_route_is_prepared(self):
        route = {"task_id": "TASK-1", "risk": "R1", "agents": ["reviewer"]}
        with patch.object(receipt_review, "_task_route", return_value=route):
            decision = receipt_review.finish_decision("TASK-1")
        self.assertTrue(decision["allow"], decision)
        self.assertEqual(decision["required"], [])
        self.assertEqual(decision["missing"], [])

    def test_finish_gate_enforces_receipt_requirements_after_route_is_prepared(self):
        route = {
            "task_id": "TASK-1",
            "risk": "R1",
            "agents": ["reviewer"],
            "receipt_review": {"plan_hash": "abc"},
        }
        blocked = {
            "allow": False,
            "required": ["receipt-assessment", "verification-plan"],
            "missing": ["receipt-assessment", "verification-plan"],
            "failing": [],
        }
        with patch.object(receipt_review, "_task_route", return_value=route), patch.object(
            receipt_review, "verification_finish_decision", return_value=blocked
        ):
            decision = receipt_review.finish_decision("TASK-1")
        self.assertFalse(decision["allow"])
        self.assertIn("receipt-assessment", decision["missing"])


if __name__ == "__main__":
    unittest.main()
