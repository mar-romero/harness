import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from symbol_index import index_source
from snippet_extractor import extract_snippet


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

    def test_metadata_hash_and_boundaries_are_exact(self):
        source = "def helper():\n    return 1\n"
        result = extract_snippet(source, "helper")
        self.assertEqual(result["start_line"], 1)
        self.assertEqual(result["end_line"], 2)
        self.assertEqual(result["sha256"], hashlib.sha256(result["text"].encode()).hexdigest())


if __name__ == "__main__":
    unittest.main()
