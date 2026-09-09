import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from context_compiler import build
from aci_core import call_tool, tool_definitions


class ContextEfficiencyTests(unittest.TestCase):
    def test_small_explicit_task_skips_structured_retrieval(self):
        out = build({
            "id": "T-small-retrieval",
            "description": "Update router comment",
            "files": ["scripts/task_router.py"],
        }, {"risk": "R0"})
        self.assertFalse(out["retrieval"]["needed"])
        self.assertEqual(out["retrieval"]["status"], "skipped")

    def test_high_risk_task_uses_bounded_builtin_fallback_without_codegraph(self):
        out = build({
            "id": "T-large-retrieval",
            "description": "Trace authentication routing, model selection, verification, and impact before changing behavior",
            "files": ["scripts/task_router.py", "scripts/model_router.py"],
            "tags": ["routing", "model", "verification", "impact"],
        }, {"risk": "R3"})
        self.assertTrue(out["retrieval"]["needed"])
        self.assertIn(out["retrieval"]["backend"], {"builtin", "codegraph"})
        self.assertIn("repo_map", out["structured_context"])
        self.assertIn("symbol_snippets", out["structured_context"])
        self.assertLessEqual(out["estimated_tokens"], out["limits"]["estimated_tokens"])
        self.assertGreaterEqual(out["candidate_full_file_tokens"], out["estimated_tokens"])

    def test_repo_explore_is_exposed_and_bounded(self):
        names = {row["name"] for row in tool_definitions()}
        self.assertIn("repo_explore", names)
        out = call_tool("repo_explore", {"query": "task routing model selection", "max_files": 5})
        self.assertTrue(out["ok"], out)
        data = out["data"]
        self.assertIn(data["backend"], {"builtin-symbol", "codegraph"})
        self.assertLessEqual(int(data.get("estimated_tokens", 0)), 7000)


if __name__ == "__main__":
    unittest.main()
