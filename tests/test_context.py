import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from context_compiler import build
class ContextTests(unittest.TestCase):
    def test_always_policy_and_excludes_secret(self):
        out=build({'id':'T-context','description':'change router behavior','files':['scripts/task_router.py']})
        paths={x['path'] for x in out['files']}
        self.assertIn('AGENTS.md',paths); self.assertIn('scripts/task_router.py',paths); self.assertNotIn('.env',paths)
        self.assertLessEqual(out['total_bytes'],out['limits']['bytes'])
if __name__=='__main__': unittest.main()
