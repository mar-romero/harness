import sys
import tempfile
import unittest
from pathlib import Path

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


if __name__ == '__main__':
    unittest.main()
