import json
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout

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

    def test_session_start_never_refreshes_or_rewrites_agent_adapters(self):
        """Lifecycle hooks must be read-only; activation owns adapter refreshes."""
        from providers import codex_activate_task

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / ".harness" / "runs" / "TASK-test"
            run.mkdir(parents=True)
            for name in ("route.json", "context.json", "progress.json", "task.json"):
                (run / name).write_text("{}", encoding="utf-8")
            binding = root / ".harness" / "codex"
            binding.mkdir(parents=True)
            (binding / "active-task.json").write_text(json.dumps({
                "task_id": "TASK-test", "risk": "R1",
                "route_path": ".harness/runs/TASK-test/route.json",
                "context_path": ".harness/runs/TASK-test/context.json",
                "progress_path": ".harness/runs/TASK-test/progress.json",
                "task_snapshot_path": ".harness/runs/TASK-test/task.json",
            }), encoding="utf-8")
            payload = json.dumps({"hook_event_name": "SessionStart", "session_id": "session-test"})
            with patch.object(codex_context_hook, "ROOT", root), \
                 patch.object(sys, "stdin", io.StringIO(payload)), \
                 patch.object(codex_activate_task, "refresh_active") as refresh, \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(codex_context_hook.main(), 0)
            refresh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
