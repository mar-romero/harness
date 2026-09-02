import sys,unittest,tempfile,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'scripts'))
from runtime_eval import run_suite
class RuntimeEvalTests(unittest.TestCase):
    def test_repeated_metrics(self):
        with tempfile.TemporaryDirectory() as td:
            s=Path(td)/'suite.json'; s.write_text(json.dumps({'suite':'x','cases':[{'id':'C1','holdout':True}]}))
            r=run_suite(str(s),['python3','tests/fixtures/fake_runtime_adapter.py'],3,10,'fake','fake','implementer')
            self.assertEqual(r['summary']['rollouts'],3); self.assertEqual(r['summary']['pass_power_k'],1.0); self.assertEqual(r['harness_check']['returncode'],0)
if __name__=='__main__': unittest.main()
