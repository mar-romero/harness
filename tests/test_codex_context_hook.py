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
            audit = root / ".harness" / "overlays" / "fixture" / "codex" / "permission-audit.jsonl"
            session = audit.with_name("session.json")
            with patch.object(codex_context_hook, "read_provider_active", return_value=None), \
                 patch.object(codex_context_hook, "provider_audit_path", return_value=audit), \
                 patch.object(codex_context_hook, "provider_session_path", return_value=session):
                codex_context_hook.audit_session_boundary({
                    "hook_event_name": "SessionStart", "session_id": "session-test",
                })
            self.assertTrue(audit.is_file())
            record = json.loads(audit.read_text(encoding="utf-8").strip())
            self.assertEqual(record["event"], "SessionStart")
            self.assertEqual(record["decision"], "session_boundary")
            self.assertEqual(record["session_id"], "session-test")

    def test_session_start_never_refreshes_or_rewrites_agent_adapters(self):
        from providers import codex_activate_task

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / ".harness" / "runs" / "TASK-test"
            run.mkdir(parents=True)
            for name in ("route.json", "context.json", "progress.json", "task.json"):
                (run / name).write_text("{}", encoding="utf-8")
            active = {
                "provider": "codex", "overlay": {"schema_version": 1, "worktree_id": "fixture"},
                "task_id": "TASK-test", "risk": "R1",
                "route_path": ".harness/runs/TASK-test/route.json",
                "context_path": ".harness/runs/TASK-test/context.json",
                "progress_path": ".harness/runs/TASK-test/progress.json",
                "task_snapshot_path": ".harness/runs/TASK-test/task.json",
            }
            payload = json.dumps({"hook_event_name": "SessionStart", "session_id": "session-test"})
            with patch.object(codex_context_hook, "read_provider_active", return_value=active), \
                 patch.object(codex_context_hook, "provider_audit_path", return_value=root / "audit.jsonl"), \
                 patch.object(codex_context_hook, "provider_session_path", return_value=root / "session.json"), \
                 patch.object(codex_context_hook, "runtime_reference", side_effect=lambda value: root / value), \
                 patch.object(sys, "stdin", io.StringIO(payload)), \
                 patch.object(codex_activate_task, "refresh_active") as refresh, \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(codex_context_hook.main(), 0)
            refresh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
