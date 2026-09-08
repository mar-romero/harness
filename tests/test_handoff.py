import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from handoff import validate

class HandoffTests(unittest.TestCase):
    def test_valid_plan(self):
        d={'task_id':'T-plan','producer':'planner','status':'PASS','steps':['x'],'acceptance_criteria':['y'],'risks':[],'rollback':[],'evidence_refs':[]}
        self.assertTrue(validate('planner',d))
    def test_wrong_producer(self):
        d={'task_id':'T-plan','producer':'implementer','status':'PASS','steps':['x'],'acceptance_criteria':['y'],'risks':[],'rollback':[],'evidence_refs':[]}
        with self.assertRaises(Exception): validate('planner',d)
    def test_test_designer_contract_exists(self):
        d={'task_id':'T-design','producer':'test-designer','status':'PASS','tdd_mode':'tdd_required','behaviors':['b'],'oracle':['o'],'cases':['c'],'assumptions':[],'unresolved':[],'evidence_refs':[]}
        self.assertTrue(validate('test-designer',d))
    def test_test_auditor_contract_exists(self):
        d={'task_id':'T-audit','producer':'test-auditor','status':'PASS','tests_reviewed':['tests/x.py'],'findings':[],'gaps':[],'evidence_refs':[]}
        self.assertTrue(validate('test-auditor',d))
    def test_security_reviewer_contract_exists(self):
        d={'task_id':'T-sec','producer':'security-reviewer','status':'PASS','trust_boundaries':['input'],'findings':[],'residual_risks':[],'evidence_refs':[]}
        self.assertTrue(validate('security-reviewer',d))
if __name__=='__main__': unittest.main()
