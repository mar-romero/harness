import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from context_compiler import excluded
from product_planning import PlanningError, validate_bundle

POLICY = json.loads((ROOT / "harness" / "product-discovery-policy.json").read_text(encoding="utf-8"))
CONTEXT_POLICY = json.loads((ROOT / "harness" / "context-policy.json").read_text(encoding="utf-8"))


def bundle(status="approved", files=None):
    return {
        "schema_version": 1,
        "discovery_id": "DISC-SURFACE",
        "status": status,
        "classification": "FEATURE",
        "title": "Surface test",
        "original_request": {"text": "Crear feature", "language": "es"},
        "refined_problem": "Need a bounded implementation surface.",
        "target_users": ["user"],
        "outcomes": ["working feature"],
        "success_metrics": [],
        "scope": {"in": ["feature"], "out": []},
        "assumptions": [],
        "open_questions": [],
        "challenges": [],
        "mvp": {"goal": "vertical slice", "includes": ["slice"], "excludes": []},
        "work_items": [
            {"id": "FEAT-SURFACE", "type": "FEATURE", "title": "Feature", "description": "Feature description"}
        ],
        "tasks": [
            {
                "id": "TASK-SURFACE",
                "title": "Implement feature",
                "description": "Implement the bounded feature.",
                "acceptance_criteria": ["Feature works"],
                "dependencies": [],
                "size": "S",
                "files": [] if files is None else files,
            }
        ],
        "proposed_sprint": None,
    }


class ProductSurfaceRegressionTests(unittest.TestCase):
    def test_approved_task_rejects_empty_file_surface(self):
        with self.assertRaises(PlanningError):
            validate_bundle(bundle("approved", []), POLICY)

    def test_ready_for_approval_rejects_empty_file_surface(self):
        with self.assertRaises(PlanningError):
            validate_bundle(bundle("ready_for_approval", []), POLICY)

    def test_draft_can_temporarily_omit_file_surface(self):
        result = validate_bundle(bundle("draft", []), POLICY)
        self.assertTrue(result["ok"])

    def test_greenfield_prospective_paths_are_accepted(self):
        result = validate_bundle(
            bundle("approved", ["examples/new-app/src/new_app/lexer.py", "examples/new-app/tests/test_lexer.py"]),
            POLICY,
        )
        self.assertTrue(result["ok"])

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(PlanningError):
            validate_bundle(bundle("approved", ["../outside.py"]), POLICY)

    def test_context_policy_excludes_all_harness_runtime_state(self):
        self.assertIn(".harness", CONTEXT_POLICY["exclude_dirs"])
        self.assertTrue(excluded(Path(".harness/backups/old/tests/test_old.py"), CONTEXT_POLICY))
        self.assertTrue(excluded(Path(".harness/runs/T/context.json"), CONTEXT_POLICY))


if __name__ == "__main__":
    unittest.main()
