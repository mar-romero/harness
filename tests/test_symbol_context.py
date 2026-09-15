import hashlib
import json
import sys
import tempfile
import unittest
import copy
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from symbol_index import index_source
from snippet_extractor import extract_snippet
from context_compiler import context_policy, fingerprint, stable_source, build
import context_compiler
import context_graph


class SymbolIndexTests(unittest.TestCase):
    def test_nested_symbols_have_stable_ranges_and_qualified_names(self):
        source = """import os\n\n@decorator\nclass Service:\n    def run(self, value):\n        def inner():\n            return value\n        return inner()\n\ndef helper():\n    return 1\n"""
        symbols = index_source(source)
        self.assertEqual([(s["kind"], s["name"], s["start_line"], s["end_line"]) for s in symbols], [
            ("class", "Service", 3, 8),
            ("method", "Service.run", 5, 8),
            ("function", "Service.run.inner", 6, 7),
            ("function", "helper", 10, 11),
        ])

    def test_control_flow_match_and_duplicate_occurrences_are_indexed(self):
        source = """def duplicate():
    return 1
match value:
    case 1:
        def duplicate():
            return 2
"""
        symbols = index_source(source)
        duplicates = [item for item in symbols if item["name"] == "duplicate"]
        self.assertEqual([item["start_line"] for item in duplicates], [1, 5])

    def test_malformed_source_returns_no_symbols(self):
        self.assertEqual(index_source("def broken(:\n"), [])


class SnippetTests(unittest.TestCase):
    def test_decorators_and_class_context_are_preserved(self):
        source = """import os\n\n@decorator\nclass Service:\n    def run(self):\n        return os.getcwd()\n"""
        result = extract_snippet(source, "Service.run")
        self.assertFalse(result["fallback"])
        self.assertIn("import os", result["text"])
        self.assertIn("@decorator", result["text"])
        self.assertIn("class Service", result["text"])
        self.assertEqual(result["start_line"], 1)
        self.assertEqual(result["end_line"], 6)

    def test_malformed_or_empty_match_falls_back_to_complete_source(self):
        source = "def broken(:\n"
        result = extract_snippet(source, "missing")
        self.assertTrue(result["fallback"])
        self.assertEqual(result["text"], source)

    def test_duplicate_occurrence_selects_requested_range(self):
        source = "def same():\n    return 1\n\ndef same():\n    return 2\n"
        result = extract_snippet(source, "same", occurrence_start=4)
        self.assertEqual(result["start_line"], 4)
        self.assertIn("return 2", result["text"])

    def test_import_after_definition_uses_complete_file_fallback(self):
        source = "def worker():\n    return 1\n\nimport later\n"
        result = extract_snippet(source, "worker")
        self.assertTrue(result["fallback"])
        self.assertEqual(result["text"], source)

    def test_metadata_hash_and_boundaries_are_exact(self):
        source = "def helper():\n    return 1\n"
        result = extract_snippet(source, "helper")
        self.assertEqual(result["start_line"], 1)
        self.assertEqual(result["end_line"], 2)
        self.assertEqual(result["sha256"], hashlib.sha256(result["text"].encode()).hexdigest())

    def test_replaced_file_is_omitted_before_symbol_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "module.py"
            path.write_text("def safe():\n    return 1\n", encoding="utf-8")
            metadata = path.stat()
            expected = hashlib.sha256(path.read_bytes()).hexdigest()
            path.write_text("def replaced():\n    return 'outside-secret'\n", encoding="utf-8")
            policy = json.loads((Path(__file__).resolve().parents[1] /
                                 "harness/context-policy.json").read_text(encoding="utf-8"))
            self.assertIsNone(stable_source(path, root, policy, metadata, expected))

    def test_oversized_explicit_source_is_not_parsed_for_symbols(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "large.py"
            path.write_bytes(b"x" * 256)
            policy = {"max_file_bytes": 32, "always_include": [],
                      "exclude_dirs": [], "exclude_globs": []}
            metadata = path.stat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertIsNone(stable_source(path, root, policy, metadata, digest))

    def test_compiler_emits_bounded_auditable_snippets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            module = root / "service.py"
            module.write_text("def service():\n    return 1\n", encoding="utf-8")
            policy = json.loads((Path(__file__).resolve().parents[1] /
                                 "harness/context-policy.json").read_text(encoding="utf-8"))
            policy.update(always_include=[], graph_backend="lexical", max_total_tokens_estimate=1000)
            with patch.object(context_compiler, "ROOT", root), \
                 patch.object(context_graph, "ROOT", root), \
                 patch.object(context_compiler, "load_json", lambda _: copy.deepcopy(policy)), \
                 patch.object(context_graph, "load_json", lambda _: copy.deepcopy(policy)):
                result = build({"id": "T-symbol-e2e", "description": "service", "files": ["service.py"]})
            self.assertEqual(len(result["snippets"]), 1)
            snippet = result["snippets"][0]
            self.assertEqual(snippet["symbol"], "service")
            self.assertEqual(snippet["start_line"], 1)
            self.assertEqual(snippet["end_line"], 2)
            self.assertEqual(len(snippet["sha256"]), 64)
            self.assertIn("estimated_tokens", snippet)
            self.assertIn("reason", snippet)
            self.assertIsInstance(snippet["start_line"], int)
            self.assertIsInstance(snippet["end_line"], int)
            self.assertIsInstance(snippet["fallback"], bool)
            self.assertEqual(snippet["reason"], "symbol")
            self.assertLessEqual(result["estimated_tokens"], result["limits"]["estimated_tokens"])
            for key in ("path", "symbol", "start_line", "end_line", "sha256", "estimated_tokens", "reason", "fallback"):
                self.assertIn(key, snippet)

    def test_compiler_symbol_order_and_metadata_are_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "z.py").write_text("def zed():\n    return 1\n", encoding="utf-8")
            (root / "a.py").write_text("def alpha():\n    return 2\n", encoding="utf-8")
            policy = json.loads((Path(__file__).resolve().parents[1] /
                                 "harness/context-policy.json").read_text(encoding="utf-8"))
            policy.update(always_include=[], graph_backend="lexical", max_total_tokens_estimate=1000)
            task = {"id": "T-symbol-order", "description": "alpha zed", "files": ["z.py", "a.py"]}
            with patch.object(context_compiler, "ROOT", root), patch.object(context_graph, "ROOT", root), \
                 patch.object(context_compiler, "load_json", lambda _: copy.deepcopy(policy)), \
                 patch.object(context_graph, "load_json", lambda _: copy.deepcopy(policy)):
                first = build(task)
                second = build(task)
            self.assertEqual(first, second)
            self.assertEqual([item["path"] for item in first["snippets"]], ["a.py", "z.py"])
            self.assertEqual(sum(item["estimated_tokens"] for item in first["snippets"]),
                             first["estimated_tokens"] - sum(item["estimated_tokens"] for item in first["files"]))
            for item in first["snippets"]:
                self.assertGreaterEqual(item["start_line"], 1)
                self.assertGreaterEqual(item["end_line"], item["start_line"])
                self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")

    def test_control_flow_nested_symbol_is_syntax_checked_or_falls_back(self):
        source = """def outer(value):
    if value:
        def inner():
            return value
        return inner()
    return None
"""
        result = extract_snippet(source, "outer.inner")
        if not result["fallback"]:
            compile(result["text"], "<snippet>", "exec")


if __name__ == "__main__":
    unittest.main()
