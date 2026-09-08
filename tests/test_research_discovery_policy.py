import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("research_discovery", ROOT / "scripts" / "research_discovery.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ResearchDiscoveryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = {
            "enabled": True,
            "routing": {
                "full_classifications": ["PROJECT", "EPIC"],
                "blocking_question_threshold_for_full": 3,
            },
        }

    def test_project_is_full(self):
        out = mod.assess_mode({"classification": "PROJECT", "open_questions": []}, self.policy)
        self.assertEqual(out["mode"], "full")

    def test_bounded_task_is_none(self):
        out = mod.assess_mode({"classification": "TASK", "open_questions": [], "refined_problem": "fix parser bug"}, self.policy)
        self.assertEqual(out["mode"], "none")

    def test_external_unknown_routes_research(self):
        out = mod.assess_mode({"classification": "TASK", "open_questions": [], "refined_problem": "verify external API protocol"}, self.policy)
        self.assertEqual(out["mode"], "research")

    def test_explicit_mode_wins(self):
        out = mod.assess_mode({"classification": "PROJECT", "research_rdd": {"mode": "light"}}, self.policy)
        self.assertEqual(out["mode"], "light")

    def test_empty_complete_research_artifact_is_not_meaningful(self):
        policy = {"minimum_content": {"research": {"sources": 1, "findings": 1}}}
        failures = mod.artifact_content_failures("research", {"status": "complete", "sources": [], "findings": []}, policy)
        self.assertTrue(failures)


if __name__ == "__main__":
    unittest.main()
