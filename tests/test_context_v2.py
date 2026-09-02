import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from context_compiler import build
class ContextV2Tests(unittest.TestCase):
    def test_token_budget_and_memory_field(self):
        r=build({'id':'T-cv2','description':'Update task routing','files':['scripts/task_router.py']})
        self.assertIn('estimated_tokens',r); self.assertIn('memory',r); self.assertLessEqual(r['estimated_tokens'],r['limits']['estimated_tokens'])
if __name__=='__main__': unittest.main()
