import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ProductDiscoveryIntegrationTests(unittest.TestCase):
    def test_skills_exist_with_canonical_frontmatter(self):
        for name in ("idea-to-work", "sprint-planning"):
            text = (ROOT / ".agents" / "skills" / name / "SKILL.md").read_text()
            self.assertTrue(text.startswith("---\n"))
            self.assertIn(f"name: {name}\n", text)

    def test_policy_is_parseable_and_question_budget_is_bounded(self):
        policy = json.loads((ROOT / "harness" / "product-discovery-policy.json").read_text())
        self.assertLessEqual(policy["questioning"]["max_questions_per_round"], 3)
        self.assertLessEqual(policy["questioning"]["max_rounds"], 2)
        self.assertTrue(policy["approval"]["executable_tasks_require_approved_status"])

    def test_orchestrator_has_discovery_integration(self):
        text = (ROOT / ".opencode" / "agents" / "harness-orchestrator.md").read_text()
        self.assertIn("PRODUCT_DISCOVERY_V1", text)
        self.assertIn("scripts/product_planning.py", text)
        self.assertIn("planning/discovery/*.json", text)
        self.assertIn("idea-to-work", text)

    def test_task_intake_does_not_force_broad_ideas_into_tasks(self):
        text = (ROOT / ".agents" / "skills" / "task-intake" / "SKILL.md").read_text()
        self.assertIn("idea-to-work", text)


if __name__ == "__main__":
    unittest.main()
