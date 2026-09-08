import sys
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import task_checks


class TaskChecksRunnerTests(unittest.TestCase):
    def test_safe_env_disables_python_bytecode(self):
        with tempfile.TemporaryDirectory() as td:
            env = task_checks._safe_env(Path(td))
        self.assertEqual(env['PYTHONDONTWRITEBYTECODE'], '1')

    def test_syntax_check_does_not_create_pycache(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / 'src'
            src.mkdir()
            (src / 'good.py').write_text('VALUE = 1\n', encoding='utf-8')
            result = task_checks._python_syntax_check(src)
            self.assertEqual(result['status'], 'PASS')
            self.assertEqual(result['files_checked'], 1)
            self.assertFalse(any(src.rglob('__pycache__')))

    def test_codex_binding_uses_immutable_snapshot(self):
        with tempfile.TemporaryDirectory() as td, patch.object(task_checks, 'ROOT', Path(td)):
            root = Path(td)
            snapshot = root / '.harness/runs/T/task.json'
            snapshot.parent.mkdir(parents=True)
            snapshot.write_text(json.dumps({'id': 'T', 'files': ['src/a.py']}), encoding='utf-8')
            active = root / '.harness/codex/active-task.json'
            active.parent.mkdir(parents=True)
            active.write_text(json.dumps({
                'task_id': 'T',
                'task_snapshot_path': '.harness/runs/T/task.json',
            }), encoding='utf-8')
            task, path = task_checks._load_active_task('T')
        self.assertEqual(task['id'], 'T')
        self.assertEqual(path.name, 'task.json')

    def test_runtime_consistency_rejects_stale_agent_budget_risk(self):
        with tempfile.TemporaryDirectory() as td, patch.object(task_checks, 'ROOT', Path(td)), \
             patch.object(task_checks, 'run_dir', return_value=Path(td) / '.harness/runs/T'):
            run = Path(td) / '.harness/runs/T'
            run.mkdir(parents=True)
            (run / 'agent-budget.json').write_text(json.dumps({'risk': 'R2'}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'agent budget risk R2 != route risk R3'):
                task_checks._validate_runtime_consistency('T', {'risk': 'R3'})


if __name__ == '__main__':
    unittest.main()
