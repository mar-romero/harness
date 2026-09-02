import sys,unittest,tempfile,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from handoff import validate
class HandoffTests(unittest.TestCase):
    def test_valid_plan(self):
        d={'task_id':'T-plan','producer':'planner','status':'PASS','steps':['x'],'acceptance_criteria':['y'],'risks':[],'rollback':[],'evidence_refs':[]}; self.assertTrue(validate('planner',d))
    def test_wrong_producer(self):
        d={'task_id':'T-plan','producer':'implementer','status':'PASS','steps':['x'],'acceptance_criteria':['y'],'risks':[],'rollback':[],'evidence_refs':[]}
        with self.assertRaises(Exception): validate('planner',d)
if __name__=='__main__': unittest.main()
