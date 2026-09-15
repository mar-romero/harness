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
        valid = "def present():\n    return 1\n"
        no_match = extract_snippet(valid, "missing")
        self.assertTrue(no_match["fallback"])
        self.assertEqual(no_match["text"], valid)

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

    def test_control_flow_imports_before_and_after_definition_are_safe(self):
        before = "if True:\n    import before\n\ndef worker():\n    return before.value\n"
        after = "def worker():\n    return later.value\n\nif True:\n    import later\n"
        self.assertTrue(extract_snippet(before, "worker")["fallback"])
        self.assertTrue(extract_snippet(after, "worker")["fallback"])

    def test_compiler_total_budget_exact_fit_and_one_over(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = "def worker():\n    return 1\n"
            (root / "module.py").write_text(source, encoding="utf-8")
            policy = json.loads((Path(__file__).resolve().parents[1] / "harness/context-policy.json").read_text())
            policy.update(always_include=[], graph_backend="lexical")
            actual_source = (root / "module.py").read_bytes().decode("utf-8")
            file_tokens = max(1, ((root / "module.py").stat().st_size + 3) // 4)
            snippet_tokens = max(1, (len(actual_source) + 3) // 4)
            task = {"id": "T-budget-symbol", "description": "worker", "files": ["module.py"]}
            def run(limit):
                p = copy.deepcopy(policy); p["max_total_tokens_estimate"] = limit
                with patch.object(context_compiler, "ROOT", root), patch.object(context_graph, "ROOT", root), \
                     patch.object(context_compiler, "load_json", lambda _: copy.deepcopy(p)), patch.object(context_graph, "load_json", lambda _: copy.deepcopy(p)):
                    return build(task)
            self.assertTrue(run(file_tokens + snippet_tokens)["snippets"])
            self.assertFalse(run(file_tokens + snippet_tokens - 1)["snippets"])

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

    def test_compiler_metadata_is_exact_and_schema_shape_is_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = "def alpha():\n    return 2\n\ndef beta():\n    return 3\n"
            (root / "module.py").write_text(source, encoding="utf-8")
            source = (root / "module.py").read_bytes().decode("utf-8")
            policy = json.loads((Path(__file__).resolve().parents[1] / "harness/context-policy.json").read_text())
            policy.update(always_include=[], graph_backend="lexical", max_total_tokens_estimate=1000)
            with patch.object(context_compiler, "ROOT", root), patch.object(context_graph, "ROOT", root), \
                 patch.object(context_compiler, "load_json", lambda _: copy.deepcopy(policy)), patch.object(context_graph, "load_json", lambda _: copy.deepcopy(policy)):
                result = build({"id": "T-meta", "description": "alpha beta", "files": ["module.py"]})
            self.assertEqual(len(result["snippets"]), 2)
            for item in result["snippets"]:
                extracted = extract_snippet(source, item["symbol"], occurrence_start=item["start_line"])
                raw = extracted["text"].encode()
                self.assertEqual(item["sha256"], hashlib.sha256(raw).hexdigest(), (item, extracted))
                self.assertEqual(item["estimated_tokens"], max(1, (len(raw) + 3) // 4))
                self.assertEqual(set(item), {"path", "symbol", "start_line", "end_line", "sha256", "estimated_tokens", "reason", "fallback"})
                self.assertEqual(item["reason"], "symbol")
                self.assertIs(item["fallback"], False)
            bad = dict(result["snippets"][0]); bad.pop("sha256")
            self.assertNotEqual(set(bad), {"path", "symbol", "start_line", "end_line", "sha256", "estimated_tokens", "reason", "fallback"})

    def test_exact_token_boundary_accepts_fit_and_rejects_one_over(self):
        source = "def alpha():\n    return 2\n"
        extracted = extract_snippet(source, "alpha")
        size = len(extracted["text"])
        self.assertFalse(extract_snippet(source, "alpha", max_chars=size)["fallback"])
        self.assertTrue(extract_snippet(source, "alpha", max_chars=size - 1)["fallback"])

    def test_nonexplicit_graph_neighbor_gets_symbols_without_name_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "seed.py").write_text("def seed():\n    return 1\n", encoding="utf-8")
            (root / "neighbor.py").write_text("def unrelated():\n    return 2\n", encoding="utf-8")
            policy = json.loads((Path(__file__).resolve().parents[1] / "harness/context-policy.json").read_text())
            policy.update(always_include=[], graph_backend="lexical", graph_neighbor_depth=1, graph_max_results=10)
            document = {"backend": {"requested": "lexical", "selected": "lexical", "fallback_reason": None},
                        "edges": [{"source": "seed.py", "target": "neighbor.py", "kind": "import"}]}
            with patch.object(context_compiler, "ROOT", root), patch.object(context_graph, "ROOT", root), \
                 patch.object(context_compiler, "load_json", lambda _: copy.deepcopy(policy)), patch.object(context_graph, "load_json", lambda _: copy.deepcopy(policy)), \
                 patch.object(context_compiler, "build_graph_document", return_value=document):
                result = build({"id": "T-graph", "description": "seed", "files": ["seed.py"]})
            self.assertEqual([item["symbol"] for item in result["snippets"] if item["path"] == "neighbor.py"], ["unrelated"])

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
