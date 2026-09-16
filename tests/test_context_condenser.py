import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from context_condenser import condense_handoffs, estimated_tokens


class ContextCondenserTests(unittest.TestCase):
    def test_condenses_large_handoffs_but_preserves_control_fields(self):
        payload = {
            "explorer": {
                "task_id": "T", "producer": "explorer", "status": "PASS",
                "facts": ["x" * 5000 for _ in range(30)],
                "relevant_files": [f"src/f{i}.py" for i in range(40)],
                "evidence_refs": [f"ref-{i}" for i in range(30)],
            },
            "planner": {
                "task_id": "T", "producer": "planner", "status": "PASS",
                "steps": ["y" * 3000 for _ in range(20)],
                "acceptance_criteria": ["criterion" for _ in range(20)],
                "evidence_refs": ["plan-ref"],
            },
        }
        out = condense_handoffs(payload)
        rendered = json.dumps(out, ensure_ascii=False)
        self.assertLess(len(rendered), 25000)
        self.assertEqual(out["explorer"]["status"], "PASS")
        self.assertIn("relevant_files", out["explorer"])
        self.assertGreater(estimated_tokens(out), 0)


if __name__ == "__main__": unittest.main()
