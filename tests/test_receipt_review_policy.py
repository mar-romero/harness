import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("receipt_review", ROOT / "scripts" / "receipt_review.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ReceiptAssessmentTests(unittest.TestCase):
    def cfg(self):
        return {
            "passive_extensions": [".md", ".txt"],
            "passive_prefixes": ["docs/", "planning/"],
            "high_path_fragments": ["/auth/", "/security/", "/migrations/"],
            "high_changed_paths": 20,
            "high_changed_lines": 500,
        }

    def test_docs_are_passive(self):
        snap = {"files": [{"path": "docs/a.md", "mode": "100644"}], "changed_paths": 1, "changed_lines": 3}
        risk, _ = mod.classify_snapshot(snap, {"risk": "R0"}, self.cfg())
        self.assertEqual(risk, "passive")

    def test_auth_is_high(self):
        snap = {"files": [{"path": "src/auth/session.py", "mode": "100644"}], "changed_paths": 1, "changed_lines": 20}
        risk, _ = mod.classify_snapshot(snap, {"risk": "R1"}, self.cfg())
        self.assertEqual(risk, "high")

    def test_r2_is_never_lowered(self):
        snap = {"files": [{"path": "docs/a.md", "mode": "100644"}], "changed_paths": 1, "changed_lines": 1}
        risk, _ = mod.classify_snapshot(snap, {"risk": "R2"}, self.cfg())
        self.assertEqual(risk, "high")

    def test_normal_code_is_medium(self):
        snap = {"files": [{"path": "src/calc.py", "mode": "100644"}], "changed_paths": 1, "changed_lines": 10}
        risk, _ = mod.classify_snapshot(snap, {"risk": "R1"}, self.cfg())
        self.assertEqual(risk, "medium")


if __name__ == "__main__":
    unittest.main()
