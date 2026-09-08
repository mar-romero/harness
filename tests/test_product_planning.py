import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from product_planning import PlanningError, _risk_factors, materialize, sprint_priority, validate_bundle

POLICY = json.loads((ROOT / "harness" / "product-discovery-policy.json").read_text())


def bundle(status="approved"):
    return {
        "schema_version": 1,
        "discovery_id": "DISC-TEST",
        "status": status,
        "classification": "FEATURE",
        "title": "Test feature",
        "original_request": {"text": "Quiero una mejora", "language": "es"},
        "refined_problem": "A real user problem.",
        "target_users": ["operator"],
        "outcomes": ["observable outcome"],
        "success_metrics": [{"name": "metric", "definition": "observable metric", "target": None}],
        "scope": {"in": ["one thing"], "out": ["another thing"]},
        "assumptions": [],
        "open_questions": [],
        "challenges": [],
        "mvp": {"goal": "vertical slice", "includes": ["slice"], "excludes": ["polish"]},
        "work_items": [{"id": "FEAT-1", "type": "FEATURE", "title": "Feature", "description": "Feature description", "status": "approved"}],
        "tasks": [{
            "id": "TASK-DISC-1", "title": "Implement slice", "description": "Implement one bounded vertical slice.",
            "acceptance_criteria": ["The slice works end to end"], "parent_id": "FEAT-1", "dependencies": [], "size": "M",
            "files": ["src/feature.py", "tests/test_feature.py"],
            "value": 5, "dependency_unlock": 3, "risk_reduction": 4, "urgency": 3, "confidence": 4,
            "risk_factors": {"external_contract": True}
        }],
        "proposed_sprint": {"id": "SPRINT-1", "goal": "Deliver the slice", "capacity_units": 12, "task_ids": ["TASK-DISC-1"], "waves": [["TASK-DISC-1"]], "rationale": ["vertical slice"]}
    }


class ProductPlanningTests(unittest.TestCase):
    def test_approved_bundle_rejects_blocking_question(self):
        data = bundle()
        data["open_questions"] = [{"question": "Who is the user?", "blocking": True, "dimension": "target_user"}]
        with self.assertRaises(PlanningError):
            validate_bundle(data, POLICY)

    def test_draft_does_not_materialize_tasks(self):
        data = bundle("draft")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "harness").mkdir()
            (root / "harness" / "product-discovery-policy.json").write_text(json.dumps(POLICY))
            result = materialize(data, root=root)
            self.assertEqual(result["tasks_materialized"], 0)
            self.assertFalse((root / "tasks" / "TASK-DISC-1.json").exists())
            self.assertTrue((root / "planning" / "features" / "FEAT-1.json").exists())

    def test_approved_materializes_task_with_provenance_and_full_risk_shape(self):
        data = bundle("approved")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "harness").mkdir()
            (root / "harness" / "product-discovery-policy.json").write_text(json.dumps(POLICY))
            materialize(data, root=root)
            task = json.loads((root / "tasks" / "TASK-DISC-1.json").read_text())
            self.assertEqual(task["origin"]["discovery_id"], "DISC-TEST")
            self.assertNotIn("request", task)
            self.assertTrue(task["risk_factors"]["external_contract"])
            self.assertIn("touches_auth", task["risk_factors"])
            self.assertGreater(task["planning"]["priority_score"], 0)

    def test_non_boolean_risk_factor_is_rejected(self):
        data = bundle()
        data["tasks"][0]["risk_factors"] = {"persistence": "none"}
        with self.assertRaisesRegex(PlanningError, "values must be booleans"):
            validate_bundle(data, POLICY)

    def test_unknown_risk_factor_key_is_rejected(self):
        data = bundle()
        data["tasks"][0]["risk_factors"] = {"correctness": "high"}
        with self.assertRaisesRegex(PlanningError, "contains unknown keys: correctness"):
            validate_bundle(data, POLICY)

    def test_risk_materializer_never_truthy_coerces_strings(self):
        with self.assertRaisesRegex(PlanningError, "values must be booleans"):
            _risk_factors({"persistence": "none"})
        normalized = _risk_factors({"persistence": False, "important_calculation": True})
        self.assertFalse(normalized["persistence"])
        self.assertTrue(normalized["important_calculation"])

    def test_sprint_capacity_is_enforced(self):
        data = bundle()
        data["tasks"][0]["size"] = "XL"
        data["proposed_sprint"]["capacity_units"] = 5
        with self.assertRaises(PlanningError):
            validate_bundle(data, POLICY)

    def test_priority_formula(self):
        score = sprint_priority({"value":5,"dependency_unlock":5,"risk_reduction":5,"urgency":5,"confidence":5}, POLICY)
        self.assertEqual(score, 5.0)


if __name__ == "__main__":
    unittest.main()
