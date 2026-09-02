import sys,unittest,tempfile,shutil
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import orchestrator
class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.task='T-progress'; shutil.rmtree(orchestrator.run_dir(self.task),ignore_errors=True)
        self.route={'risk':'R2','agents':['explorer','planner','implementer','reviewer','test-auditor','verifier'],'human_gate':False}
    def tearDown(self): shutil.rmtree(orchestrator.run_dir(self.task),ignore_errors=True)
    def test_progress_and_stall(self):
        s=orchestrator.init_progress(self.task,self.route); self.assertEqual(s['current_step'],'IMPLEMENT')
        s=orchestrator.record(self.task,'FAIL'); self.assertEqual(s['state'],'WAITING')
        orchestrator.resume(self.task); s=orchestrator.record(self.task,'FAIL'); self.assertEqual(s['state'],'STALLED'); self.assertIn('debugger',s['recommended_action'])
if __name__=='__main__': unittest.main()
