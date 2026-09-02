import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from task_router import route
class RouterTests(unittest.TestCase):
    def test_docs_r0(self):
        r=route({'id':'T-doc','description':'Fix README documentation typo','files':['README.md']})
        self.assertEqual(r['risk'],'R0'); self.assertNotIn('reviewer',r['agents'])
    def test_migration_r2(self):
        r=route({'id':'T-db','description':'Add database schema migration for invoices'})
        self.assertEqual(r['risk'],'R2'); self.assertIn('verifier',r['agents']); self.assertIn('test-auditor',r['agents'])
    def test_auth_r3(self):
        r=route({'id':'T-auth','description':'Change authentication token authorization in production'})
        self.assertEqual(r['risk'],'R3'); self.assertTrue(r['human_gate']); self.assertIn('security-reviewer',r['agents'])
if __name__=='__main__': unittest.main()
