import json
import sys
import unittest
from unittest.mock import patch
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

    @patch("context_compiler.codegraph_explore")
    def test_high_risk_task_uses_validated_codegraph_paths(self, explore):
        explore.return_value = {
            "ok": True,
            "paths": ["scripts/context_compiler.py", "tests/test_context_efficiency.py"],
            "text": "untrusted provider text must not enter the context artifact",
        }
        out = build({
            "id": "T-codegraph-ready",
            "description": "Trace context compilation and retrieval",
            "files": ["scripts/context_compiler.py"],
            "tags": ["context", "retrieval"],
        }, {"risk": "R2"})
        self.assertEqual(out["codegraph"], {"status": "ready", "reason": None})
        self.assertEqual(out["retrieval"]["backend"], "codegraph")
        self.assertTrue(any("codegraph" in record["reason"] for record in out["files"]))
        self.assertNotIn("untrusted provider text", json.dumps(out))
        explore.assert_called_once()

    @patch("context_compiler.codegraph_explore")
    def test_codegraph_paths_are_filtered_before_ranking(self, explore):
        explore.return_value = {
            "ok": True,
            "paths": ["../outside.py", ".env", "missing.py", "scripts/context_compiler.py"],
        }
        out = build({
            "id": "T-codegraph-filtered",
            "description": "Trace context compilation",
            "files": ["scripts/context_compiler.py"],
        }, {"risk": "R2"})
        selected = {record["path"] for record in out["files"]}
        self.assertNotIn("../outside.py", selected)
        self.assertNotIn(".env", selected)
        self.assertNotIn("missing.py", selected)
        self.assertIn("scripts/context_compiler.py", selected)

    @patch("context_compiler.codegraph_explore")
    def test_codegraph_with_no_admitted_paths_uses_fallback(self, explore):
        explore.return_value = {
            "ok": True,
            "paths": ["../outside.py", ".env", "missing.py"],
        }
        out = build({
            "id": "T-codegraph-no-candidates",
            "description": "Trace context compilation",
            "files": ["scripts/context_compiler.py"],
        }, {"risk": "R2"})
        self.assertEqual(out["codegraph"], {"status": "fallback", "reason": "no-candidate-paths"})
        self.assertEqual(out["retrieval"]["backend"], "builtin")
        explore.assert_called_once()

    @patch("context_compiler.codegraph_explore")
    def test_codegraph_failure_and_malformed_output_use_fixed_fallback(self, explore):
        for response in (
            {"ok": False, "reason": "private provider diagnostics"},
            {"ok": True, "paths": None},
            {"ok": True, "paths": []},
        ):
            with self.subTest(response=response):
                explore.reset_mock()
                explore.return_value = response
                out = build({
                    "id": "T-codegraph-fallback",
                    "description": "Trace context compilation",
                    "files": ["scripts/context_compiler.py"],
                }, {"risk": "R2"})
                self.assertEqual(out["codegraph"]["status"], "fallback")
                self.assertEqual(out["retrieval"]["backend"], "builtin")
                self.assertNotIn("private provider diagnostics", json.dumps(out))
                explore.assert_called_once()

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
