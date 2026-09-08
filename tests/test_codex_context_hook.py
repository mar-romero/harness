import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import codex_context_hook


class CodexContextHookTests(unittest.TestCase):
    def test_session_boundary_creates_auditable_jsonl_record_without_active_task(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(codex_context_hook, "ROOT", root):
                codex_context_hook.audit_session_boundary({
                    "hook_event_name": "SessionStart", "session_id": "session-test",
                })
            audit = root / ".harness" / "codex" / "permission-audit.jsonl"
            self.assertTrue(audit.is_file())
            record = json.loads(audit.read_text(encoding="utf-8").strip())
            self.assertEqual(record["event"], "SessionStart")
            self.assertEqual(record["decision"], "session_boundary")
            self.assertEqual(record["session_id"], "session-test")


if __name__ == "__main__":
    unittest.main()
